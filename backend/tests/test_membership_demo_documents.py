from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.models import (
    AdminAudit,
    MembershipApplication,
    MembershipApplicationStatus,
    MembershipDocument,
    MembershipDocumentStatus,
    MembershipDocumentType,
    User,
)
from app.routers.membership import get_document_storage
from tests.support import auth_headers, fast_password_hash
from tests.test_v2_routers import RecordingDocumentStorage, v2_context


pytestmark = pytest.mark.asyncio

SEED_CASES = (
    ("application-supplement", "user-applicant-supplement", "id_front", "supplement-front.jpg", 120000, "a" * 64),
    ("application-supplement", "user-applicant-supplement", "id_back", "supplement-back.jpg", 118000, "b" * 64),
    ("application-payment", "user-applicant-payment", "id_front", "payment-id_front.jpg", 125000, "1" * 64),
    ("application-payment", "user-applicant-payment", "id_back", "payment-id_back.jpg", 125000, "2" * 64),
    ("application-payment", "user-applicant-payment", "secondary", "payment-secondary.jpg", 125000, "3" * 64),
)


async def demo_document(context, case=SEED_CASES[0], **changes):
    application_id, user_id, kind, filename, size, checksum = case
    application_id = changes.pop("application_id", application_id)
    user_id = changes.pop("user_id", user_id)
    user = User(
        id=user_id,
        email="demo-applicant@example.test",
        display_name="合成示範申請人",
        password_hash=fast_password_hash("synthetic-password"),
        email_verified_at=datetime.now(timezone.utc),
    )
    application = MembershipApplication(
        id=application_id,
        user=user,
        status=MembershipApplicationStatus.NEEDS_SUPPLEMENT,
    )
    document = MembershipDocument(
        application=application,
        document_type=MembershipDocumentType(changes.pop("document_type", kind)),
        status=MembershipDocumentStatus(changes.pop("status", "confirmed")),
        object_key=changes.pop("object_key", f"membership-documents/demo/{filename}"),
        content_type=changes.pop("content_type", "image/jpeg"),
        size_bytes=changes.pop("size_bytes", size),
        checksum_sha256=changes.pop("checksum_sha256", checksum),
        confirmed_at=datetime.now(timezone.utc),
    )
    assert not changes
    context["session"].add(document)
    await context["session"].commit()
    return user, application, document


@pytest.mark.parametrize("case", SEED_CASES)
async def test_exact_seed_attachment_is_rejected_before_signing_download(v2_context, case):
    _, application, document = await demo_document(v2_context, case)
    storage = RecordingDocumentStorage()
    client = v2_context["client"]
    client._transport.app.dependency_overrides[get_document_storage] = lambda: storage

    response = await client.get(
        f"/v1/admin/membership-applications/{application.id}/documents/{document.id}/download-url",
        headers=auth_headers(v2_context["admin"]),
    )

    assert response.status_code == 409, response.text
    assert response.json() == {"detail": "示範資料，無實際檔案"}
    assert response.headers["x-error-code"] == "demo_document_placeholder"
    assert storage.downloaded == []
    assert await v2_context["session"].scalar(
        select(func.count(AdminAudit.id)).where(AdminAudit.action == "membership.view_document")
    ) == 0


@pytest.mark.parametrize("case", SEED_CASES)
async def test_placeholder_marker_is_derived_in_existing_authorized_responses(v2_context, case):
    user, application, _ = await demo_document(v2_context, case)
    client = v2_context["client"]
    for path, actor, nested in [
        ("/v1/membership/documents", user, False),
        ("/v1/membership/application", user, True),
        (f"/v1/admin/membership-applications/{application.id}", v2_context["admin"], True),
    ]:
        response = await client.get(path, headers=auth_headers(actor))
        assert response.status_code == 200, response.text
        documents = response.json()["documents"] if nested else response.json()
        assert len(documents) == 1
        assert documents[0]["is_demo_placeholder"] is True
        assert "object_key" not in documents[0]


