from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from ..admin_explanations import AdminExplanation
from ..auth import require_admin
from ..config import Settings, get_settings
from ..database import get_session
from ..integrations.feie import FeieClient, FeieError, configuration_read, configured_region, validate_live_config
from ..models import AdminAudit, Order, OrderItem, PrintJob, User
from ..manual_order_printing import (current_fulfillment_status, current_manual_snapshot, load_manual_print_order,
                                    manual_print_blocked_reason, manual_print_order_blocked_reason, order_print_jobs, order_print_preview_token)
from ..printer_worker import wake_printer
from ..printing import (format_receipt, fresh_receipt_snapshot, job_matches_printer, job_read, job_region,
                        open_receipt_content, payment_snapshot_from_session, printer_fingerprint, receipt_preview,
                        render_receipt_snapshot)

printers_router = APIRouter(prefix="/v1/admin/printers", tags=["printers"])


def _require_order_job(job: PrintJob) -> None:
    if job.kind == "summary":
        raise HTTPException(409, "這是訂單彙總單，請回訂單彙整查看此批列印狀態")


@printers_router.get("/configuration")
async def printer_configuration(_admin: User = Depends(require_admin), settings: Settings = Depends(get_settings)):
    return configuration_read(settings)


@printers_router.get("/status")
async def printer_status(_admin: User = Depends(require_admin), settings: Settings = Depends(get_settings)):
    result = {"enabled": settings.printer_enabled, "dry_run": settings.printer_dry_run,
              "status": "disabled", "error": None}
    if settings.printer_enabled:
        if settings.printer_dry_run:
            result["status"] = "dry_run"
        else:
            try:
                result["status"] = await FeieClient(settings).printer_status()
            except FeieError as exc:
                result.update(status="unknown", error=exc.code)
    return result


@printers_router.get("/jobs")
async def print_jobs(status: str | None = Query(default=None, pattern="^(pending|sending|submitted|printed|failed|unknown|simulated)$"),
    search: str = Query(default="", max_length=100),
    order_id: str | None = None, limit: int = Query(default=30, ge=1, le=100), offset: int = Query(default=0, ge=0),
    _admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)):
    query = select(PrintJob, Order).join(Order, Order.id == PrintJob.order_id)
    if status:
        query = query.where(PrintJob.status == status)
    if order_id:
        query = query.where(PrintJob.order_id == order_id)
    if term := search.strip():
        query = query.where(or_(
            Order.order_number.icontains(term, autoescape=True),
            Order.user.has(User.display_name.icontains(term, autoescape=True)),
            Order.items.any(OrderItem.product_name.icontains(term, autoescape=True)),
        ))
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    jobs = await session.execute(query.order_by(PrintJob.created_at.desc(), PrintJob.id.desc()).offset(offset).limit(limit))
    return {"items": [job_read(job, settings, order) for job, order in jobs], "total": total}


@printers_router.get("/jobs/{job_id}")
async def print_job(job_id: str, _admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)):
    job = await session.get(PrintJob, job_id)
    if job is None:
        raise HTTPException(404, "找不到列印工作")
    _require_order_job(job)
    order = await session.scalar(select(Order).where(Order.id == job.order_id).options(selectinload(Order.fulfillment)))
    blocked = manual_print_order_blocked_reason(order)
    try:
        if job.content is not None:
            preview = open_receipt_content(job.content, job.order_id, settings)
        else:
            snapshot = render_receipt_snapshot(job.snapshot, job.order_id, settings)
            preview = format_receipt({**snapshot, "reprint": bool(job.reprint_of_id), "dry_run": job.mode == "dry_run"}, width=settings.printer_line_width)
    except FeieError as exc:
        if exc.code != "receipt_too_long":
            return {**job_read(job, settings, order), "preview": "加密收據或聯絡資料無法解密，請確認資料金鑰後再處理。",
                    "preview_error": exc.code, "reprint_blocked_reason": blocked}
        preview = "內容超出單張 5000 bytes 上限，請依訂單人工處理。"
    return {**job_read(job, settings, order), "preview": receipt_preview(preview), "reprint_blocked_reason": blocked}


class ReprintRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    request_id: UUID
    reason: AdminExplanation = Field(default="", max_length=200)
    acknowledge_duplicate: bool = False


class ResolveRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    printed: bool
    reason: AdminExplanation = Field(default="", max_length=200)


