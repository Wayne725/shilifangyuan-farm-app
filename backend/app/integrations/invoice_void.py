from __future__ import annotations

from datetime import timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import (
    AdminAudit,
    Invoice,
    InvoiceAllowance,
    InvoiceAllowanceStatus,
    InvoiceStatus,
    Order,
    PaymentStatus,
    Refund,
    RefundStatus,
)
from .common import IntegrationError
from .fanyu_invoice import FanyuInvoiceAdapter
from .invoice import InvoiceProvider
from .invoice_service import InvoiceApplicationError, reconcile_order_invoice


class InvoiceVoidError(InvoiceApplicationError):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


async def void_refunded_order_invoice(
    session: AsyncSession,
    order_id: str,
    adapter: InvoiceProvider,
    *,
    reason: str,
    actor_id: str,
    trigger: str = "admin",
    refund_id: str | None = None,
) -> Invoice:
    audit_context = (
        {"trigger": trigger, "refund_id": refund_id}
        if trigger != "admin" else {}
    )

    def audit(action: str, **data: object) -> None:
        session.add(AdminAudit(
            actor_id=actor_id,
            action=action,
            aggregate_type="order",
            aggregate_id=order_id,
            reason=reason,
            data={**audit_context, **data},
        ))

    try:
        order = await session.scalar(
            select(Order)
            .where(Order.id == order_id)
            .options(selectinload(Order.invoice))
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if order is None or order.invoice is None:
            raise InvoiceVoidError("找不到訂單發票", 404)
        invoice = order.invoice
        refunded = await session.scalar(
            select(func.coalesce(func.sum(Refund.amount), 0)).where(
                Refund.order_id == order_id,
                Refund.status == RefundStatus.COMPLETED,
            )
        )
        if order.payment_status != PaymentStatus.REFUNDED or refunded < order.amount_total:
            raise InvoiceVoidError("此操作僅供已完成全額退款的訂單")
        if invoice.provider != "fanyu" or not isinstance(adapter, FanyuInvoiceAdapter):
            raise InvoiceVoidError("請至原發票供應商處理，不可切換供應商作廢")
        allowance = await session.scalar(
            select(InvoiceAllowance.id).where(
                InvoiceAllowance.invoice_id == invoice.id,
                InvoiceAllowance.status != InvoiceAllowanceStatus.VOIDED,
            ).limit(1)
        )
        if allowance:
            raise InvoiceVoidError("已有折讓紀錄，請先由會計核對，不可直接作廢")
        if invoice.provider_request.get("sellerID") != adapter.settings.seller_id:
            raise InvoiceVoidError("目前賣方設定不符合原發票快照，請人工核對")
        original_number = invoice.invoice_number
        found = await reconcile_order_invoice(session, order_id, adapter, commit=False)
        if found is None:
            raise InvoiceVoidError("汎宇查無原發票，停止作廢，請先人工核對")
        if original_number and invoice.invoice_number != original_number:
            raise InvoiceVoidError("查回票號與原發票不同，停止作廢")
        if invoice.invoice_date is None:
            raise InvoiceVoidError("查回發票日期不完整，停止作廢")
        if invoice.provider_status not in {"0", "1"}:
            raise InvoiceVoidError("汎宇尚未回傳可確認的原發票狀態")
        if invoice.status == InvoiceStatus.VOIDED and invoice.provider_status != "1":
            raise InvoiceVoidError("汎宇狀態與本機作廢紀錄不一致，請人工核對")
    except (InvoiceApplicationError, IntegrationError, ValueError, TimeoutError) as exc:
        await session.rollback()
        blocked = isinstance(exc, InvoiceApplicationError)
        message = str(exc) if blocked else "作廢前查詢失敗，未送出作廢；請檢查供應商設定"
        invoice = await session.scalar(select(Invoice).where(Invoice.order_id == order_id))
        if invoice is not None:
            invoice.error_message = message
        audit("invoice.void_blocked" if blocked else "invoice.void_query_failed", error_type=type(exc).__name__)
        await session.commit()
        raise InvoiceVoidError(message, getattr(exc, "status_code", 409) if blocked else 502) from exc

    if invoice.status != InvoiceStatus.VOIDED and invoice.void_source != "api_submission_started":
        invoice.status = order.invoice_status = InvoiceStatus.VOID_PENDING
        invoice.void_reason = reason
        invoice.void_source = "api_submission_started"
        invoice_number = invoice.invoice_number
        invoice_date = invoice.invoice_date.replace(
            tzinfo=invoice.invoice_date.tzinfo or timezone.utc,
        ).astimezone(ZoneInfo("Asia/Taipei")).strftime("%Y%m%d")
        audit("invoice.void_requested", invoice_number=invoice_number, provider="fanyu")
        # Persist the submission marker before releasing the lock or contacting Fanyu.
        await session.commit()
        try:
            response = await adapter.cancel_invoice(
                order_id=invoice.relate_number,
                buyer_type=invoice.buyer_type.value,
                buyer_tax_id=invoice.buyer_tax_id or "",
                invoice_number=invoice_number,
                invoice_date=invoice_date,
                reason=reason,
            )
            if str(response.get("invNo", "")) != invoice_number:
                raise ValueError("作廢回傳票號不符")
            found = await reconcile_order_invoice(session, order_id, adapter, commit=False)
            if found is None or invoice.invoice_number != invoice_number:
                raise ValueError("作廢後查回票號不符")
        except (IntegrationError, ValueError, TimeoutError):
            await session.rollback()
            order = await session.scalar(
                select(Order).where(Order.id == order_id)
                .options(selectinload(Order.invoice))
                .execution_options(populate_existing=True)
                .with_for_update()
            )
            invoice = order.invoice

    invoice.error_message = (
        None if invoice.status == InvoiceStatus.VOIDED
        else "作廢送出結果未確認；請重新查詢，勿重送作廢"
    )
    audit("invoice.void_checked", status=invoice.status.value, provider="fanyu")
    await session.commit()
    return invoice
