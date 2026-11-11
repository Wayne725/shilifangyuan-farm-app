from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import stat
from typing import Literal

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from pydantic import ValidationError

from .config import get_settings
from .integrations.common import IntegrationError
from .integrations.fanyu_invoice import FanyuInvoiceAdapter
from .integrations.feie import FeieClient, FeieError
from .integrations.invoice_service import invoice_adapter_from_settings
from .integrations.payment_service import raygate_payment_adapter_from_settings
from .recovery_invoice_proof import FanyuCaptureRequest, capture_fanyu_proof
from .recovery_print_proof import FeieCaptureRequest, capture_feie_proof
from .recovery_operator import _write_private_json
from .recovery_payment_proof import MAX_PROOF_BYTES, RayGateCaptureRequest, capture_raygate_proof


def _load_signer(path: Path) -> Ed25519PrivateKey:
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as handle:
        metadata = os.fstat(handle.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
            raise ValueError("請使用擷取者本人專用的簽署金鑰檔")
        key = load_pem_private_key(handle.read(MAX_PROOF_BYTES), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("對帳擷取需使用 Ed25519 簽署金鑰")
    return key


async def run(
    request_path: Path, signer_path: Path, output: Path, *, provider: Literal["raygate", "fanyu", "feie"] = "raygate",
) -> None:
    settings = get_settings()
    if not settings.recovery_mode:
        raise ValueError("請在唯讀擷取模式執行")
    with request_path.open("rb") as handle:
        raw = handle.read(MAX_PROOF_BYTES + 1)
    if len(raw) > MAX_PROOF_BYTES:
        raise ValueError("擷取需求檔案過大")
    signer = _load_signer(signer_path)
    if provider == "fanyu":
        adapter = invoice_adapter_from_settings(settings)
        if not isinstance(adapter, FanyuInvoiceAdapter):
            raise ValueError("請設定原汎宇發票帳戶")
        proof = await capture_fanyu_proof(FanyuCaptureRequest.model_validate_json(raw), adapter, signer)
    elif provider == "feie":
        proof = await capture_feie_proof(FeieCaptureRequest.model_validate_json(raw), FeieClient(settings), signer)
    else:
        proof = await capture_raygate_proof(
            RayGateCaptureRequest.model_validate_json(raw), raygate_payment_adapter_from_settings(settings), signer,
        )
    _write_private_json(output, proof.model_dump(mode="json"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("request", type=Path)
    parser.add_argument("signer", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--provider", choices=("raygate", "fanyu", "feie"), default="raygate")
    args = parser.parse_args()
    try:
        asyncio.run(run(args.request, args.signer, args.output, provider=args.provider))
    except (FeieError, IntegrationError, OSError, TypeError, ValidationError, ValueError):
        parser.exit(1, "無法擷取對帳證明，請核對查單權限、擷取需求與簽署設定。\n")
    print("對帳證明已儲存")


if __name__ == "__main__":
    main()
