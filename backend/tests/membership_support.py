from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import MembershipApplication, MembershipDocument, MembershipDocumentStatus, MembershipDocumentType
from app.routers.membership import get_document_storage


async def seed_legacy_document(context, storage, kind="id_front", *, confirm=True):
    """Seed pre-policy files directly; the production upload routes stay disabled."""
    session = context["session"]
    context["client"]._transport.app.dependency_overrides[get_document_storage] = lambda: storage
    application = await session.scalar(select(MembershipApplication).where(
        MembershipApplication.user_id == context["applicant"].id,
    ))
    if application is None:
        application = MembershipApplication(user_id=context["applicant"].id)
        session.add(application)
        await session.flush()
    now = datetime.now(timezone.utc)
    document = MembershipDocument(
        application_id=application.id, document_type=MembershipDocumentType(kind),
        object_key=f"membership-documents/{'verified' if confirm else 'pending'}/legacy/{application.id}-{kind}.pdf",
        content_type="application/pdf", size_bytes=100, checksum_sha256="a" * 64,
        status=MembershipDocumentStatus.CONFIRMED if confirm else MembershipDocumentStatus.PENDING_UPLOAD,
        confirmed_at=now if confirm else None,
        expires_at=now + timedelta(days=14 if confirm else 1),
    )
    session.add(document)
    await session.commit()
    storage.stored[document.object_key] = {
        "content_type": document.content_type, "content_length": document.size_bytes,
        "sha256": document.checksum_sha256,
    }
    return document
