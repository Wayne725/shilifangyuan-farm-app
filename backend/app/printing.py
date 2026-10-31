from __future__ import annotations

import hashlib
import unicodedata
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .integrations.feie import FeieError, configured_region
from .models import Order, PaymentMethod, PrintJob, User


def printer_fingerprint(settings: Settings) -> str:
    identity = "|".join((settings.feie_api_base_url, settings.feie_user.get_secret_value(), settings.feie_printer_sn.get_secret_value()))
    return hashlib.sha256(identity.encode()).hexdigest()


def job_region(job: PrintJob) -> str | None:
    region = job.snapshot.get("printer_region", "jp")
    return region if isinstance(region, str) and region in {"jp", "cn"} else None


def job_matches_printer(job: PrintJob, settings: Settings) -> bool:
    mode = "dry_run" if settings.printer_dry_run else "live"
    region = configured_region(settings)
    return (region is not None and job.mode == mode and job.printer_fingerprint == printer_fingerprint(settings)
            and job_region(job) == region)


def clean_text(value: object) -> str:
    value = str(value or "").translate(str.maketrans({"<": "＜", ">": "＞", "&": "＆"}))
    return "".join(char for char in value if char == "\n" or not unicodedata.category(char).startswith("C"))


def wrap_text(value: object, width: int) -> list[str]:
    lines, current, length = [], "", 0
    for char in clean_text(value):
        size = 0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in {"W", "F", "A"} else 1
        if char == "\n" or length + size > width:
            lines.append(current)
            current, length = "", 0
        if char != "\n":
            current += char
            length += size
    lines.append(current)
    return lines


def format_receipt(snapshot: dict, *, width: int = 32) -> str:
    titles = {"order": "訂餐單", "cancellation": "取消通知", "amendment": "數量修訂單"}
    lines = ["十里方圓 合作社", titles.get(snapshot.get("kind", "order"), "訂餐單")]
    if snapshot.get("revision"):
        lines.append(f"訂單版本：{snapshot['revision']}")
    if snapshot.get("kind") == "amendment":
        lines.append("＊＊ 請依最新版調整原單 ＊＊")
        for change in snapshot.get("quantity_changes", []):
            lines.append(f"{change['name']}：{change['before']} → {change['after']} 份")
    if snapshot.get("reprint"):
        lines.append("＊＊ 補印 ＊＊")
    if snapshot.get("dry_run"):
        lines.append("＊＊ 模擬列印 ＊＊")
    lines.extend([
        f"訂單：{snapshot['order_number']}", f"姓名：{snapshot['customer_name']}",
        f"取餐：{snapshot['pickup_at']}", f"地點：{snapshot['location']}",
    ])
    if snapshot.get("pickup_address"):
        lines.append(f"地址：{snapshot['pickup_address']}")
    if "needs_utensils" in snapshot:
        lines.append(f"餐具：{'需要提供' if snapshot['needs_utensils'] else '不需要'}")
    lines.append("-" * width)
    for item in snapshot["items"]:
        lines.extend([item["name"], f"{item['quantity']} 份 × ${item['unit_price']} = ${item['subtotal']}"])
        lines.extend(f"  {option}" for option in item.get("options", []))
    lines.extend(["-" * width, f"總金額：NT${snapshot['amount_total']}", f"付款方式：{snapshot['payment_method']}", f"付款狀態：{snapshot['payment_status']}"])
    if snapshot.get("note"):
        lines.append(f"備註：{snapshot['note']}")
    if snapshot.get("kind") == "cancellation":
        lines.extend(["＊＊ 請停止製作／核對原單 ＊＊", f"取消原因：{snapshot.get('cancellation_reason', '')}"])
    content = "<BR>".join(line for value in lines for line in wrap_text(value, width)) + "<BR><BR><BR>"
    if len(content.encode("utf-8")) > 5000:
        raise FeieError("receipt_too_long")
    return content


def payment_snapshot(order: Order) -> dict:
    statuses = {"paid": "已付款", "pending": "未付款", "expired": "已逾期／取消", "refund_pending": "退款待處理", "refunded": "已退款", "failed": "付款失敗"}
    return {
        "payment_method": "現場付款" if order.payment_method == PaymentMethod.CASH_ON_PICKUP else "線上付款",
        "payment_status": statuses.get(order.payment_status.value, order.payment_status.value),
    }


