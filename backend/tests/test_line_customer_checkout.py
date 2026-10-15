import pytest
from sqlalchemy import func, select

from app.models import ExternalIdentity, Order, TaxType
from app.routers.auth import user_read
from tests.support import api_test_context, auth_headers
from tests.test_member_invoice_tax import CHANNELS, ROUTERS, checkout_context, tax_settings


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", CHANNELS)
@pytest.mark.parametrize("contact_field", ["filled", "null", "missing"])
async def test_line_customer_defers_email_until_checkout_without_changing_login(database_session, channel, contact_field):
    context = await checkout_context(database_session, channel, membership_state="nonmember", buyer_type="personal", base_tax_type=TaxType.TAXABLE)
    context.user.email = None
    context.user.email_verified_at = None
    identity = ExternalIdentity(user_id=context.user.id, provider="line", subject="U-email-less-checkout")
    database_session.add(identity)
    await database_session.commit()
    headers = auth_headers(context.user)
    async with api_test_context(database_session, ROUTERS, settings=tax_settings()) as client:
        payload = {**context.payload, "contact_email": "", "invoice_buyer_email": ""}
        invalid = await client.post(context.create_path, json=payload, headers=headers)
        assert invalid.status_code == 422
        assert await database_session.scalar(select(func.count()).select_from(Order)) == 0
        assert context.user.notification_email is None
        payload.update(contact_email="CHECKOUT@example.com", invoice_buyer_email="CHECKOUT@example.com")
        if contact_field == "null":
            payload["contact_email"] = None
        elif contact_field == "missing":
            payload.pop("contact_email")
        created = await client.post(context.create_path, json=payload, headers=headers)
        assert created.status_code == 201, created.text
        saved_order = await database_session.get(Order, created.json()["id"])
        assert saved_order.contact_email == "checkout@example.com"
        assert saved_order.invoice_buyer_email == "checkout@example.com"
        assert created.json()["payment_status"] == "pending"
        await database_session.refresh(context.user)
        assert context.user.email is None and context.user.email_verified_at is None
        assert context.user.notification_email == "checkout@example.com"
        assert user_read(context.user).notification_email == "checkout@example.com"
        assert (await database_session.get(ExternalIdentity, identity.id)).user_id == context.user.id
