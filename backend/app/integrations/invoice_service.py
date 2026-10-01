from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..config import Settings
from ..models import (
    Invoice,
    InvoiceCarrierType,
    InvoiceItem,
    InvoiceStatus,
    Order,
    OrderFulfillment,
    OutboxEvent,
    PaymentStatus,
    Refund,
    TaxType,
)
from .invoice import (
    ECPayInvoiceAdapter,
    ECPayInvoiceSettings,
    InvoiceIssueRequest,
    InvoiceIssueResult,
    InvoiceLine,
    InvoiceProvider,
    PreparedInvoice,
    PreparedInvoiceLine,
)
from .fanyu_invoice import FanyuInvoiceAdapter, FanyuInvoiceSettings
from .invoice_context import fanyu_account_context


class InvoiceApplicationError(ValueError):
    pass


def invoice_context_from_settings(settings: Settings) -> dict:
    if settings.invoice_provider != "fanyu":
        return {"version": 1, "provider": settings.invoice_provider}
    return fanyu_account_context(
        base_url=settings.fanyu_invoice_base_url,
        company_id=settings.fanyu_invoice_company_id,
        seller_id=settings.fanyu_invoice_seller_id,
        user_id=settings.fanyu_invoice_user_id,
    )


def invoice_adapter_from_settings(
    settings: Settings,
) -> ECPayInvoiceAdapter | FanyuInvoiceAdapter:
    if settings.invoice_provider == "fanyu":
        return FanyuInvoiceAdapter(
            FanyuInvoiceSettings(
                company_id=settings.fanyu_invoice_company_id,
                user_id=settings.fanyu_invoice_user_id,
                auth_password=settings.fanyu_invoice_auth_password,
                api_key=settings.fanyu_invoice_api_key,
                seller_id=settings.fanyu_invoice_seller_id,
                base_url=settings.fanyu_invoice_base_url,
                signature_verified=settings.fanyu_invoice_signature_verified,
                timeout_seconds=settings.integration_timeout_seconds,
            )
        )
    return ECPayInvoiceAdapter(
        ECPayInvoiceSettings(
            merchant_id=settings.ecpay_invoice_merchant_id,
            hash_key=settings.ecpay_invoice_hash_key,
            hash_iv=settings.ecpay_invoice_hash_iv,
            issue_url=settings.ecpay_invoice_issue_url,
            query_url=settings.ecpay_invoice_query_url,
            barcode_url=settings.ecpay_invoice_barcode_url,
            timeout_seconds=settings.integration_timeout_seconds,
        )
    )


def invoice_relate_number(order_number: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9]", "", order_number)
    if not sanitized:
        raise InvoiceApplicationError("訂單編號無法建立發票關聯編號")
    return "INV{}".format(sanitized)[:30]


def invoice_request_from_order(
    order: Order, relate_number: str
) -> InvoiceIssueRequest:
    carrier_type = (
        "mobile_barcode"
        if order.invoice_carrier_type == InvoiceCarrierType.MOBILE_BARCODE
        else "cloud"
    )
    lines = [
        InvoiceLine(
            name=item.product_name,
            quantity=item.quantity,
            unit_price=item.unit_price,
            unit=item.unit_label,
            tax_type="3" if item.tax_type == TaxType.TAX_EXEMPT else "1",
        )
        for item in order.items
    ]
    shipment = (
        order.fulfillment.shipment
        if order.fulfillment is not None
        else None
    )
    if shipment is not None and shipment.shipping_fee > 0:
        lines.append(
            InvoiceLine(
                name="運費",
                quantity=1,
                unit_price=shipment.shipping_fee,
                unit="筆",
                tax_type="1",
            )
        )
    if sum(line.amount for line in lines) != order.amount_total:
        raise InvoiceApplicationError("發票明細總額與訂單總額不一致")
    return InvoiceIssueRequest(
        relate_number=relate_number,
        customer_email=order.invoice_buyer_email or order.contact_email,
        items=lines,
        buyer_type=order.invoice_buyer_type.value,
        buyer_tax_id=order.invoice_buyer_tax_id or "",
        buyer_name=order.invoice_buyer_name or "",
        carrier_type=carrier_type,
        carrier_number=order.invoice_carrier_value or "",
        customer_id=order.user_id.replace("-", "")[:20],
        remark="十里方圓訂單 {}".format(order.order_number),
    )


def enqueue_invoice_issue(
    session: AsyncSession,
    order: Order,
    *,
    trigger: str,
) -> bool:
    if order.payment_status != PaymentStatus.PAID:
        return False
    if order.invoice_status in {
        InvoiceStatus.PENDING,
        InvoiceStatus.ISSUED,
        InvoiceStatus.VOID_PENDING,
        InvoiceStatus.VOIDED,
    }:
        return False
    order.invoice_status = InvoiceStatus.PENDING
    session.add(
        OutboxEvent(
            event_type="invoice.issue_requested",
            aggregate_type="order",
            aggregate_id=order.id,
            payload={"order_id": order.id, "trigger": trigger},
        )
    )
    return True


