from typing import Annotated
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..admin_explanations import AdminExplanation
from ..auth import require_admin
from ..config import Settings, get_settings
from ..database import get_session
from ..integrations.feie import FeieError, configured_region, validate_live_config
from ..models import AdminAudit, PrintJob, User
from ..order_summary import OrderSummaryFilters, build_print_summary, read_order_summary
from ..printer_worker import wake_printer
from ..printing import printer_fingerprint, receipt_preview, seal_receipt_content
from ..summary_printing import (SUMMARY_FORMAT_VERSION, format_summary_pages, normalized_print_filters, read_summary_jobs,
                               summary_batch_read, summary_fingerprint, summary_generated_at)


order_summary_router = APIRouter(prefix="/v1/admin", tags=["order-summary"])


@order_summary_router.get("/order-summary")
async def get_order_summary(
    filters: Annotated[OrderSummaryFilters, Query()],
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    return await read_order_summary(session, filters)


def _print_pages(report: dict, settings: Settings, request_id: str) -> list[str]:
    if not report["summary"]["order_count"]:
        raise HTTPException(422, "沒有可列印的已付款訂單")
    try:
        return format_summary_pages(report, summary_generated_at(), request_id,
            width=settings.printer_line_width, dry_run=settings.printer_dry_run)
    except FeieError as exc:
        detail = "彙總超過 50 張，請縮小查詢期間或品項範圍" if exc.code == "summary_too_many_pages" else "彙總內容超過單張上限，請縮小查詢範圍"
        raise HTTPException(422, detail) from None


@order_summary_router.get("/order-summary/print-preview")
async def print_summary_preview(
    filters: Annotated[OrderSummaryFilters, Query()],
    _admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    report = await build_print_summary(session, filters)
    pages = _print_pages(report, settings, "00000000-0000-0000-0000-000000000000")
    return {"fingerprint": summary_fingerprint(report, settings), "summary": report["summary"],
            "pages": [receipt_preview(page) for page in pages], "page_count": len(pages),
            "filters": report["filters"], "mode": "dry_run" if settings.printer_dry_run else "live"}


class SummaryPrintRequest(BaseModel):
    filters: OrderSummaryFilters
    request_id: UUID
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


def _existing_batch(jobs: list[PrintJob], filters: dict) -> dict:
    if jobs[0].snapshot["filters"] != filters:
        raise HTTPException(409, "列印識別碼已用於其他查詢條件，請重新預覽")
    return summary_batch_read(jobs)


@order_summary_router.post("/order-summary/print-jobs", status_code=201)
async def create_summary_print_jobs(
    body: SummaryPrintRequest, admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings),
):
    request_id, filters = str(body.request_id), normalized_print_filters(body.filters)
    existing = await read_summary_jobs(session, request_id)
    if existing:
        return _existing_batch(existing, filters)
    if not settings.printer_enabled:
        raise HTTPException(409, "出單功能尚未啟用")
    if not settings.printer_dry_run:
        try:
            validate_live_config(settings)
        except FeieError as exc:
            raise HTTPException(409, exc.code) from None
    report = await build_print_summary(session, body.filters)
    fingerprint = summary_fingerprint(report, settings)
    if fingerprint != body.fingerprint:
        raise HTTPException(409, "訂單資料或出單設備已變更，請重新預覽後列印")
    pages = _print_pages(report, settings, request_id)
    mode = "dry_run" if settings.printer_dry_run else "live"
    jobs = []
    for page, content in enumerate(pages, 1):
        job_id = str(uuid4())
        try:
            sealed = seal_receipt_content(content, {}, f"summary:{job_id}", settings, force=True)
        except FeieError:
            raise HTTPException(409, "列印資料加密設定尚未完成，請先確認伺服器設定") from None
        jobs.append(PrintJob(id=job_id, kind="summary", order_id=None,
            event_key=f"summary:{request_id}:{page:02}", mode=mode,
            printer_fingerprint=printer_fingerprint(settings), content=sealed,
            snapshot={"kind": "summary", "request_id": request_id, "page": page, "page_count": len(pages),
                      "format_version": SUMMARY_FORMAT_VERSION,
                      "filters": report["filters"], "summary": report["summary"], "fingerprint": fingerprint,
                      "source_versions": report["source_versions"],
                      "printer_region": configured_region(settings)}))
    session.add_all(jobs)
    session.add(AdminAudit(actor_id=admin.id, action="printer.summary", aggregate_type="print_summary",
        aggregate_id=request_id, data={"filters": filters, "summary": report["summary"], "page_count": len(pages),
                                       "fingerprint": fingerprint, "mode": mode}))
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await read_summary_jobs(session, request_id)
        if not existing:
            raise
        return _existing_batch(existing, filters)
    wake_printer()
    return summary_batch_read(jobs)


@order_summary_router.get("/order-summary/print-jobs")
async def list_summary_print_jobs(
    limit: int = Query(default=10, ge=1, le=30), _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    request_id = PrintJob.snapshot["request_id"].as_string()
    batches = list(await session.scalars(select(request_id).where(PrintJob.kind == "summary")
        .group_by(request_id).order_by(func.min(PrintJob.created_at).desc(), request_id.desc()).limit(limit)))
    if not batches:
        return {"items": []}
    jobs = list(await session.scalars(select(PrintJob).where(PrintJob.kind == "summary", request_id.in_(batches))))
    groups = {batch: [] for batch in batches}
    for job in jobs:
        groups[job.snapshot["request_id"]].append(job)
    return {"items": [summary_batch_read(groups[batch]) for batch in batches]}


@order_summary_router.get("/order-summary/print-jobs/{request_id}")
async def get_summary_print_jobs(
    request_id: UUID, _admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
):
    jobs = await read_summary_jobs(session, str(request_id))
    if not jobs:
        raise HTTPException(404, "找不到彙總列印工作")
    return summary_batch_read(jobs)


class SummaryResolveRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    printed: bool
    reason: AdminExplanation = Field(default="", max_length=200)


@order_summary_router.post("/order-summary/print-jobs/{request_id}/pages/{job_id}/resolve")
async def resolve_summary_page(
    request_id: UUID, job_id: UUID, body: SummaryResolveRequest,
    admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
):
    job = await session.scalar(select(PrintJob).where(PrintJob.id == str(job_id)).with_for_update())
    if job is None or job.kind != "summary" or job.snapshot.get("request_id") != str(request_id):
        raise HTTPException(404, "找不到此批彙總列印工作")
    if job.mode != "live" or job.status not in {"submitted", "unknown"}:
        raise HTTPException(409, "僅能人工核對正式模式中結果待確認的工作")
    job.status = "printed" if body.printed else "unknown"
    job.printed_at = datetime.now(timezone.utc) if body.printed else None
    job.lease_token = job.lease_until = None
    job.last_error = None if body.printed else "admin_has_not_seen_paper"
    job.last_result = {"source": "admin_paper_check", "printed": body.printed}
    session.add(AdminAudit(actor_id=admin.id, action="printer.summary.resolve", aggregate_type="print_summary",
        aggregate_id=str(request_id), reason=body.reason,
        data={"print_job_id": job.id, "page": job.snapshot["page"], "printed": body.printed}))
    await session.commit()
    return summary_batch_read(await read_summary_jobs(session, str(request_id)))