@printers_router.post("/jobs/{job_id}/resolve")
async def resolve_job(job_id: str, body: ResolveRequest, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)):
    job = await session.scalar(select(PrintJob).where(PrintJob.id == job_id).with_for_update())
    if job is None:
        raise HTTPException(404, "找不到列印工作")
    _require_order_job(job)
    if job.mode != "live" or job.status not in {"submitted", "unknown"}:
        raise HTTPException(409, "僅能人工核對正式模式中結果待確認的工作")
    job.status = "printed" if body.printed else "unknown"
    job.printed_at = datetime.now(timezone.utc) if body.printed else None
    job.lease_until = job.lease_token = None
    job.last_error = None if body.printed else "admin_has_not_seen_paper"
    job.last_result = {"source": "admin_paper_check", "printed": body.printed}
    session.add(AdminAudit(actor_id=admin.id, action="printer.resolve", aggregate_type="order", aggregate_id=job.order_id,
        reason=body.reason, data={"print_job_id": job.id, "printed": body.printed}))
    await session.commit()
    return job_read(job, settings)


@printers_router.post("/jobs/{job_id}/reprint", status_code=201)
async def reprint_job(job_id: str, body: ReprintRequest, admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)):
    if not settings.printer_enabled:
        raise HTTPException(409, "出單功能尚未啟用")
    if not settings.printer_dry_run:
        try:
            validate_live_config(settings)
        except FeieError as exc:
            raise HTTPException(409, exc.code) from None
    source_info = (await session.execute(select(PrintJob.order_id, PrintJob.kind).where(PrintJob.id == job_id))).first()
    if source_info is None:
        raise HTTPException(404, "找不到列印工作")
    if source_info.kind == "summary":
        raise HTTPException(409, "這是訂單彙總單，請回訂單彙整查看此批列印狀態")
    order_id = source_info.order_id
    order = await session.scalar(select(Order).where(Order.id == order_id).options(selectinload(Order.fulfillment))
        .execution_options(populate_existing=True).with_for_update())
    source = await session.scalar(select(PrintJob).where(PrintJob.id == job_id).with_for_update())
    if source.kind == "reference" or source.snapshot.get("manual"):
        raise HTTPException(409, "請回到訂單頁重新預覽目前狀態後列印")
    region = configured_region(settings)
    if region is None or job_region(source) != region:
        raise HTTPException(409, "原工作與目前印表機的雲端區域不同，不能跨區補印")
    event_key = f"reprint:{body.request_id}"
    existing = await session.scalar(select(PrintJob).where(PrintJob.event_key == event_key))
    if existing:
        if existing.reprint_of_id != job_id:
            raise HTTPException(409, "補印識別碼已用於其他工作")
        return job_read(existing, settings, order)
    if source.status in {"pending", "sending"}:
        raise HTTPException(409, "工作仍待發送或正在發送，請先確認結果")
    if source.status in {"submitted", "unknown"} and not body.acknowledge_duplicate:
        raise HTTPException(409, "原單可能已列印；請先查詢並確認可重複列印")
    if blocked := manual_print_order_blocked_reason(order):
        raise HTTPException(409, blocked)
    if source.kind in {"order", "amendment"} and source.snapshot.get("revision", 1) != order.revision:
        raise HTTPException(409, "訂單已更新，請選擇最新版出單工作補印")
    in_progress = await session.scalar(select(PrintJob.id).where(PrintJob.order_id == order_id,
        PrintJob.id != source.id, PrintJob.status.in_({"pending", "sending"})).limit(1))
    if in_progress:
        raise HTTPException(409, "這筆訂單已有等待發送或正在發送的工作，請先確認結果")
    uncertain = await session.scalar(select(PrintJob.id).where(PrintJob.order_id == order_id,
        PrintJob.id != source.id, PrintJob.status.in_({"submitted", "unknown"})).limit(1))
    if uncertain and not body.acknowledge_duplicate:
        raise HTTPException(409, "同筆訂單另有結果待確認的工作，請先核對並接受可能重複列印")
    mode = "dry_run" if settings.printer_dry_run else "live"
    snapshot = {**await fresh_receipt_snapshot(session, source.snapshot, order), "printer_region": region,
                **await payment_snapshot_from_session(session, order)}
    job = PrintJob(order_id=order_id, event_key=event_key, reprint_of_id=source.id, kind=source.kind,
        mode=mode, printer_fingerprint=printer_fingerprint(settings), snapshot=snapshot)
    session.add(job)
    await session.flush()
    session.add(AdminAudit(actor_id=admin.id, action="printer.reprint", aggregate_type="order", aggregate_id=order_id,
        reason=body.reason, data={"source_job_id": source.id, "print_job_id": job.id, "acknowledge_duplicate": body.acknowledge_duplicate, "mode": mode}))
    await session.commit()
    wake_printer()
    return job_read(job, settings, order)


@printers_router.post("/jobs/{job_id}/query")
async def query_job(job_id: str, _admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)):
    job = await session.scalar(select(PrintJob).where(PrintJob.id == job_id).with_for_update())
    if job is None:
        raise HTTPException(404, "找不到列印工作")
    _require_order_job(job)
    if job.status not in {"submitted", "unknown"} or not job.vendor_order_id:
        raise HTTPException(409, "此工作沒有可查詢的雲端工作編號；請核對紙本結果")
    if not job_matches_printer(job, settings):
        raise HTTPException(409, "工作與目前出單模式、設備或雲端區域不同，請切回原設定再查詢")
    job.next_attempt_at = datetime.now(timezone.utc)
    await session.commit()
    wake_printer()
    return job_read(job, settings)