def enqueue_invoice_adjustment_after_refund(
    session: AsyncSession,
    order: Order,
    refund: Refund,
) -> bool:
    invoice = order.__dict__.get("invoice")
    if invoice is None or invoice.status not in {
        InvoiceStatus.PENDING,
        InvoiceStatus.FAILED,
        InvoiceStatus.ISSUED,
    }:
        return False
    full_refund = refund.amount >= order.amount_total
    if full_refund:
        invoice.status = InvoiceStatus.VOID_PENDING
        invoice.void_reason = refund.reason
        invoice.void_source = "refund"
        order.invoice_status = InvoiceStatus.VOID_PENDING
    session.add(
        OutboxEvent(
            event_type="invoice.adjustment_required",
            aggregate_type="invoice",
            aggregate_id=invoice.id,
            payload={
                "invoice_id": invoice.id,
                "order_id": order.id,
                "refund_id": refund.id,
                "adjustment": "void" if full_refund else "allowance",
                "amount": refund.amount,
                "reason": refund.reason,
                "provider_status_uncertain": not bool(invoice.invoice_number),
                "auto_void": full_refund and invoice.provider == "fanyu",
            },
        )
    )
    return True


def _query_invoice_number(response: dict) -> str:
    return str(
        response.get("InvoiceNo")
        or response.get("IIS_Number")
        or response.get("InvoiceNumber")
        or ""
    )


def _parse_invoice_date(value: object) -> Optional[datetime]:
    if not value:
        return None
    raw = str(value)
    for pattern in (
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%d %H%M%S",
        "%Y/%m/%d %H%M%S",
        "%Y%m%d %H%M%S",
        "%Y%m%d%H%M%S",
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y%m%d",
    ):
        try:
            return (
                datetime.strptime(raw, pattern)
                .replace(tzinfo=ZoneInfo("Asia/Taipei"))
                .astimezone(timezone.utc)
            )
        except ValueError:
            continue
    return None


def _paid_attempt(order: Order):
    paid_attempts = [
        attempt
        for attempt in order.payment_attempts
        if attempt.status == PaymentStatus.PAID
    ]
    return max(
        paid_attempts,
        key=lambda attempt: attempt.paid_at or attempt.created_at,
        default=None,
    )


def _ensure_provider_binding(order: Order, adapter: InvoiceProvider) -> None:
    invoice = order.invoice
    if invoice is not None and invoice.provider != adapter.provider_name:
        raise InvoiceApplicationError(
            "此訂單原本使用 {} 發票，不能改由 {} 重送".format(
                invoice.provider, adapter.provider_name,
            )
        )
    order_context = order.invoice_provider_context
    if order_context and order_context.get("provider") != adapter.provider_name:
        raise InvoiceApplicationError("訂單發票環境／帳戶與目前設定不符，請先人工核對歷史來源")
    if adapter.provider_name != "fanyu":
        return
    expected = getattr(adapter, "binding_context", None)
    if not expected or order_context != expected or (
        invoice is not None and invoice.provider_context != expected
    ):
        raise InvoiceApplicationError("汎宇發票環境／帳戶未確認或與目前設定不符，禁止開票、補查及作廢；請先人工核對歷史來源")
    if invoice is not None and invoice.provider_request and (
        invoice.provider_request.get("sellerID") != expected["seller_id"]
    ):
        raise InvoiceApplicationError("汎宇發票環境／帳戶與原始銷售資料不符，請先人工核對歷史來源")


