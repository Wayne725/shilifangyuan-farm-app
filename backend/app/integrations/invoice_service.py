from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..config import Settings
from ..models import (
    FulfillmentStatus,
    Invoice,
    InvoiceCarrierType,
    InvoiceStatus,
    Order,
    PaymentStatus,
    TaxType,
)
from .invoice import (
    ECPayInvoiceAdapter,
    ECPayInvoiceSettings,
    InvoiceIssueRequest,
    InvoiceIssueResult,
    InvoiceLine,
)


class InvoiceApplicationError(ValueError):
    pass


def invoice_adapter_from_settings(settings: Settings) -> ECPayInvoiceAdapter:
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
        "3"
        if order.invoice_carrier_type == InvoiceCarrierType.MOBILE_BARCODE
        else "1"
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
    return InvoiceIssueRequest(
        relate_number=relate_number,
        customer_email=order.contact_email,
        items=lines,
        carrier_type=carrier_type,
        carrier_number=order.invoice_carrier_value or "",
        customer_id=order.user_id.replace("-", "")[:20],
        remark="十里方圓訂單 {}".format(order.order_number),
    )


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
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return (
                datetime.strptime(raw, pattern)
                .replace(tzinfo=ZoneInfo("Asia/Taipei"))
                .astimezone(timezone.utc)
            )
        except ValueError:
            continue
    return None


async def issue_picked_up_order_invoice(
    session: AsyncSession,
    order_id: str,
    adapter: ECPayInvoiceAdapter,
) -> InvoiceIssueResult:
    order = await session.scalar(
        select(Order)
        .where(Order.id == order_id)
        .options(selectinload(Order.items), selectinload(Order.invoice))
        .with_for_update()
    )
    if order is None:
        raise InvoiceApplicationError("找不到發票訂單")
    if order.fulfillment_status != FulfillmentStatus.PICKED_UP:
        raise InvoiceApplicationError("只有完成取貨的訂單可以開立發票")
    if order.payment_status != PaymentStatus.PAID:
        raise InvoiceApplicationError("只有已付款訂單可以開立發票")

    invoice = order.invoice
    if invoice is not None and invoice.status == InvoiceStatus.ISSUED:
        return InvoiceIssueResult(
            relate_number=invoice.relate_number,
            invoice_number=invoice.invoice_number or "",
            invoice_date="",
            random_number="",
            raw=invoice.provider_response,
        )
    if invoice is None:
        invoice = Invoice(
            order=order,
            relate_number=invoice_relate_number(order.order_number),
            status=InvoiceStatus.PENDING,
        )
        session.add(invoice)
        await session.flush()
    order.invoice_status = InvoiceStatus.PENDING

    try:
        query_result = await adapter.query_invoice(invoice.relate_number)
        existing_number = (
            _query_invoice_number(query_result)
            if int(query_result.get("RtnCode", 0)) == 1
            else ""
        )
        if existing_number:
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
            invoice.status = InvoiceStatus.ISSUED
            invoice.provider_response = query_result
            invoice.error_message = None
            invoice.issued_at = datetime.now(timezone.utc)
            order.invoice_status = InvoiceStatus.ISSUED
            await session.commit()
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

        request = invoice_request_from_order(order, invoice.relate_number)
        result = await adapter.issue_invoice(request)
        invoice.invoice_number = result.invoice_number or None
        invoice.invoice_date = _parse_invoice_date(result.invoice_date)
        invoice.random_number = result.random_number or None
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
