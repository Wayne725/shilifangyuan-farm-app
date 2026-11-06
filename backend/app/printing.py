from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .integrations.common import IntegrationError
from .integrations.feie import FeieError, configured_region
from .integrations.pii_crypto import pii_cipher_from_settings
from .models import FulfillmentStatus, MemberProfile, Order, OrderFulfillment, PaymentAttempt, PaymentMethod, PaymentStatus, PrintJob, User


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
    titles = {"order": "便當訂餐單", "cancellation": "取消通知", "amendment": "數量修訂單"}
    lines: list[str] = []

    def add(value: object, *tags: str) -> None:
        line_width = width // 2 if any(tag in {"CB", "B", "W"} for tag in tags) else width
        for line in wrap_text(value, line_width):
            for tag in reversed(tags):
                line = f"<{tag}>{line}</{tag}>"
            lines.append(line)

    add("十里方圓 合作社", "C")
    add(titles.get(snapshot.get("kind", "order"), "便當訂餐單"), "CB")
    if snapshot.get("revision"):
        add(f"訂單版本：{snapshot['revision']}")
    if snapshot.get("kind") == "amendment":
        add("＊＊ 請依最新版調整原單 ＊＊", "BOLD")
        for change in snapshot.get("quantity_changes", []):
            add(f"{change['name']}：{change['before']} → {change['after']} 份", "BOLD")
    if snapshot.get("reprint"):
        add("＊＊ 補印 ＊＊", "CB")
    if snapshot.get("dry_run"):
        add("＊＊ 模擬列印 ＊＊", "BOLD")
    if snapshot.get("pickup_code"):
        add(f"取餐碼 {snapshot['pickup_code']}", "CB")
    add(f"訂單：{snapshot['order_number']}")
    add(f"顧客：{snapshot['customer_name']}", "L", "BOLD")
    if snapshot.get("customer_phone"):
        add(f"電話：{snapshot['customer_phone']}")
    elif snapshot.get("customer_email"):
        add(f"Email：{snapshot['customer_email']}")
    add("-" * width)
    if snapshot.get("ordered_at"):
        add(f"訂購：{snapshot['ordered_at']}")
    add(f"取餐：{snapshot['pickup_at']}", "L")
    add("預約自取", "C")
    add(f"取餐點：{snapshot['location']}", "BOLD")
    if snapshot.get("pickup_address"):
        add(f"地址：{snapshot['pickup_address']}")
    if "needs_utensils" in snapshot:
        add(f"餐具：{'需要提供' if snapshot['needs_utensils'] else '不需要'}", "BOLD")
    if snapshot.get("note"):
        add(f"備註：{snapshot['note']}", "BOLD")
    if snapshot.get("kind") == "cancellation":
        add("＊＊ 請停止製作／核對原單 ＊＊", "BOLD")
        add(f"取消原因：{snapshot.get('cancellation_reason', '')}", "BOLD")
    add("-" * width)
    for item in snapshot["items"]:
        add(f"{item['quantity']} × {item['name']}", "L", "BOLD")
        for option in item.get("options", []):
            add(f"  {option}")
        add(f"單價 ${item['unit_price']}　小計 ${item['subtotal']}", "RIGHT")
    add(f"共 {sum(item['quantity'] for item in snapshot['items'])} 份", "RIGHT")
    items_total = sum(item['subtotal'] for item in snapshot['items'])
    if items_total != snapshot['amount_total']:
        add(f"餐點小計：NT${items_total}", "RIGHT")
        adjustment = snapshot['amount_total'] - items_total
        add(f"金額調整：{'+' if adjustment > 0 else '-'}NT${abs(adjustment)}", "RIGHT")
    add("-" * width)
    add(f"付款方式：{snapshot['payment_method']}", "C")
    add(f"付款狀態：{snapshot['payment_status']}", "C", "L")
    add(f"總金額：NT${snapshot['amount_total']}", "C", "L", "BOLD")
    add("-" * width)
    add(f"送印時間：{snapshot.get('print_requested_at') or '送印時產生'}")
    add("時間皆為台灣時間", "C")
    content = "<BR>".join(lines) + "<BR><BR><BR>"
    if len(content.encode("utf-8")) > 5000:
        raise FeieError("receipt_too_long")
    return content


def receipt_preview(content: str) -> str:
    return re.sub(r"</?(?:CB|B|C|L|W|RIGHT|BOLD)>", "", content.replace("<BR>", "\n"))