async def _ensure_invoice_record(
    session: AsyncSession,
    order: Order,
    adapter: InvoiceProvider,
    prepared: PreparedInvoice,
) -> tuple[Invoice, PreparedInvoice]:
    invoice = order.invoice
    provider = adapter.provider_name
    if invoice is not None:
        if invoice.provider != provider:
            raise InvoiceApplicationError(
                "此訂單原本使用 {} 發票，不能改由 {} 重送".format(
                    invoice.provider,
                    provider,
                )
            )
        if not invoice.provider_request:
            invoice.provider_request = dict(prepared.provider_request)
            invoice.sales_amount = int(prepared.sales_amount)
            invoice.tax_amount = int(prepared.tax_amount)
            invoice.total_amount = int(prepared.total_amount)
            if not invoice.items:
                invoice.items = [
                    InvoiceItem(
                        item_name=line.name,
                        quantity=line.quantity,
                        unit=line.unit,
                        unit_price=line.unit_price,
                        amount=line.amount,
                        tax_type=line.tax_type,
                        sequence_number=line.sequence_number,
                    )
                    for line in prepared.items
                ]
        stored_items = tuple(
            PreparedInvoiceLine(
                name=item.item_name,
                quantity=item.quantity,
                unit=item.unit,
                unit_price=Decimal(item.unit_price),
                amount=Decimal(item.amount),
                tax_type=item.tax_type,
                sequence_number=item.sequence_number,
            )
            for item in invoice.items
        )
        return invoice, PreparedInvoice(
            provider=invoice.provider,
            relate_number=invoice.relate_number,
            buyer_type=invoice.buyer_type.value,
            provider_request=(
                dict(invoice.provider_request)
                if invoice.provider_request
                else dict(prepared.provider_request)
            ),
            sales_amount=Decimal(invoice.sales_amount),
            tax_amount=Decimal(invoice.tax_amount),
            total_amount=Decimal(invoice.total_amount),
            items=stored_items or prepared.items,
        )

    paid_attempt = _paid_attempt(order)
    invoice = Invoice(
        order=order,
        relate_number=prepared.relate_number,
        provider=provider,
        provider_context=(dict(order.invoice_provider_context) if order.invoice_provider_context else None),
        payment_attempt_id=(paid_attempt.id if paid_attempt else None),
        buyer_type=order.invoice_buyer_type,
        buyer_tax_id=order.invoice_buyer_tax_id,
        buyer_name=order.invoice_buyer_name,
        buyer_email=order.invoice_buyer_email or order.contact_email,
        carrier_type=order.invoice_carrier_type.value,
        carrier_id=order.invoice_carrier_value,
        sales_amount=int(prepared.sales_amount),
        tax_amount=int(prepared.tax_amount),
        total_amount=int(prepared.total_amount),
        status=InvoiceStatus.PENDING,
        provider_request=dict(prepared.provider_request),
        items=[
            InvoiceItem(
                item_name=line.name,
                quantity=line.quantity,
                unit=line.unit,
                unit_price=line.unit_price,
                amount=line.amount,
                tax_type=line.tax_type,
                sequence_number=line.sequence_number,
            )
            for line in prepared.items
        ],
    )
    session.add(invoice)
    await session.flush()
    return invoice, prepared


async def _query_provider_invoice(
    adapter: InvoiceProvider,
    order: Order,
    relate_number: str,
) -> dict:
    return await adapter.query_invoice(
        relate_number,
        buyer_type=order.invoice_buyer_type.value,
    )


def _apply_provider_query_result(
    invoice: Invoice,
    order: Order,
    query_result: dict,
) -> Optional[InvoiceIssueResult]:
    existing_number = (
        _query_invoice_number(query_result)
        if int(query_result.get("RtnCode", 0)) == 1
        else ""
    )
    if not existing_number:
        invoice.provider_response = dict(query_result)
        return None

    provider_status = str(query_result.get("ProviderStatus", ""))
    invoice.invoice_number = existing_number
    invoice.invoice_date = _parse_invoice_date(
        query_result.get("InvoiceDate")
        or query_result.get("IIS_Create_Date")
    )
    invoice.random_number = str(
        query_result.get("RandomNumber")
        or query_result.get("IIS_Random_Number")
        or ""
    ) or None
    invoice.provider_status = provider_status or None
    previous_status = invoice.status
    invoice.status = (
        InvoiceStatus.VOIDED
        if provider_status == "1"
        else InvoiceStatus.FAILED
        if provider_status == "3"
        else InvoiceStatus.ISSUED
    )
    if invoice.status == InvoiceStatus.ISSUED and previous_status in {
        InvoiceStatus.VOID_PENDING, InvoiceStatus.VOIDED,
    }:
        invoice.status = previous_status
    invoice.provider_response = dict(query_result)
    invoice.error_message = (
        "汎宇回報發票已退回" if provider_status == "3" else None
    )
    if invoice.status == InvoiceStatus.ISSUED:
        invoice.issued_at = invoice.invoice_date or datetime.now(timezone.utc)
    if invoice.status == InvoiceStatus.VOIDED and invoice.voided_at is None:
        invoice.voided_at = datetime.now(timezone.utc)
    order.invoice_status = invoice.status
    return InvoiceIssueResult(
        relate_number=invoice.relate_number,
        invoice_number=existing_number,
        invoice_date=str(
            query_result.get("InvoiceDate")
            or query_result.get("IIS_Create_Date")
            or ""
        ),
        random_number=str(
            query_result.get("RandomNumber")
            or query_result.get("IIS_Random_Number")
            or ""
        ),
        raw=query_result,
    )


