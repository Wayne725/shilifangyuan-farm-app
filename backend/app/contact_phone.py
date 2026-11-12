import re

from .config import Settings
from .integrations.common import IntegrationError
from .integrations.pii_crypto import pii_cipher_from_settings


def normalize_contact_phone(value: str) -> str:
    phone = re.sub(r"\s", "", value)
    pattern = r"09[0-9]{8}" if phone.startswith("09") else r"0[0-9]{1,2}-?[0-9]{6,8}"
    if re.fullmatch(pattern, phone) is None:
        raise ValueError("手機需為 09 開頭的 10 碼數字；市話請輸入含區碼的完整號碼")
    return phone


def decrypt_contact_phone(ciphertext: str | None, aad: str, settings: Settings) -> str | None:
    if not ciphertext:
        return None
    try:
        return pii_cipher_from_settings(settings).decrypt_text(ciphertext, associated_data=aad).strip() or None
    except IntegrationError:
        return None
