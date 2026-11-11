from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from ..domain import aware
from ..invoice_timing import TAIPEI
from ..models import InvoiceBuyerType, InvoiceStatus, Order
from ..recovery_quarantine import require_unheld
from .fanyu_invoice import FanyuInvoiceAdapter, FanyuInvoicePrintCodes
from .invoice_service import InvoiceApplicationError, _ensure_provider_binding, _parse_invoice_date


@dataclass(frozen=True)
class FanyuInvoiceCertificate:
    codes: FanyuInvoicePrintCodes
    thermal_pdf: bytes | None = field(repr=False)


async def retrieve_fanyu_invoice_certificate(
    session: AsyncSession,
    order_id: str,
    adapter: FanyuInvoiceAdapter,
) -> FanyuInvoiceCertificate:
    """Read the original issued ticket without changing the invoice or submitting a print job."""
    order = await session.scalar(
        select(Order).where(Order.id == order_id).options(joinedload(Order.invoice))
        .execution_options(autoflush=False, populate_existing=True)
    )
    if order is None or order.invoice is None:
        raise InvoiceApplicationError("找不到已開立的原發票")

    invoice = order.invoice
    if (
        invoice.provider != "fanyu" or invoice.status != InvoiceStatus.ISSUED
        or order.invoice_status != InvoiceStatus.ISSUED
        or not invoice.invoice_number or invoice.invoice_date is None
    ):
        raise InvoiceApplicationError("目前無法取回有效的汎宇原發票")
    _ensure_provider_binding(order, adapter)
    await require_unheld(session, ("order", order.id))

    invoice_date = aware(invoice.invoice_date).astimezone(TAIPEI).date()
    provider_invoice = await adapter.query_invoice(invoice.relate_number, buyer_type=invoice.buyer_type.value)
    reported_date = _parse_invoice_date(provider_invoice.get("InvoiceDate"))
    if (
        provider_invoice.get("ProviderStatus") != "0"
        or provider_invoice.get("InvoiceNo") != invoice.invoice_number
        or reported_date is None or reported_date.astimezone(TAIPEI).date() != invoice_date
    ):
        raise InvoiceApplicationError("汎宇查回資料無法確認為原有效發票")
    reported_random = provider_invoice.get("RandomNumber", "")
    if invoice.random_number and reported_random and invoice.random_number != reported_random:
        raise InvoiceApplicationError("汎宇查回隨機碼與原發票不符")

    day = invoice_date.strftime("%Y%m%d")
    codes = await adapter.get_invoice_print_codes(invoice.invoice_number, day, buyer_type=invoice.buyer_type.value)
    if (random_number := invoice.random_number or reported_random) and not codes.barcode.endswith(random_number):
        raise InvoiceApplicationError("汎宇一維條碼與原發票隨機碼不符")

    pdf = None
    if invoice.buyer_type == InvoiceBuyerType.PERSONAL:
        # The provider's company invoice PDF is A4, not a thermal certificate.
        pdf = await adapter.get_invoice_b2c_pdf(invoice.invoice_number, day, random_number=codes.barcode[-4:])
    return FanyuInvoiceCertificate(codes, pdf)
