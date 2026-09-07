import hashlib

import pytest

from app.routers.membership import get_document_storage
from tests.support import auth_headers
from tests.test_v2_routers import RecordingDocumentStorage, v2_context

pytestmark = pytest.mark.asyncio


async def test_documents_are_private_and_deleted_documents_cannot_be_downloaded(v2_context):
    client = v2_context["client"]
    owner = auth_headers(v2_context["applicant"])
    outsider = auth_headers(v2_context["customer_b"])
    admin = auth_headers(v2_context["admin"])
    storage = RecordingDocumentStorage()
    client._transport.app.dependency_overrides[get_document_storage] = lambda: storage
    checksum = hashlib.sha256(b"TEST ONLY - NOT AN ID DOCUMENT").hexdigest()
    upload = await client.post("/v1/membership/documents/upload-url", headers=owner, json={
        "document_type": "id_front", "content_type": "application/pdf",
        "size_bytes": 1024, "checksum_sha256": checksum,
    })
    assert upload.status_code == 201
    document_id = upload.json()["document_id"]
    confirmation = f"/v1/membership/documents/{document_id}/confirm"
    denied = await client.post(confirmation, headers=outsider, json={"checksum_sha256": checksum})
    assert denied.status_code == 404
    confirmed = await client.post(confirmation, headers=owner, json={"checksum_sha256": checksum})
    assert confirmed.status_code == 200
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


@pytest.mark.parametrize("size", [0, 8 * 1024 * 1024 + 1])
async def test_server_rejects_invalid_document_size_even_without_browser_check(v2_context, size):
    client = v2_context["client"]
    storage = RecordingDocumentStorage()
    client._transport.app.dependency_overrides[get_document_storage] = lambda: storage
    response = await client.post("/v1/membership/documents/upload-url", headers=auth_headers(v2_context["applicant"]), json={
        "document_type": "secondary", "content_type": "application/pdf",
        "size_bytes": size, "checksum_sha256": "0" * 64,
    })
    assert response.status_code == 422
    assert storage.tickets == []