def order_print_snapshot(order: Order, customer_name: str) -> dict:
    pickup = order.fulfillment.pickup_at or order.fulfillment.pickup_starts_at
    if pickup.tzinfo is None:
        pickup = pickup.replace(tzinfo=ZoneInfo("UTC"))
    snapshot = {
        "kind": "order", "revision": order.revision or 1, "order_number": order.order_number, "customer_name": customer_name,
        "pickup_at": pickup.astimezone(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M"),
        "location": order.fulfillment.pickup_location, "amount_total": order.amount_total,
        "pickup_address": order.fulfillment.pickup_address,
        "needs_utensils": bool(order.fulfillment.needs_utensils),
        "note": order.kitchen_note, **payment_snapshot(order),
        "items": [{"id": item.id, "name": item.product_name, "quantity": item.quantity, "unit_price": item.unit_price,
                   "subtotal": item.subtotal, "options": [f"{option.group_name}：{option.option_name}" for option in item.selected_options]}
                  for item in order.items],
    }
    return snapshot


def enqueue_order_print(session: AsyncSession, order: Order, user: User, settings: Settings) -> PrintJob | None:
    if not settings.printer_enabled:
        return None
    snapshot = order_print_snapshot(order, user.display_name)
    snapshot["printer_region"] = configured_region(settings)
    job = PrintJob(order_id=order.id, event_key=f"order:{order.id}:initial", kind="order",
        mode="dry_run" if settings.printer_dry_run else "live", printer_fingerprint=printer_fingerprint(settings), snapshot=snapshot)
    session.add(job)
    return job


def _item_identity(item: dict):
    return item.get("id") or (item["name"], tuple(item.get("options", [])))


def quantity_changes(before: dict, after: dict) -> list[dict]:
    # Legacy receipts lack item IDs, so compare their name/options snapshots.
    use_ids = all(item.get("id") for item in before["items"])
    def key(item):
        return _item_identity(item) if use_ids else (item["name"], tuple(item.get("options", [])))
    previous = {key(item): item for item in before["items"]}
    current = {key(item): item for item in after["items"]}
    return [{"name": (current.get(identity) or previous[identity])["name"],
             "before": previous.get(identity, {}).get("quantity", 0),
             "after": current.get(identity, {}).get("quantity", 0)}
            for identity in dict.fromkeys([*previous, *current])
            if previous.get(identity, {}).get("quantity", 0) != current.get(identity, {}).get("quantity", 0)]


async def enqueue_amendment_print(session: AsyncSession, order: Order, before_snapshot: dict, settings: Settings) -> None:
    if not order.meal_event_id:
        return
    jobs = list(await session.scalars(select(PrintJob).where(PrintJob.order_id == order.id)
        .order_by(PrintJob.created_at, PrintJob.id).with_for_update()))
    originals = [job for job in jobs if job.kind in {"order", "amendment"}]
    if not originals:
        if settings.printer_enabled:
            user = await session.get(User, order.user_id)
            enqueue_order_print(session, order, user, settings)
        return
    targets = {(job.mode, job.printer_fingerprint) for job in originals}
    for mode, fingerprint in sorted(targets):
        key = f"order:{order.id}:revision:{order.revision}:{mode}:{fingerprint[:32]}"
        if any(job.event_key == key for job in jobs):
            continue
        target_jobs = [job for job in originals if (job.mode, job.printer_fingerprint) == (mode, fingerprint)]
        sent = [job for job in target_jobs if job.status in {"sending", "submitted", "printed", "unknown", "simulated"}]
        source = max(sent or target_jobs, key=lambda job: (job.snapshot.get("revision", 1), job.created_at, job.id))
        snapshot = order_print_snapshot(order, source.snapshot["customer_name"])
        kind = "amendment" if sent else "order"
        snapshot.update(kind=kind, printer_region=job_region(source))
        if sent:
            snapshot["quantity_changes"] = quantity_changes(source.snapshot, snapshot)
        for job in target_jobs:
            if job.status == "pending":
                job.status, job.last_error = "failed", "order_revision_superseded"
        session.add(PrintJob(order_id=order.id, event_key=key, kind=kind, mode=mode,
            printer_fingerprint=fingerprint, snapshot=snapshot))


async def enqueue_cancellation_print(session: AsyncSession, order: Order, settings: Settings) -> None:
    # Called while holding the order lock, as is the worker claim.
    jobs = list(await session.scalars(select(PrintJob).where(PrintJob.order_id == order.id)
        .order_by(PrintJob.created_at, PrintJob.id).with_for_update()))
    originals = [job for job in jobs if job.kind in {"order", "amendment"}]
    if not originals:
        return
    targets = {(job.mode, job.printer_fingerprint): job for job in originals
               if job.status in {"sending", "submitted", "printed", "unknown", "simulated"}}
    existing_targets = {(job.mode, job.printer_fingerprint) for job in jobs if job.kind == "cancellation"}
    for job in originals:
        if job.status == "pending":
            job.status, job.last_error = "failed", "order_cancelled_before_send"
    for target, original in targets.items():
        if target in existing_targets:
            continue
        latest = max((job for job in originals if (job.mode, job.printer_fingerprint) == target),
            key=lambda job: (job.snapshot.get("revision", 1), job.created_at, job.id))
        session.add(PrintJob(order_id=order.id, event_key=f"order:{order.id}:cancel:{original.mode}:{original.printer_fingerprint[:32]}", kind="cancellation",
            mode=original.mode, printer_fingerprint=original.printer_fingerprint,
            snapshot={**latest.snapshot, "printer_region": job_region(latest), "kind": "cancellation", "cancellation_reason": order.cancellation_reason, **payment_snapshot(order)}))


def job_read(job: PrintJob, settings: Settings | None = None) -> dict:
    return {key: getattr(job, key) for key in (
        "id", "order_id", "kind", "mode", "status", "reprint_of_id", "attempts", "last_error",
        "last_result", "created_at", "updated_at", "submitted_at", "printed_at",
    )} | {"revision": job.snapshot.get("revision", 1), "order_number": job.snapshot["order_number"], "pickup_at": job.snapshot["pickup_at"],
           "printer_region": job_region(job),
           "can_query": bool(job.vendor_order_id) and job.status in {"submitted", "unknown"}
                        and (settings is None or job_matches_printer(job, settings))}
