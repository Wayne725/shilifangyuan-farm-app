from argparse import Namespace
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs
from uuid import UUID, uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import recovery_capture, recovery_operator
from app.auth import create_token
from app.integrations.feie import FeieClient, FeieError, configured_region
from app.models import AdminAudit, PrintJob, RecoveryCutover, RecoveryHold, UserRole
from app.printing import printer_fingerprint
from app.recovery_print_proof import _signed_bytes, apply_feie_proof, capture_feie_proof, prepare_feie_request
from app.recovery_quarantine import Item, Resolution, resolve_hold
from tests.test_feie_printing import live_settings
from tests.test_integrations import make_regular_order


pytestmark = pytest.mark.asyncio


async def _case(session, *, summary=False):
    actor, _, order = await make_regular_order(session)
    actor.user_role = UserRole.ADMIN
    signer = Ed25519PrivateKey.generate()
    settings = live_settings(recovery_mode=True, recovery_proof_signer_public_key=signer.public_key().public_bytes_raw().hex())
    now = datetime.now(timezone.utc)
    cutover = RecoveryCutover(
        id=str(uuid4()), created_by_id=actor.id, checkpoint_sha256="a" * 64, manifest_sha256="b" * 64,
        created_at=now - timedelta(minutes=1), armed_at=now,
    )
    if summary:
        request_id = str(uuid4())
        snapshot = {"kind": "summary", "request_id": request_id, "page": 1, "page_count": 2,
                    "fingerprint": "c" * 64, "source_versions": [{"id": order.id, "revision": order.revision}]}
        event_key = f"summary:{request_id}:01"
    else:
        snapshot = {"kind": "order", "order_number": order.order_number, "revision": order.revision}
        event_key = f"order:{order.id}:initial"
    job = PrintJob(
        kind="summary" if summary else "order", mode="live", status="unknown" if summary else "submitted",
        event_key=event_key, order_id=None if summary else order.id, printer_fingerprint=printer_fingerprint(settings),
        snapshot={**snapshot, "printer_region": configured_region(settings), "recipient": "PRIVATE NAME"},
        content="PRIVATE RECEIPT CONTENT", vendor_order_id="FEIE-VERIFIED-1",
        created_at=now - timedelta(minutes=3), submitted_at=now - timedelta(minutes=2), attempts=1,
        lease_token=str(uuid4()), lease_until=now + timedelta(minutes=1), last_error="result_unconfirmed",
    )
    session.add_all([cutover, job])
    await session.flush()
    session.add(RecoveryHold(cutover_id=cutover.id, scope="print_job", reference=job.id, evidence_sha256="d" * 64))
    await session.commit()
    responses = {"ret": 0, "data": True}
    calls = []

    def handler(request):
        body = parse_qs(request.content.decode())
        calls.append((str(request.url), body))
        assert request.method == "POST"
        assert str(request.url) == f"{settings.feie_api_base_url}queryOrderState"
        assert body["apiname"] == ["Open_queryOrderState"]
        assert "content" not in body
        return httpx.Response(200, json=responses)

    client = FeieClient(settings, transport=httpx.MockTransport(handler))
    return actor, order, job, cutover, signer, settings, client, responses, calls


async def _count(session, model):
    return await session.scalar(select(func.count()).select_from(model))


@pytest.mark.parametrize("summary", [False, True])
async def test_proven_feie_print_closes_only_exact_job_without_new_print(database_session, summary):
    session = database_session
    actor, _, job, cutover, signer, settings, client, _, calls = await _case(session, summary=summary)
    sibling = None
    if summary:
        sibling = PrintJob(
            kind="summary", mode="live", status="submitted",
            event_key=f"summary:{job.snapshot['request_id']}:02",
            printer_fingerprint=job.printer_fingerprint, snapshot={**job.snapshot, "page": 2},
            content="PRIVATE OTHER PAGE", vendor_order_id="FEIE-SECOND-2", submitted_at=job.submitted_at, attempts=1,
        )
        session.add(sibling)
        await session.commit()
    request = await prepare_feie_request(session, actor, UUID(cutover.id), UUID(job.id))
    proof = await capture_feie_proof(request, client, signer)
    assert len(calls) == 1 and calls[0][1]["orderid"] == [job.vendor_order_id]
    encoded = proof.model_dump_json()
    assert all(value not in encoded for value in ("PRIVATE", "sig=", "test-user", "test-device"))
    await session.commit()

    async with session.begin():
        assert await apply_feie_proof(session, actor, settings, proof) == {"applied": True, "status": "printed"}
    assert job.status == "printed" and job.printed_at == proof.evidence.observed_at
    assert job.lease_token is None and job.last_error is None and job.attempts == 1
    if sibling:
        assert sibling.status == "submitted"
    async with session.begin():
        assert await apply_feie_proof(session, actor, settings, proof) == {"applied": False, "status": "printed"}
    assert len(calls) == 1
    assert await _count(session, AdminAudit) == 1
    assert await _count(session, PrintJob) == (2 if summary else 1)
    assert await resolve_hold(session, actor, Resolution(
        cutover_id=UUID(cutover.id), target=Item(scope="print_job", reference=job.id),
        outcome="reconciled", evidence_sha256="e" * 64,
    ))