async def reconcile_order_invoice(
    session: AsyncSession,
    order_id: str,
    adapter: InvoiceProvider,
    *,
    commit: bool = True,
) -> Optional[InvoiceIssueResult]:
    order = await session.scalar(
        select(Order)
        .where(Order.id == order_id)
        .options(
            selectinload(Order.items),
            selectinload(Order.invoice).selectinload(Invoice.items),
            selectinload(Order.payment_attempts),
            selectinload(Order.fulfillment).selectinload(
                OrderFulfillment.shipment
            ),
        )
        .with_for_update()
    )
    if order is None:
        raise InvoiceApplicationError("找不到發票訂單")
    if order.invoice is None and order.payment_status != PaymentStatus.PAID:
        raise InvoiceApplicationError("只有已付款訂單可以查詢發票")
    _ensure_provider_binding(order, adapter)
    invoice = order.invoice
    if invoice is None:
        request = invoice_request_from_order(order, invoice_relate_number(order.order_number))
        invoice, _ = await _ensure_invoice_record(
            session, order, adapter, adapter.prepare_invoice(request),
        )
    query_result = await adapter.query_invoice(
        invoice.relate_number,
        buyer_type=invoice.buyer_type.value,
    )
    result = _apply_provider_query_result(invoice, order, query_result)
    if commit:
        await session.commit()
    else:
        await session.flush()
    return result


async def issue_paid_order_invoice(
    session: AsyncSession,
    order_id: str,
    adapter: InvoiceProvider,
) -> InvoiceIssueResult:
    order = await session.scalar(
        select(Order)
        .where(Order.id == order_id)
        .options(
            selectinload(Order.items),
            selectinload(Order.invoice).selectinload(Invoice.items),
            selectinload(Order.payment_attempts),
            selectinload(Order.fulfillment).selectinload(
                OrderFulfillment.shipment
            ),
        )
        .with_for_update()
    )
    if order is None:
        raise InvoiceApplicationError("找不到發票訂單")
    if order.payment_status != PaymentStatus.PAID:
        raise InvoiceApplicationError("只有已付款訂單可以開立發票")

    _ensure_provider_binding(order, adapter)
    request = invoice_request_from_order(
        order,
        order.invoice.relate_number
        if order.invoice is not None
        else invoice_relate_number(order.order_number),
    )
    prepared = adapter.prepare_invoice(request)
    invoice, prepared = await _ensure_invoice_record(
        session,
        order,
        adapter,
        prepared,
    )
    if invoice.status == InvoiceStatus.ISSUED:
        return InvoiceIssueResult(
            relate_number=invoice.relate_number,
            invoice_number=invoice.invoice_number or "",
            invoice_date="",
            random_number="",
            raw=invoice.provider_response,
        )
    order.invoice_status = InvoiceStatus.PENDING

    # Persist the deterministic provider reference before any external side effect.
    await session.commit()
    order = await session.scalar(
        select(Order)
        .where(Order.id == order_id)
        .options(
            selectinload(Order.items),
            selectinload(Order.invoice).selectinload(Invoice.items),
            selectinload(Order.payment_attempts),
            selectinload(Order.fulfillment).selectinload(
                OrderFulfillment.shipment
            ),
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if order is None or order.invoice is None:
        raise InvoiceApplicationError("找不到發票開立意圖")
    if order.payment_status != PaymentStatus.PAID:
        raise InvoiceApplicationError("只有已付款訂單可以開立發票")
    _ensure_provider_binding(order, adapter)
    invoice, prepared = await _ensure_invoice_record(
        session,
        order,
        adapter,
        prepared,
    )
    if invoice.status == InvoiceStatus.ISSUED:
        return InvoiceIssueResult(
            relate_number=invoice.relate_number,
            invoice_number=invoice.invoice_number or "",
            invoice_date="",
            random_number="",
            raw=invoice.provider_response,
        )

    try:
        query_result = await _query_provider_invoice(
            adapter,
            order,
            invoice.relate_number,
        )
        existing = _apply_provider_query_result(invoice, order, query_result)
        if existing is not None:
            await session.commit()
            return existing

        result = await adapter.issue_prepared_invoice(prepared)
        if not result.invoice_number.strip():
            raise InvoiceApplicationError("發票服務成功回應缺少發票號碼")
        invoice.invoice_number = result.invoice_number or None
        invoice.invoice_date = _parse_invoice_date(result.invoice_date)
        invoice.random_number = result.random_number or None
        invoice.provider_status = "0"
        invoice.status = InvoiceStatus.ISSUED
        invoice.provider_response = dict(result.raw)
        invoice.error_message = None
        invoice.issued_at = datetime.now(timezone.utc)
        order.invoice_status = InvoiceStatus.ISSUED
        await session.commit()
        return result
    except Exception as exc:
        invoice.status = InvoiceStatus.FAILED
        invoice.error_message = str(exc)[:500]
        order.invoice_status = InvoiceStatus.FAILED
        await session.commit()
        raise


issue_picked_up_order_invoice = issue_paid_order_invoice
