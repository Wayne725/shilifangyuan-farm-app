from datetime import date, datetime, timezone
import json
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import pytest

from app.integrations.common import HTTPResponse
from app.integrations.ecpay import build_check_mac_value
from app.integrations.payment_service import (
    browser_return_base, browser_return_storefront, raygate_payment_adapter_from_settings,
)
from app.integrations.raygate import RayGateAdapter
from app.models import (
    Membership, MembershipApplication, MembershipApplicationStatus, MembershipCharge, MembershipChargeKind,
    MembershipFeeSchedule, MembershipStatus, PaymentAttempt, PaymentStatus,
)
from app.routers import payments as payments_module
from app.routers.activity_payments import router as activity_router
from app.routers.auth import auth_router
from app.routers.payments import payments_router
from tests.support import api_test_context, auth_headers, fast_password_hash
from tests.test_activity_payments import make_activity, registration
from tests.test_integrations import make_regular_order, preview_raygate_acceptance_settings
from tests.test_payment_return import payment_settings, successful_result
from tests.test_raygate import payment_result


CANONICAL = "https://shilifangyuan.com"
LEGACY = "https://shilifangyuan-web.onrender.com"
PASSWORD = "local-checkout-example-01"


def storefront_settings(settings):
    return settings.model_copy(update={
        "web_base_url": CANONICAL, "cors_origins": [CANONICAL, LEGACY], "refresh_cookie_secure": True,
    })


def test_legacy_storefront_requires_configured_cookie_origin():
    settings = storefront_settings(payment_settings()).model_copy(update={"cors_origins": [CANONICAL]})
    assert browser_return_storefront(settings, LEGACY) is None
    assert browser_return_base(settings, "legacy") == CANONICAL


def test_disabled_capture_keeps_existing_legacy_return_destination():
    settings = storefront_settings(payment_settings()).model_copy(update={"payment_legacy_return_enabled": False})
    assert browser_return_storefront(settings, LEGACY) is None
    assert browser_return_base(settings, "legacy") == LEGACY


async def prepare_login(session, user):
    user.email = "legacy-buyer@example.com"
    user.password_hash = fast_password_hash(PASSWORD)
    user.email_verified_at = datetime.now(timezone.utc)
    await session.commit()


async def legacy_login(client, user):
    login = await client.post(
        f"{LEGACY}/v1/auth/login", headers={"Origin": LEGACY},
        json={"email": user.email, "password": PASSWORD},
    )
    assert login.status_code == 200, login.text
    assert "domain=" not in login.headers["Set-Cookie"].lower()
    assert (await client.post(f"{CANONICAL}/v1/auth/refresh", headers={"Origin": CANONICAL})).status_code == 401
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def test_ecpay_browser_return_preserves_legacy_only_login(database_session):
    session = database_session
    buyer, _product, order = await make_regular_order(session)
    await prepare_login(session, buyer)
    settings = storefront_settings(payment_settings())
    async with api_test_context(session, [auth_router, payments_router], settings=settings) as client:
        auth = await legacy_login(client, buyer)
        started = await client.post(f"{LEGACY}/v1/orders/{order.id}/payment-attempts", headers={**auth, "Origin": LEGACY})
        assert started.status_code == 201, started.text
        attempt = await session.get(PaymentAttempt, started.json()["id"])
        assert attempt.browser_return_storefront == "legacy"
        checkout = await client.get(started.json()["payment_url"])
        assert checkout.status_code == 200, checkout.text
        assert attempt.checkout_payload["ClientBackURL"] == f"{LEGACY}/orders?order_id={order.id}"
        assert "browser_return_storefront" not in checkout.text
        assert attempt.checkout_payload["CheckMacValue"] == build_check_mac_value(
            {key: value for key, value in attempt.checkout_payload.items() if key != "CheckMacValue"},
            settings.ecpay_payment_hash_key, settings.ecpay_payment_hash_iv,
        )
        result = await client.post(
            f"{settings.app_base_url}/payments/result", data=successful_result(attempt),
            headers={"Origin": "https://checkout.example.test", "X-Forwarded-Host": "attacker.example"},
        )
        assert result.status_code == 303
        parsed = urlsplit(result.headers["location"])
        assert f"{parsed.scheme}://{parsed.netloc}" == LEGACY
        returned = await client.post(f"{parsed.scheme}://{parsed.netloc}/v1/auth/refresh", headers={"Origin": LEGACY})
        assert returned.status_code == 200, returned.text
        assert returned.json()["user"]["id"] == buyer.id
        status = await client.get(f"{LEGACY}/v1/payment-attempts/{attempt.id}",
                                  headers={"Authorization": f"Bearer {returned.json()['access_token']}"})
        assert status.status_code == 200 and status.json()["status"] == "paid"
        await session.refresh(order)
        assert order.payment_status == PaymentStatus.PAID


