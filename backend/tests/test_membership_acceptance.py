from datetime import date

import pytest
from sqlalchemy import select

from app.models import MemberProfile, MembershipChargeKind, MembershipFeeSchedule, MembershipDocument
from app.integrations.pii_crypto import pii_cipher_from_settings
from app.routers.membership import get_document_storage
from tests.membership_support import seed_legacy_document
from tests.support import auth_headers
from tests.test_v2_routers import RecordingDocumentStorage, settings, v2_context

pytestmark = pytest.mark.asyncio


async def test_documents_are_private_and_deleted_documents_cannot_be_downloaded(v2_context):
    client = v2_context["client"]
    owner = auth_headers(v2_context["applicant"])
    outsider = auth_headers(v2_context["customer_b"])
    admin = auth_headers(v2_context["admin"])
    storage = RecordingDocumentStorage()
    client._transport.app.dependency_overrides[get_document_storage] = lambda: storage
    document = await seed_legacy_document(v2_context, storage)
    document_id = document.id
    confirmation = f"/v1/membership/documents/{document_id}/confirm"
    for headers in (owner, outsider):
        assert (await client.post(confirmation, headers=headers, json={"checksum_sha256": "a" * 64})).status_code == 410
    application = (await client.get("/v1/membership/application", headers=owner)).json()
    download = f"/v1/admin/membership-applications/{application['id']}/documents/{document_id}/download-url"
    for headers in (owner, outsider):
        response = await client.get(download, headers=headers)
        assert response.status_code == 403
        assert "download_url" not in response.text
        private = await client.get(f"/v1/admin/membership-applications/{application['id']}", headers=headers)
        assert private.status_code == 403
    other_documents = await client.get("/v1/membership/documents", headers=outsider)
    assert other_documents.json() == []
    allowed = await client.get(download, headers=admin)
    assert allowed.status_code == 200
    assert allowed.json()["expires_in_seconds"] == 120
    mismatched = await client.get(download.replace(application["id"], "unrelated-application"), headers=admin)
    assert mismatched.status_code == 404
    deleted = await client.delete(f"/v1/membership/documents/{document_id}", headers=owner)
    assert deleted.status_code == 204
    assert (await client.get(download, headers=admin)).status_code == 404
    assert (await client.get("/v1/membership/documents", headers=owner)).json() == []


@pytest.mark.parametrize("kind", ["id_front", "id_back", "secondary"])
async def test_server_rejects_all_new_document_intake(v2_context, kind):
    client = v2_context["client"]
    storage = RecordingDocumentStorage()
    client._transport.app.dependency_overrides[get_document_storage] = lambda: storage
    response = await client.post("/v1/membership/documents/upload-url", headers=auth_headers(v2_context["applicant"]), json={
        "document_type": kind, "content_type": "application/pdf",
        "size_bytes": 100, "checksum_sha256": "0" * 64,
    })
    assert response.status_code == 410
    assert storage.tickets == []
    assert (await v2_context["session"].scalars(select(MembershipDocument))).all() == []


PROFILE = {
    "legal_name": "隔離測試申請人", "phone": "0900000000", "birth_date": "1995-01-01",
    "address": "測試地址", "emergency_contact": "測試聯絡人", "consent_version": "test",
}


async def test_no_identity_number_collected_or_returned_and_existing_ciphertext_preserved(v2_context):
    client, session = v2_context["client"], v2_context["session"]
    headers = auth_headers(v2_context["applicant"])
    rejected = await client.put("/v1/membership/application", headers=headers, json={**PROFILE, "identity_number": "A123456789"})
    assert rejected.status_code == 422
    assert await session.scalar(select(MemberProfile)) is None
    created = await client.put("/v1/membership/application", headers=headers, json=PROFILE)
    assert created.status_code == 200, created.text
    assert "identity_number" not in created.json()["profile"]
    saved = await session.scalar(select(MemberProfile))
    cipher = pii_cipher_from_settings(settings())
    old_value = cipher.encrypt_text("A123456789", associated_data=f"member-profile:{v2_context['applicant'].id}")
    saved.identity_number_encrypted = old_value
    await session.commit()
    updated = await client.put("/v1/membership/application", headers=headers, json={**PROFILE, "legal_name": "基本資料更新", "identity_number": None})
    assert updated.status_code == 200, updated.text
    assert "identity_number" not in updated.json()["profile"]
    await session.refresh(saved)
    assert saved.identity_number_encrypted == old_value


async def test_document_free_submission_preserves_fees_and_admin_review(v2_context):
    client, session = v2_context["client"], v2_context["session"]
    for kind, amount in ((MembershipChargeKind.ADMISSION_FEE, 500), (MembershipChargeKind.SHARE_CAPITAL, 1000)):
        session.add(MembershipFeeSchedule(charge_kind=kind, amount=amount, effective_from=date(2026, 1, 1)))
    await session.commit()
    headers = auth_headers(v2_context["applicant"])
    missing = await client.post("/v1/membership/application/submit", headers=headers)
    assert missing.status_code == 409
    saved = await client.put("/v1/membership/application", headers=headers, json=PROFILE)
    assert saved.status_code == 200
    submitted = await client.post("/v1/membership/application/submit", headers=headers)
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["documents"] == []
    charges = (await client.get("/v1/membership/charges", headers=headers)).json()
    assert {(charge["charge_kind"], charge["amount"], charge["status"]) for charge in charges} == {
        ("admission_fee", 500, "pending"), ("share_capital", 1000, "pending"),
    }
    path = f"/v1/admin/membership-applications/{saved.json()['id']}/approve"
    assert (await client.post(path, headers=headers, json={})).status_code == 403
    approved = await client.post(path, headers=auth_headers(v2_context["admin"]), json={})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "pending_payment"


async def test_member_profile_hides_share_holdings_but_admin_retains_records(v2_context):
    member = v2_context["member_a"]
    member.membership.share_capital_amount = 5000
    member.membership.share_count = 5
    member.membership.share_certificate_number = "LEGACY-CERT"
    member.membership.share_subscribed_on = date(2025, 1, 1)
    member.membership.share_paid_on = date(2025, 1, 2)
    await v2_context["session"].commit()
    response = await v2_context["client"].get("/v1/members/me", headers=auth_headers(member))
    assert response.status_code == 200
    assert not any(key.startswith("share_") for key in response.json()["membership"])
    assert response.json()["membership"]["member_number"] == member.membership.member_number
    admin = await v2_context["client"].get("/v1/admin/members", headers=auth_headers(v2_context["admin"]))
    assert admin.status_code == 200, admin.text
    record = next(item for item in admin.json() if item["id"] == member.membership.id)
    assert record["share_capital_amount"] == 5000
    assert record["share_count"] == 5
    assert member.membership.share_certificate_number == "LEGACY-CERT"
