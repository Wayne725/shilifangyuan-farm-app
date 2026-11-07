from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class DemoMembershipDocument:
    application_id: str
    user_id: str
    document_type: str
    object_key: str
    content_type: str
    size_bytes: int
    checksum_sha256: str
    status: str = "confirmed"


DEMO_MEMBERSHIP_DOCUMENTS = (
    DemoMembershipDocument(
        "application-supplement", "user-applicant-supplement", "id_front",
        "membership-documents/demo/supplement-front.jpg", "image/jpeg", 120000, "a" * 64,
    ),
    DemoMembershipDocument(
        "application-supplement", "user-applicant-supplement", "id_back",
        "membership-documents/demo/supplement-back.jpg", "image/jpeg", 118000, "b" * 64,
    ),
    DemoMembershipDocument(
        "application-payment", "user-applicant-payment", "id_front",
        "membership-documents/demo/payment-id_front.jpg", "image/jpeg", 125000, "1" * 64,
    ),
    DemoMembershipDocument(
        "application-payment", "user-applicant-payment", "id_back",
        "membership-documents/demo/payment-id_back.jpg", "image/jpeg", 125000, "2" * 64,
    ),
    DemoMembershipDocument(
        "application-payment", "user-applicant-payment", "secondary",
        "membership-documents/demo/payment-secondary.jpg", "image/jpeg", 125000, "3" * 64,
    ),
)


def is_demo_membership_document(
    *,
    application_id: str,
    user_id: str,
    document_type: str | Enum,
    object_key: str,
    content_type: str,
    size_bytes: int,
    checksum_sha256: str | None,
    status: str | Enum,
) -> bool:
    if checksum_sha256 is None:
        return False
    candidate = DemoMembershipDocument(
        application_id=application_id,
        user_id=user_id,
        document_type=document_type.value if isinstance(document_type, Enum) else document_type,
        object_key=object_key,
        content_type=content_type,
        size_bytes=size_bytes,
        checksum_sha256=checksum_sha256,
        status=status.value if isinstance(status, Enum) else status,
    )
    return candidate in DEMO_MEMBERSHIP_DOCUMENTS