def payment_snapshot(order: Order) -> dict:
    statuses = {"paid": "已付款", "pending": "未付款", "expired": "已逾期／取消", "refund_pending": "退款待處理", "refunded": "已退款", "failed": "付款失敗"}
    return {
        "payment_method": {PaymentMethod.CASH_ON_PICKUP: "現場付款", PaymentMethod.CASH_ON_DELIVERY: "貨到付款"}.get(order.payment_method, "線上付款"),
        "payment_status": statuses.get(order.payment_status.value, order.payment_status.value),
    }


async def payment_snapshot_from_session(session: AsyncSession, order: Order) -> dict:
    result = payment_snapshot(order)
    if order.payment_status not in {PaymentStatus.PAID, PaymentStatus.REFUND_PENDING, PaymentStatus.REFUNDED}:
        return result
    attempt = await session.scalar(select(PaymentAttempt).where(
        PaymentAttempt.order_id == order.id, PaymentAttempt.order_revision == order.revision,
        PaymentAttempt.amount == order.amount_total,
        PaymentAttempt.superseded_at.is_(None), PaymentAttempt.paid_at.is_not(None),
        PaymentAttempt.status.in_({PaymentStatus.PAID, PaymentStatus.REFUND_PENDING, PaymentStatus.REFUNDED}),
    ).order_by(PaymentAttempt.paid_at.desc(), PaymentAttempt.created_at.desc(), PaymentAttempt.id.desc()).limit(1))
    if attempt is not None:
        if attempt.provider == "raygate" and attempt.provider_response.get("PaymentType") == "linepay":
            result["payment_method"] = "LINE Pay"
        elif attempt.provider == "onsite":
            method = attempt.provider_response.get("collection_method")
            result["payment_method"] = {"cash": "現場付款（現金）", "pos": "現場付款（POS）", "qr": "現場付款（掃碼）"}.get(method, "現場付款")
    return result