@pytest.mark.parametrize("origin,stored", [
    (CANONICAL, "canonical"),
    (LEGACY + ".attacker.example", None),
    ("null", None),
    (None, None),
])
async def test_unknown_origin_and_reused_attempt_keep_canonical_destination(database_session, origin, stored):
    session = database_session
    buyer, _product, order = await make_regular_order(session)
    settings = storefront_settings(payment_settings())
    async with api_test_context(session, [payments_router], settings=settings) as client:
        path = f"/v1/orders/{order.id}/payment-attempts"
        headers = auth_headers(buyer)
        if origin is not None:
            headers["Origin"] = origin
        started = await client.post(path, headers=headers)
        assert started.status_code == 201, started.text
        attempt = await session.get(PaymentAttempt, started.json()["id"])
        assert attempt.browser_return_storefront == stored
        assert attempt.checkout_payload["ClientBackURL"] == f"{CANONICAL}/orders?order_id={order.id}"
        retry = await client.post(path, headers={**headers, "Origin": LEGACY})
        assert retry.status_code == 201 and retry.json()["id"] == attempt.id
        await session.refresh(attempt)
        assert attempt.browser_return_storefront == stored
        result = await client.post("/payments/result", data=successful_result(attempt), headers={"Origin": LEGACY})
        assert result.status_code == 303 and result.headers["location"] == f"{CANONICAL}/orders?order_id={order.id}&payment=paid"


async def test_raygate_checkout_and_browser_return_preserve_legacy_session(database_session, monkeypatch):
    session = database_session
    buyer, _product, order = await make_regular_order(session)
    await prepare_login(session, buyer)
    settings = storefront_settings(preview_raygate_acceptance_settings(environment="test"))
    provider = raygate_payment_adapter_from_settings(settings)
    query_count = 0

    async def provider_query(_url, _payload, _headers, _timeout):
        nonlocal query_count
        query_count += 1
        data = payment_result(
            amount=attempt.amount, store_code=provider.settings.store_identifier,
            pos_order_number=attempt.merchant_trade_no,
            transaction_time=datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M:%S"),
        )
        return HTTPResponse(200, json.dumps({"ErrorCode": "0000", "Message": "ok", "Data": [data]}), {})

    monkeypatch.setattr(payments_module, "raygate_payment_adapter_from_settings",
                        lambda _settings: RayGateAdapter(provider.settings, transport=provider_query))
    async with api_test_context(session, [auth_router, payments_router], settings=settings) as client:
        auth = await legacy_login(client, buyer)
        started = await client.post(f"{LEGACY}/v1/orders/{order.id}/payment-attempts", headers={**auth, "Origin": LEGACY})
        assert started.status_code == 201, started.text
        attempt = await session.get(PaymentAttempt, started.json()["id"])
        assert attempt.browser_return_storefront == "legacy"
        checkout = await client.get(started.json()["payment_url"])
        assert checkout.status_code == 303
        provider_data = parse_qs(urlsplit(checkout.headers["location"]).query)
        return_url = provider.decrypt_transaction(provider_data["TransactionData"][0])["return_url"]
        attempt_id, order_id = attempt.id, order.id
        assert return_url == f"{settings.app_base_url}/payments/raygate/result?attempt_id={attempt_id}"
        result = await client.get(return_url, headers={"Origin": CANONICAL})
        assert result.status_code == 303 and result.headers["location"] == (
            f"{LEGACY}/orders?order_id={order_id}&payment=paid&attempt_id={attempt_id}"
        )
        assert query_count == 1
        assert (await client.post(f"{LEGACY}/v1/auth/refresh", headers={"Origin": LEGACY})).status_code == 200
        await session.refresh(order)
        assert order.payment_status == PaymentStatus.PAID


@pytest.mark.parametrize("origin", [LEGACY, CANONICAL])
@pytest.mark.parametrize("subject,landing,query_key", [
    ("membership", "/account", "membership_charge_id"),
    ("activity", "/activities/registrations", "activity_charge_id"),
])
async def test_ecpay_storefront_is_used_for_membership_and_activity(database_session, subject, landing, query_key, origin):
    session = database_session
    if subject == "membership":
        buyer, _product, _order = await make_regular_order(session)
        application = MembershipApplication(user=buyer, status=MembershipApplicationStatus.SUBMITTED)
        membership = Membership(user=buyer, application=application, status=MembershipStatus.PENDING_PAYMENT)
        fee = MembershipFeeSchedule(charge_kind=MembershipChargeKind.ADMISSION_FEE, amount=500, effective_from=date(2026, 1, 1))
        charge = MembershipCharge(user=buyer, application=application, membership=membership, fee_schedule=fee,
                                  charge_kind=MembershipChargeKind.ADMISSION_FEE, amount=500)
        session.add(charge)
        await session.commit()
        charge_id = charge.id
        path = f"/v1/membership/charges/{charge_id}/payment-attempts"
    else:
        activity = await make_activity(session)
        buyer = activity.buyer
        registered = await registration(activity)
        charge_id = registered.charge.id
        path = f"/v1/activity-charges/{charge_id}/payment-attempts"
    settings = storefront_settings(payment_settings())
    async with api_test_context(session, [activity_router, payments_router], settings=settings) as client:
        started = await client.post(path, headers={**auth_headers(buyer), "Origin": origin})
        assert started.status_code == 201, started.text
        attempt = await session.get(PaymentAttempt, started.json()["id"])
        assert attempt.browser_return_storefront == ("legacy" if origin == LEGACY else "canonical")
        client_back = urlsplit(attempt.checkout_payload["ClientBackURL"])
        assert f"{client_back.scheme}://{client_back.netloc}{client_back.path}" == f"{origin}{landing}"
        assert parse_qs(client_back.query) == (
            {query_key: [charge_id], "attempt_id": [attempt.id]} if origin == LEGACY else {}
        )
        result = await client.post("/payments/result", data=successful_result(attempt))
        assert result.status_code == 303
        location = urlsplit(result.headers["location"])
        assert f"{location.scheme}://{location.netloc}{location.path}" == f"{origin}{landing}"
        assert parse_qs(location.query) == {query_key: [charge_id], "payment": ["paid"]}
