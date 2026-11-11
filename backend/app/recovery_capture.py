from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import stat

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from pydantic import ValidationError

from .config import get_settings
from .integrations.common import IntegrationError
from .integrations.payment_service import raygate_payment_adapter_from_settings
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


async def run(request_path: Path, signer_path: Path, output: Path) -> None:
    settings = get_settings()
    if not settings.recovery_mode:
        raise ValueError("請在雷門唯讀擷取模式執行")
    with request_path.open("rb") as handle:
        raw = handle.read(MAX_PROOF_BYTES + 1)
    if len(raw) > MAX_PROOF_BYTES:
        raise ValueError("擷取需求檔案過大")
    request = RayGateCaptureRequest.model_validate_json(raw)
    proof = await capture_raygate_proof(request, raygate_payment_adapter_from_settings(settings), _load_signer(signer_path))
    _write_private_json(output, proof.model_dump(mode="json"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("request", type=Path)
    parser.add_argument("signer", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        asyncio.run(run(args.request, args.signer, args.output))
    except (IntegrationError, OSError, TypeError, ValidationError, ValueError):
        parser.exit(1, "無法擷取雷門對帳證明，請核對查單權限、擷取需求與簽署設定。\n")
    print("對帳證明已儲存")


if __name__ == "__main__":
    main()
