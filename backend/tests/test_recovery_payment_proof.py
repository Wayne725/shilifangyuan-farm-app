from argparse import Namespace
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4
from zoneinfo import ZoneInfo

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import recovery_capture, recovery_operator
from app.auth import create_token
from app.integrations.common import HTTPResponse
from app.integrations.payment_service import SQLAlchemyPaymentCallbackRepository
from app.integrations.raygate import RayGateAdapter
from app.models import (
    AdminAudit, ExternalEvent, InventoryReservation, OutboxEvent, OutboxStatus, PaymentAttempt,
    PaymentStatus, RecoveryCutover, RecoveryHold, Refund, RefundStatus, ReservationStatus, UserRole,
)
from app.recovery_payment_proof import RayGateCaptureRequest, _signed_bytes, apply_raygate_proof, capture_raygate_proof
from app.recovery_quarantine import Item, RecoveryHoldActive, Resolution, resolve_hold
from tests.support import make_test_settings
from tests.test_integrations import make_regular_order
from tests.test_raygate import raygate_settings


pytestmark = pytest.mark.asyncio


async def _case(session, *, refunded=False):
    actor, product, order = await make_regular_order(session)
    actor.user_role = UserRole.ADMIN
    now = datetime.now(timezone.utc)
    cutover = RecoveryCutover(id=str(uuid4()), checkpoint_sha256="a" * 64, manifest_sha256="b" * 64,
                              created_by_id=actor.id, created_at=now - timedelta(minutes=1), armed_at=now)
    attempt = PaymentAttempt(order_id=order.id, provider="raygate", merchant_trade_no="RECOVERYORDER1", amount=200,
                             created_at=now - timedelta(minutes=2), expires_at=now + timedelta(minutes=5),
                             status=PaymentStatus.REFUND_PENDING if refunded else PaymentStatus.PENDING,
                             provider_trade_no="PROVIDER1" if refunded else None)
    session.add_all([cutover, attempt])
    await session.flush()
    if refunded:
        order.payment_status = PaymentStatus.REFUND_PENDING
        refund = Refund(order_id=order.id, payment_attempt_id=attempt.id, provider="raygate", amount=200,
                        status=RefundStatus.PENDING, reason="退款測試", requested_by_id=actor.id)
        session.add(refund)
        await session.flush()
        refund_event = OutboxEvent(event_type="refund.requested", aggregate_type="refund", aggregate_id=refund.id,
                                   payload={"refund_id": refund.id, "order_id": order.id})
        session.add(refund_event)
        await session.flush()
    else:
        refund = refund_event = None
    reservation = InventoryReservation(order_id=order.id, payment_attempt_id=attempt.id, source_product_id=product.id,
                                       quantity=2, expires_at=attempt.expires_at,
                                       status=ReservationStatus.CONSUMED if refunded else ReservationStatus.ACTIVE)
    product.stock_quantity = 1
    session.add(reservation)
    holds = [("payment_attempt", attempt.id), ("order", order.id)]
    if refund_event:
        holds.append(("outbox", refund_event.id))
    session.add_all(RecoveryHold(cutover_id=cutover.id, scope=scope, reference=reference, evidence_sha256="c" * 64)
                    for scope, reference in holds)
    await session.commit()
    request = RayGateCaptureRequest(cutover_id=cutover.id, checkpoint_sha256=cutover.checkpoint_sha256,
        attempt_id=attempt.id, merchant_trade_no=attempt.merchant_trade_no,
        refund_outbox_id=refund_event.id if refund_event else None)
    signer = Ed25519PrivateKey.generate()
    adapter_settings = raygate_settings(recovery_mode=True)
    settings = make_test_settings(recovery_mode=True, recovery_proof_signer_public_key=signer.public_key().public_bytes_raw().hex(),
        raygate_payment_base_url=adapter_settings.base_url, raygate_payment_store_identifier=adapter_settings.store_identifier,
        raygate_payment_merchant_id=adapter_settings.merchant_id, raygate_payment_terminal_id=adapter_settings.terminal_id)
    record = {
        "order_id": "PROVIDER1", "amount": 200, "pay_type": "linepay", "return_code": "0000", "message": "測試",
        "transaction_time": now.astimezone(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M:%S"),
        "store_name": "測試", "store_code": adapter_settings.store_identifier, "status": 3 if refunded else 2,
        "associated_order_id": "REFUND1" if refunded else "", "pos_order_number": attempt.merchant_trade_no,
    }
    calls = []

    async def transport(url, body, headers, timeout):
        calls.append(url)
        assert url == f"{adapter_settings.base_url}/api/query/{adapter_settings.store_identifier}"
        assert adapter.decrypt_transaction(body["TransactionData"]) == {"pos_order_number": request.merchant_trade_no}
        assert headers["X-ePay-MerchantID"] == adapter_settings.merchant_id
        return HTTPResponse(200, json.dumps({"ErrorCode": "0000", "Data": [record]}), {})

    adapter = RayGateAdapter(adapter_settings, transport=transport)
    return actor, product, order, attempt, reservation, refund, refund_event, request, signer, adapter, settings, record, calls


async def _count(session, model):
    return await session.scalar(select(func.count()).select_from(model))


async def test_signed_paid_proof_applies_once_preserves_invoice_hold_and_never_calls_provider(database_session):
    session = database_session
    actor, product, order, attempt, reservation, _, _, request, signer, adapter, settings, _, calls = await _case(session)
    proof = await capture_raygate_proof(request, adapter, signer)
    async with session.begin():
        assert await apply_raygate_proof(session, actor, settings, proof) == {"applied": True, "status": "paid"}
    await session.refresh(reservation)
    await session.refresh(product)
    assert (attempt.status, order.payment_status, reservation.status, product.stock_quantity) == (
        PaymentStatus.PAID, PaymentStatus.PAID, ReservationStatus.CONSUMED, 1,
    )
    assert await _count(session, ExternalEvent) == 1
    events = list(await session.scalars(select(OutboxEvent)))
    assert [event.event_type for event in events] == ["invoice.issue_requested"]
    assert await session.get(RecoveryHold, (str(request.cutover_id), "outbox", events[0].id))
    assert await _count(session, AdminAudit) == 1
    await session.commit()

    async with session.begin():
        assert await apply_raygate_proof(session, actor, settings, proof) == {"applied": False, "status": "paid"}
    assert await _count(session, AdminAudit) == 1
    assert await _count(session, ExternalEvent) == 1
    assert len(calls) == 1
    with pytest.raises(RecoveryHoldActive):
        await SQLAlchemyPaymentCallbackRepository(session).apply_raygate_payment_callback(
            "ordinary-callback", {"MerchantTradeNo": request.merchant_trade_no}
        )
    payment_resolution = Resolution(cutover_id=request.cutover_id, target=Item(scope="payment_attempt", reference=attempt.id),
                                    outcome="reconciled", evidence_sha256="d" * 64)
    assert await resolve_hold(session, actor, payment_resolution)
    with pytest.raises(ValueError, match="先完成關聯工作"):
        await resolve_hold(session, actor, payment_resolution.model_copy(update={"target": Item(scope="order", reference=order.id)}))


async def test_refunded_proof_closes_original_outbox_without_resending_refund(database_session):
    session = database_session
    actor, product, order, attempt, reservation, refund, event, request, signer, adapter, settings, _, calls = await _case(session, refunded=True)
    proof = await capture_raygate_proof(request, adapter, signer)
    async with session.begin():
        assert await apply_raygate_proof(session, actor, settings, proof) == {"applied": True, "status": "refunded"}
    assert (attempt.status, order.payment_status, refund.status, refund.provider_refund_id, event.status) == (
        PaymentStatus.REFUNDED, PaymentStatus.REFUNDED, RefundStatus.COMPLETED, "REFUND1", OutboxStatus.COMPLETED,
    )
    assert await _count(session, OutboxEvent) == 1
    assert len(calls) == 1


@pytest.mark.parametrize("error", [
    "wrong_signer", "tampered", "orphan", "amount", "wrong_account", "wrong_cutover", "non_admin",
    "late_payment", "missing_reservation",
])
async def test_invalid_or_contradictory_proof_preserves_all_holds_and_state(database_session, error):
    session = database_session
    actor, _, order, attempt, reservation, _, _, request, signer, adapter, settings, record, _ = await _case(session)
    if error == "wrong_signer":
        signer = Ed25519PrivateKey.generate()
    elif error == "orphan":
        request = request.model_copy(update={"attempt_id": uuid4()})
    elif error == "amount":
        record["amount"] = 201
    elif error == "wrong_account":
        settings.raygate_payment_terminal_id = "OTHER"
    elif error == "wrong_cutover":
        request = request.model_copy(update={"cutover_id": uuid4()})
    elif error == "non_admin":
        actor.user_role = UserRole.CUSTOMER
        await session.commit()
    elif error == "late_payment":
        attempt.expires_at = attempt.created_at
        await session.commit()
    elif error == "missing_reservation":
        reservation.status = ReservationStatus.RELEASED
        await session.commit()
    proof = await capture_raygate_proof(request, adapter, signer)
    if error == "tampered":
        proof.evidence.record["message"] = "另一個結果"
    expected_error = PermissionError if error == "non_admin" else ValueError
    with pytest.raises(expected_error):
        async with session.begin():
            await apply_raygate_proof(session, actor, settings, proof)
    await session.refresh(attempt)
    await session.refresh(order)
    assert (attempt.status, order.payment_status) == (PaymentStatus.PENDING, PaymentStatus.PENDING)
    assert await _count(session, ExternalEvent) == await _count(session, OutboxEvent) == await _count(session, AdminAudit) == 0
    assert await _count(session, RecoveryHold) == 2


async def test_apply_failure_rolls_back_callback_effects_and_evidence(database_session, monkeypatch):
    session = database_session
    actor, product, order, attempt, reservation, _, _, request, signer, adapter, settings, _, _ = await _case(session)
    proof = await capture_raygate_proof(request, adapter, signer)

    async def fail(*args):
        raise ValueError("合成回滾測試")

    monkeypatch.setattr("app.recovery_payment_proof._hold_invoice_work", fail)
    with pytest.raises(ValueError, match="合成回滾測試"):
        async with session.begin():
            await apply_raygate_proof(session, actor, settings, proof)
    for row in (attempt, order, reservation, product):
        await session.refresh(row)
    assert (attempt.status, order.payment_status, reservation.status, product.stock_quantity) == (
        PaymentStatus.PENDING, PaymentStatus.PENDING, ReservationStatus.ACTIVE, 1,
    )
    assert await _count(session, ExternalEvent) == await _count(session, OutboxEvent) == await _count(session, AdminAudit) == 0


async def test_capture_and_authenticated_offline_operator_file_flow(database_session, monkeypatch, tmp_path):
    session = database_session
    actor, _, _, _, _, _, _, request, signer, adapter, settings, _, calls = await _case(session)
    request_path, key_path, output = (tmp_path / name for name in ("request.json", "capture.pem", "proof.json"))
    request_path.write_text(request.model_dump_json())
    key_path.write_bytes(signer.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ))
    key_path.chmod(0o600)
    monkeypatch.setattr(recovery_capture, "get_settings", lambda: settings)
    monkeypatch.setattr(recovery_capture, "raygate_payment_adapter_from_settings", lambda _settings: adapter)
    await recovery_capture.run(request_path, key_path, output)
    assert output.stat().st_mode & 0o777 == 0o600
    assert len(calls) == 1

    monkeypatch.setattr(recovery_operator, "get_settings", lambda: settings)
    monkeypatch.setattr(recovery_operator, "SessionLocal", async_sessionmaker(session.bind, expire_on_commit=False))
    token = create_token(actor, "access", timedelta(minutes=5), settings)
    result = await recovery_operator.run(Namespace(action="raygate-proof", input=output), token)
    assert result == {"applied": True, "status": "paid"}
    assert len(calls) == 1


async def test_earlier_signed_observation_cannot_overwrite_recovered_payment(database_session):
    session = database_session
    actor, _, _, _, _, _, _, request, signer, adapter, settings, _, _ = await _case(session)
    proof = await capture_raygate_proof(request, adapter, signer)
    async with session.begin():
        await apply_raygate_proof(session, actor, settings, proof)
    earlier = proof.evidence.model_copy(update={"observed_at": proof.evidence.observed_at - timedelta(microseconds=1)})
    stale = proof.model_copy(update={"evidence": earlier, "signature": signer.sign(_signed_bytes(earlier)).hex()})
    with pytest.raises(ValueError, match="早於已補登"):
        async with session.begin():
            await apply_raygate_proof(session, actor, settings, stale)
    assert await _count(session, AdminAudit) == 1
    assert await _count(session, ExternalEvent) == 1
