from types import SimpleNamespace

import pytest

from app.domain import invoice_estimated_tax_amount, order_tax_amount
from app.models import InvoiceBuyerType, TaxType


@pytest.mark.parametrize('amount', [1, 10, 11, 21, 105, 200, 545, 999, 10001])
def test_company_tax_uses_invoice_rounded_taxable_sales(amount):
    from decimal import Decimal, ROUND_HALF_UP

    expected = amount - int((Decimal(amount) / Decimal('1.05')).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    assert invoice_estimated_tax_amount([(amount, TaxType.TAXABLE)], InvoiceBuyerType.COMPANY) == expected


def test_company_mixed_lines_and_shipping_round_once():
    lines = [(10, TaxType.TAXABLE), (10, TaxType.TAXABLE), (1, TaxType.TAXABLE), (999, TaxType.TAX_EXEMPT)]
    assert invoice_estimated_tax_amount(iter(lines), InvoiceBuyerType.COMPANY) == 1
    order = SimpleNamespace(
        items=[SimpleNamespace(subtotal=10, tax_type=TaxType.TAXABLE), SimpleNamespace(subtotal=999, tax_type=TaxType.TAX_EXEMPT)],
        invoice_buyer_type=InvoiceBuyerType.COMPANY, member_tax_exempt_snapshot=False,
    )
    assert order_tax_amount(order, shipping_fee=11) == 1


def test_personal_estimate_keeps_existing_per_line_rounding():
    assert invoice_estimated_tax_amount([(545, TaxType.TAXABLE)], InvoiceBuyerType.PERSONAL) == 25
    assert invoice_estimated_tax_amount([(545, TaxType.TAX_EXEMPT)], InvoiceBuyerType.PERSONAL) == 0


def test_company_545_estimate_is_26_and_keeps_total():
    assert invoice_estimated_tax_amount([(545, TaxType.TAXABLE)], InvoiceBuyerType.COMPANY) == 26


@pytest.mark.parametrize('channel', ['regular', 'meal', 'group'])
async def test_company_quote_and_new_order_match_provider_tax(database_session, channel):
    from sqlalchemy import select
    from app.models import GroupCampaign, MealEventOffering
    from app.integrations.invoice_service import invoice_request_from_order
    from tests.support import api_test_context
    from tests.test_member_invoice_tax import (
        ROUTERS, checkout_context, invoice_adapter, quote_and_create, stored_order, tax_settings,
    )

    context = await checkout_context(database_session, channel, buyer_type='company')
    if channel == 'regular':
        context.sources[0].member_price = context.sources[0].nonmember_price = 545
    elif channel == 'meal':
        (await database_session.scalar(select(MealEventOffering))).price = 545
    else:
        campaign = await database_session.scalar(select(GroupCampaign))
        campaign.member_price = campaign.nonmember_price = 545
    await database_session.commit()
    async with api_test_context(database_session, ROUTERS, settings=tax_settings()) as client:
        quote, created = await quote_and_create(client, context)
    order = await stored_order(database_session, created['id'])
    payload = invoice_adapter().build_issue_data(invoice_request_from_order(order, 'TAXESTIMATE545'))
    assert quote['tax_amount'] == created['tax_amount'] == order.tax_amount == int(payload['taxAmount']) == 26
    assert quote['amount_total'] == created['amount_total'] == int(payload['totalAmount']) == 545