@pytest.mark.parametrize("cause", [
    "missing_vendor_id", "duplicate_vendor_id", "missing_submission", "missing_region", "dry_run",
    "bad_event_key", "missing_content", "unknown_summary_source", "wrong_summary_page", "manual_origin",
])
async def test_request_export_refuses_unbound_or_ambiguous_job(database_session, cause):
    session = database_session
    actor, _, job, cutover, signer, settings, client, _, calls = await _case(session, summary="summary" in cause)
    if cause == "missing_vendor_id":
        job.vendor_order_id = None
    elif cause == "duplicate_vendor_id":
        session.add(PrintJob(
            kind=job.kind, mode="live", order_id=job.order_id, status="submitted", event_key="duplicate-record",
            printer_fingerprint=job.printer_fingerprint, snapshot=job.snapshot, vendor_order_id=job.vendor_order_id,
        ))
    elif cause == "missing_submission":
        job.submitted_at = None
    elif cause == "missing_region":
        job.snapshot = {key: value for key, value in job.snapshot.items() if key != "printer_region"}
    elif cause == "dry_run":
        job.mode = "dry_run"
    elif cause == "bad_event_key":
        job.event_key = "unknown-local-lineage"
    elif cause == "missing_content":
        job.content = None
    elif cause == "unknown_summary_source":
        job.snapshot = {**job.snapshot, "source_versions": []}
    elif cause == "wrong_summary_page":
        job.snapshot = {**job.snapshot, "page": 2}
    elif cause == "manual_origin":
        job.snapshot = {**job.snapshot, "manual": True}
    await session.commit()
    with pytest.raises(ValueError):
        await prepare_feie_request(session, actor, UUID(cutover.id), UUID(job.id))
    assert job.status in {"submitted", "unknown"} and not calls


@pytest.mark.parametrize("result", ["pending", "not_found", "malformed", "wrong_region", "wrong_account"])
async def test_only_vendor_true_can_produce_signed_print_proof(database_session, result):
    session = database_session
    actor, _, job, cutover, signer, settings, client, response, calls = await _case(session)
    request = await prepare_feie_request(session, actor, UUID(cutover.id), UUID(job.id))
    if result == "pending":
        response["data"] = False
    elif result == "not_found":
        response.update(ret=-999, msg="synthetic vendor has no matching order")
    elif result == "malformed":
        response["data"] = "true"
    elif result == "wrong_region":
        request = request.model_copy(update={"printer_region": "cn" if request.printer_region == "jp" else "jp"})
    else:
        request = request.model_copy(update={"printer_fingerprint": "0" * 64})
    with pytest.raises((FeieError, ValueError)):
        await capture_feie_proof(request, client, signer)
    assert len(calls) == (0 if result.startswith("wrong_") else 1)
    assert job.status == "submitted"


@pytest.mark.parametrize("cause", [
    "wrong_signer", "edited_proof", "different_vendor_id", "changed_content", "resolved_hold", "signed_false",
    "before_submission",
])
async def test_offline_import_refuses_modified_or_unheld_binding(database_session, cause):
    session = database_session
    actor, _, job, cutover, signer, settings, client, _, _ = await _case(session)
    request = await prepare_feie_request(session, actor, UUID(cutover.id), UUID(job.id))
    proof = await capture_feie_proof(request, client, Ed25519PrivateKey.generate() if cause == "wrong_signer" else signer)
    if cause == "edited_proof":
        proof.evidence.request.job_binding_sha256 = "0" * 64
    elif cause == "different_vendor_id":
        job.vendor_order_id = "ANOTHER-ID"
    elif cause == "changed_content":
        job.content = "DIFFERENT CONTENT"
    elif cause == "resolved_hold":
        hold = await session.get(RecoveryHold, (cutover.id, "print_job", job.id))
        hold.resolved_at = datetime.now(timezone.utc)
    elif cause in {"signed_false", "before_submission"}:
        evidence = proof.evidence.model_copy(update={
            "printed": cause != "signed_false",
            "observed_at": cutover.created_at - timedelta(minutes=2)
                if cause == "before_submission" else proof.evidence.observed_at,
        })
        proof = proof.model_copy(update={"evidence": evidence, "signature": signer.sign(_signed_bytes(evidence)).hex()})
    await session.commit()
    with pytest.raises(ValueError):
        async with session.begin():
            await apply_feie_proof(session, actor, settings, proof)
    await session.refresh(job)
    assert job.status == "submitted" and job.printed_at is None
    assert await _count(session, AdminAudit) == 0


async def test_authenticated_private_export_query_and_import_flow(database_session, monkeypatch, tmp_path):
    session = database_session
    actor, _, job, cutover, signer, settings, client, _, calls = await _case(session)
    request_path, key_path, proof_path = (tmp_path / name for name in ("request.json", "capture.pem", "proof.json"))
    key_path.write_bytes(signer.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ))
    key_path.chmod(0o600)
    monkeypatch.setattr(recovery_operator, "get_settings", lambda: settings)
    monkeypatch.setattr(recovery_operator, "SessionLocal", async_sessionmaker(session.bind, expire_on_commit=False))
    token = create_token(actor, "access", timedelta(minutes=5), settings)
    assert await recovery_operator.run(Namespace(
        action="feie-request", cutover_id=UUID(cutover.id), job_id=UUID(job.id), output=request_path,
    ), token) == {"created": True}
    assert request_path.stat().st_mode & 0o777 == 0o600
    monkeypatch.setattr(recovery_capture, "get_settings", lambda: settings)
    monkeypatch.setattr(recovery_capture, "FeieClient", lambda _settings: client)
    await recovery_capture.run(request_path, key_path, proof_path, provider="feie")
    assert proof_path.stat().st_mode & 0o777 == 0o600
    assert await recovery_operator.run(Namespace(action="feie-proof", input=proof_path), token) == {
        "applied": True, "status": "printed",
    }
    assert len(calls) == 1