class OrderPrintRequest(ReprintRequest):
    preview_token: str = Field(pattern=r"^[0-9a-f]{64}$")


def _duplicate_risk(jobs: list[PrintJob]) -> bool:
    return any(job.status in {"submitted", "unknown"} for job in jobs)


@printers_router.get("/orders/{order_id}/preview")
async def preview_order_print(order_id: str, _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)):
    order = await load_manual_print_order(session, order_id)
    jobs = await order_print_jobs(session, order_id)
    blocked = manual_print_blocked_reason(order, jobs, settings)
    try:
        snapshot = await current_manual_snapshot(session, order, settings)
        token = order_print_preview_token(snapshot, order, settings)
        content = format_receipt({**render_receipt_snapshot(snapshot, order.id, settings),
            "dry_run": settings.printer_dry_run}, width=settings.printer_line_width)
    except FeieError as exc:
        message = "訂單內容超出單張上限，請由管理員另外處理" if exc.code == "receipt_too_long" else "聯絡資料加密設定無法使用，請先確認伺服器資料金鑰"
        raise HTTPException(409, message) from None
    return {"order_id": order.id, "order_number": order.order_number, "preview": receipt_preview(content),
        "preview_token": token, "kind": snapshot["kind"], "revision": order.revision,
        "payment_status": order.payment_status, "fulfillment_status": current_fulfillment_status(order),
        "duplicate_risk": _duplicate_risk(jobs), "can_print": blocked is None, "blocked_reason": blocked,
        "mode": "dry_run" if settings.printer_dry_run else "live",
        "jobs": [job_read(job, settings, order) for job in jobs]}


@printers_router.get("/orders/{order_id}/print-requests/{request_id}")
async def find_order_print(order_id: str, request_id: UUID, _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)):
    order = await load_manual_print_order(session, order_id)
    job = await session.scalar(select(PrintJob).where(
        PrintJob.event_key == f"manual-order:{request_id}", PrintJob.order_id == order.id))
    if job is None:
        raise HTTPException(404, "尚未找到這筆列印要求")
    return job_read(job, settings, order)


@printers_router.post("/orders/{order_id}/print", status_code=201)
async def create_order_print(order_id: str, body: OrderPrintRequest, admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)):
    order = await load_manual_print_order(session, order_id, lock=True)
    event_key = f"manual-order:{body.request_id}"
    existing = await session.scalar(select(PrintJob).where(PrintJob.event_key == event_key))
    if existing:
        if existing.order_id != order_id:
            raise HTTPException(409, "列印識別碼已用於另一筆訂單")
        return job_read(existing, settings, order)
    jobs = await order_print_jobs(session, order_id)
    if blocked := manual_print_blocked_reason(order, jobs, settings):
        raise HTTPException(409, blocked)
    if _duplicate_risk(jobs) and not body.acknowledge_duplicate:
        raise HTTPException(409, "原單可能已列印，請先核對並接受可能重複列印")
    try:
        snapshot = await current_manual_snapshot(session, order, settings)
        if order_print_preview_token(snapshot, order, settings) != body.preview_token:
            raise HTTPException(409, "訂單資料、狀態或出單設備已變更，請重新預覽")
        format_receipt(render_receipt_snapshot(snapshot, order.id, settings), width=settings.printer_line_width)
    except FeieError as exc:
        message = "訂單內容超出單張上限，請由管理員另外處理" if exc.code == "receipt_too_long" else "聯絡資料加密設定無法使用，請先確認伺服器資料金鑰"
        raise HTTPException(409, message) from None
    job = PrintJob(order_id=order.id, event_key=event_key, kind=snapshot["kind"], snapshot=snapshot,
        mode="dry_run" if settings.printer_dry_run else "live", printer_fingerprint=printer_fingerprint(settings))
    session.add(job)
    try:
        await session.flush()
        session.add(AdminAudit(actor_id=admin.id, action="printer.order.print", aggregate_type="order",
            aggregate_id=order.id, reason=body.reason, data={"print_job_id": job.id, "request_id": str(body.request_id),
                "kind": job.kind, "revision": order.revision, "payment_status": order.payment_status.value,
                "fulfillment_status": current_fulfillment_status(order),
                "acknowledge_duplicate": body.acknowledge_duplicate, "mode": job.mode}))
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await session.scalar(select(PrintJob).where(PrintJob.event_key == event_key))
        if existing is None:
            raise
        if existing.order_id != order_id:
            raise HTTPException(409, "列印識別碼已用於另一筆訂單") from None
        return job_read(existing, settings)
    wake_printer()
    return job_read(job, settings, order)