@pytest.mark.parametrize("changes,expected_status", [
    ({"object_key": "membership-documents/demo/not-a-seed.jpg"}, 200),
    ({"object_key": "membership-documents/verified/reuploaded.jpg"}, 200),
    ({"content_type": "image/png"}, 200),
    ({"size_bytes": 120001}, 200),
    ({"checksum_sha256": "b" * 64}, 200),
    ({"checksum_sha256": None}, 200),
    ({"application_id": "different-application"}, 200),
    ({"user_id": "different-applicant"}, 200),
    ({"document_type": "secondary"}, 200),
    ({"status": "pending_upload"}, 404),
])
async def test_any_tuple_or_relationship_difference_preserves_existing_download_behavior(
    v2_context, changes, expected_status,
):
    user, application, document = await demo_document(v2_context, **changes)
    storage = RecordingDocumentStorage()
    client = v2_context["client"]
    client._transport.app.dependency_overrides[get_document_storage] = lambda: storage
    listed = await client.get("/v1/membership/documents", headers=auth_headers(user))
    assert listed.status_code == 200
    assert listed.json()[0]["is_demo_placeholder"] is False

    response = await client.get(
        f"/v1/admin/membership-applications/{application.id}/documents/{document.id}/download-url",
        headers=auth_headers(v2_context["admin"]),
    )

    assert response.status_code == expected_status, response.text
    assert "demo_document_placeholder" not in response.headers.values()
    if expected_status == 200:
        assert response.json() == {
            "download_url": f"https://r2.example.test/{document.object_key}?download-signed=1",
            "expires_in_seconds": 120,
        }
        assert storage.downloaded == [document.object_key]
    else:
        assert response.json() == {"detail": "找不到證件"}
        assert storage.downloaded == []
    assert await v2_context["session"].scalar(
        select(func.count(AdminAudit.id)).where(AdminAudit.action == "membership.view_document")
    ) == (1 if expected_status == 200 else 0)


@pytest.mark.parametrize("application_status", ["draft", "approved", "withdrawn"])
async def test_application_workflow_changes_do_not_turn_placeholder_into_real_file(
    v2_context, application_status,
):
    _, application, document = await demo_document(v2_context)
    application.status = MembershipApplicationStatus(application_status)
    await v2_context["session"].commit()
    storage = RecordingDocumentStorage()
    client = v2_context["client"]
    client._transport.app.dependency_overrides[get_document_storage] = lambda: storage

    response = await client.get(
        f"/v1/admin/membership-applications/{application.id}/documents/{document.id}/download-url",
        headers=auth_headers(v2_context["admin"]),
    )

    assert response.status_code == 409
    assert storage.downloaded == []


async def test_authentication_object_scope_and_deleted_status_precede_placeholder_response(v2_context):
    user, application, document = await demo_document(v2_context)
    client = v2_context["client"]
    storage = RecordingDocumentStorage()
    client._transport.app.dependency_overrides[get_document_storage] = lambda: storage
    path = f"/v1/admin/membership-applications/{application.id}/documents/{document.id}/download-url"

    assert (await client.get(path)).status_code == 401
    for actor in [user, v2_context["customer_b"]]:
        response = await client.get(path, headers=auth_headers(actor))
        assert response.status_code == 403
        assert "示範資料" not in response.text
    admin = auth_headers(v2_context["admin"])
    wrong_application = await client.get(path.replace(application.id, "unrelated"), headers=admin)
    assert wrong_application.status_code == 404
    document.status = MembershipDocumentStatus.DELETED
    document.deleted_at = datetime.now(timezone.utc)
    await v2_context["session"].commit()
    assert (await client.get(path, headers=admin)).status_code == 404
    assert storage.downloaded == []


async def test_seed_preserves_the_five_original_document_tuples(database_session):
    from app.seed import seed_demo_data

    await seed_demo_data(database_session)
    rows = (
        await database_session.execute(
            select(MembershipDocument, MembershipApplication.user_id)
            .join(MembershipApplication)
        )
    ).all()

    assert sorted(
        (
            document.application_id, user_id, document.document_type.value,
            document.object_key, document.content_type, document.size_bytes,
            document.checksum_sha256, document.status.value,
        )
        for document, user_id in rows
    ) == sorted(
        (app, user, kind, f"membership-documents/demo/{filename}", "image/jpeg", size, checksum, "confirmed")
        for app, user, kind, filename, size, checksum in SEED_CASES
    )