def receipt_time(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo("UTC"))
    return value.astimezone(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M:%S")


_RECEIPT_CONTENT_PREFIX = "receipt-content:v1:"


def render_receipt_snapshot(snapshot: dict, order_id: str, settings: Settings) -> dict:
    result = {key: value for key, value in snapshot.items() if key != "customer_phone"}
    if "customer_phone_encrypted" in result:
        encrypted = result.pop("customer_phone_encrypted")
        try:
            if not isinstance(encrypted, str) or not encrypted:
                raise FeieError("receipt_contact_decryption_failed")
            phone = pii_cipher_from_settings(settings).decrypt_text(
                encrypted, associated_data=f"receipt-contact:{order_id}:phone").strip()
            if not phone:
                raise FeieError("receipt_contact_decryption_failed")
        except IntegrationError:
            raise FeieError("receipt_contact_decryption_failed") from None
        result.pop("customer_email", None)
        result["customer_phone"] = phone
    return result


def seal_receipt_content(content: str, snapshot: dict, order_id: str, settings: Settings) -> str:
    if "customer_phone_encrypted" not in snapshot:
        return content
    try:
        encrypted = pii_cipher_from_settings(settings).encrypt_text(content, associated_data=f"receipt-content:{order_id}")
    except IntegrationError:
        raise FeieError("receipt_content_decryption_failed") from None
    return f"{_RECEIPT_CONTENT_PREFIX}{encrypted}"


def open_receipt_content(content: str, order_id: str, settings: Settings) -> str:
    if not content.startswith(_RECEIPT_CONTENT_PREFIX):
        if content.startswith("receipt-content:"):
            raise FeieError("receipt_content_decryption_failed")
        return content
    try:
        return pii_cipher_from_settings(settings).decrypt_text(
            content[len(_RECEIPT_CONTENT_PREFIX):], associated_data=f"receipt-content:{order_id}")
    except IntegrationError:
        raise FeieError("receipt_content_decryption_failed") from None


def receipt_contact(snapshot: dict, contact_email: str) -> dict:
    if "customer_phone_encrypted" in snapshot:
        return {"customer_phone_encrypted": snapshot["customer_phone_encrypted"]}
    return {"customer_email": snapshot.get("customer_email") or contact_email}


async def fresh_receipt_snapshot(session: AsyncSession, snapshot: dict, order: Order) -> dict:
    result = {key: value for key, value in snapshot.items()
              if key not in {"print_requested_at", "customer_phone", "customer_phone_encrypted", "customer_email"}}
    result.update(receipt_contact(snapshot, order.contact_email))
    result.setdefault("ordered_at", receipt_time(order.created_at))
    if "pickup_code" not in result:
        result["pickup_code"] = await session.scalar(select(OrderFulfillment.pickup_code).where(OrderFulfillment.order_id == order.id))
    return result


def _contact_phone(ciphertext: str | None, aad: str, settings: Settings) -> str | None:
    if not ciphertext:
        return None
    try:
        return pii_cipher_from_settings(settings).decrypt_text(ciphertext, associated_data=aad).strip() or None
    except IntegrationError:
        return None


def order_print_snapshot(order: Order, customer_name: str, contact: dict | None = None) -> dict:
    pickup = order.fulfillment.pickup_at or order.fulfillment.pickup_starts_at
    if pickup.tzinfo is None:
        pickup = pickup.replace(tzinfo=ZoneInfo("UTC"))
    snapshot = {
        "kind": "order", "revision": order.revision or 1, "order_number": order.order_number, "customer_name": customer_name,
        "ordered_at": receipt_time(order.created_at), "pickup_code": order.fulfillment.pickup_code,
        "pickup_at": pickup.astimezone(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M"),
        "location": order.fulfillment.pickup_location, "amount_total": order.amount_total,
        "pickup_address": order.fulfillment.pickup_address,
        "needs_utensils": bool(order.fulfillment.needs_utensils),
        "note": order.kitchen_note, **payment_snapshot(order),
        "items": [{"id": item.id, "name": item.product_name, "quantity": item.quantity, "unit_price": item.unit_price,
                   "subtotal": item.subtotal, "options": [f"{option.group_name}：{option.option_name}" for option in item.selected_options]}
                  for item in order.items],
    }
    snapshot.update(receipt_contact(contact or {}, order.contact_email))
    return snapshot


async def enqueue_order_print(session: AsyncSession, order: Order, user: User, settings: Settings) -> PrintJob | None:
    if not settings.printer_enabled:
        return None
    fulfillment = order.fulfillment
    phone = _contact_phone(fulfillment.recipient_phone_encrypted,
                           f"order-fulfillment:{fulfillment.id}:recipient_phone", settings)
    if phone is None:
        ciphertext = await session.scalar(select(MemberProfile.phone_encrypted).where(MemberProfile.user_id == order.user_id))
        phone = _contact_phone(ciphertext, f"member-profile:{order.user_id}", settings)
    contact = {"customer_email": order.contact_email}
    if phone:
        try:
            contact = {"customer_phone_encrypted": pii_cipher_from_settings(settings).encrypt_text(
                phone, associated_data=f"receipt-contact:{order.id}:phone")}
        except IntegrationError:
            pass
    snapshot = order_print_snapshot(order, user.display_name, contact)
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
            await enqueue_order_print(session, order, user, settings)
        return
    targets = {(job.mode, job.printer_fingerprint) for job in originals}
    for mode, fingerprint in sorted(targets):
        key = f"order:{order.id}:revision:{order.revision}:{mode}:{fingerprint[:32]}"
        if any(job.event_key == key for job in jobs):
            continue
        target_jobs = [job for job in originals if (job.mode, job.printer_fingerprint) == (mode, fingerprint)]
        sent = [job for job in target_jobs if job.status in {"sending", "submitted", "printed", "unknown", "simulated"}]
        source = max(sent or target_jobs, key=lambda job: (job.snapshot.get("revision", 1), job.created_at, job.id))
        snapshot = order_print_snapshot(order, source.snapshot["customer_name"], receipt_contact(source.snapshot, order.contact_email))
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
            snapshot={**await fresh_receipt_snapshot(session, latest.snapshot, order), "printer_region": job_region(latest), "kind": "cancellation",
                      "cancellation_reason": order.cancellation_reason, **await payment_snapshot_from_session(session, order)}))


def confirmed_print_payment(order: Order) -> bool:
    return order.payment_status == PaymentStatus.PAID and order.paid_at is not None


def job_read(job: PrintJob, settings: Settings | None = None, order: Order | None = None) -> dict:
    return {key: getattr(job, key) for key in (
        "id", "order_id", "kind", "mode", "status", "reprint_of_id", "attempts", "last_error",
        "last_result", "created_at", "updated_at", "submitted_at", "printed_at",
    )} | {"revision": job.snapshot.get("revision", 1), "order_number": job.snapshot["order_number"], "pickup_at": job.snapshot["pickup_at"],
           "printer_region": job_region(job),
           "awaiting_payment": bool(job.status == "pending" and job.kind in {"order", "amendment"}
                                    and order is not None and not confirmed_print_payment(order)
                                    and order.cancelled_at is None and order.fulfillment_status != FulfillmentStatus.CANCELLED),
           "can_query": bool(job.vendor_order_id) and job.status in {"submitted", "unknown"}
                        and (settings is None or job_matches_printer(job, settings))}
