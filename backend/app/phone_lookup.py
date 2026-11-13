from .integrations.pii_crypto import VersionedPIICipher
from .member_claims import normalize_phone


def phone_lookup_digest(phone: str, cipher: VersionedPIICipher) -> str | None:
    if not 8 <= len(normalize_phone(phone)) <= 15:
        return None
    return phone_lookup_digests(phone, cipher)[cipher.current_version]


def phone_lookup_digests(
    phone: str, cipher: VersionedPIICipher, *, purpose: str = "member-phone",
) -> dict[str, str]:
    return cipher.lookup_digests(normalize_phone(phone), purpose=purpose)


def mask_phone(phone: str) -> str:
    digits = normalize_phone(phone)
    return f"{'*' * max(0, len(digits) - 4)}{digits[-4:]}"
