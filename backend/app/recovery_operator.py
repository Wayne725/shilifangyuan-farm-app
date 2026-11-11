from __future__ import annotations

import argparse
import asyncio
from getpass import getpass
import json
import os
from pathlib import Path
import sys
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import decode_token, get_active_user_for_access
from .config import get_settings
from .database import SessionLocal
from .recovery_invoice_proof import SignedFanyuEvidence, apply_fanyu_proof
from .recovery_payment_proof import MAX_PROOF_BYTES, SignedRayGateEvidence, apply_raygate_proof
from .models import RecoveryCutover, RecoveryHold, User
from .recovery_quarantine import (
    Digest, Manifest, QuarantineInput, Resolution, _require_admin, arm_cutover, begin_cutover,
    inventory, resolve_hold,
)


class Begin(QuarantineInput):
    cutover_id: UUID
    checkpoint_sha256: Digest


async def export_inventory(session: AsyncSession, actor: User, cutover_id: UUID, output: Path) -> int:
    _require_admin(actor)
    cutover = await session.get(RecoveryCutover, str(cutover_id))
    if cutover is None or cutover.armed_at is not None:
        raise ValueError("請先開啟未封存的隔離作業")
    entries = await inventory(session)
    payload = {
        "cutover_id": cutover.id,
        "checkpoint_sha256": cutover.checkpoint_sha256,
        "decisions": [
            {"target": {"scope": scope, "reference": reference}, "disposition": "hold", "evidence_sha256": None}
            for scope, reference in sorted(entries)
        ],
    }
    _write_private_json(output, payload)
    return len(entries)


def _write_private_json(output: Path, payload: dict) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    with os.fdopen(os.open(output, flags, 0o600), "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")


async def export_status(session: AsyncSession, actor: User, cutover_id: UUID, output: Path) -> int:
    _require_admin(actor)
    cutover = await session.get(RecoveryCutover, str(cutover_id))
    if cutover is None:
        raise ValueError("找不到隔離作業")
    rows = list(await session.scalars(select(RecoveryHold).where(RecoveryHold.cutover_id == cutover.id)
                                      .order_by(RecoveryHold.scope, RecoveryHold.reference)))
    _write_private_json(output, {
        "cutover_id": cutover.id,
        "armed": cutover.armed_at is not None,
        "manifest_sha256": cutover.manifest_sha256,
        "holds": [
            {"scope": row.scope, "reference": row.reference, "evidence_sha256": row.evidence_sha256,
             "resolution": row.resolution, "resolution_sha256": row.resolution_sha256}
            for row in rows
        ],
    })
    return len(rows)


async def run(args: argparse.Namespace, token: str) -> dict:
    settings = get_settings()
    if not settings.recovery_mode:
        raise ValueError("請在隔離還原模式執行此命令")
    async with SessionLocal() as session, session.begin():
        actor = await get_active_user_for_access(decode_token(token, "access", settings), session)
        if args.action == "begin":
            request = Begin(cutover_id=args.cutover_id, checkpoint_sha256=args.checkpoint_sha256)
            return {"created": await begin_cutover(session, actor, request.cutover_id, request.checkpoint_sha256)}
        if args.action == "inventory":
            return {"items": await export_inventory(session, actor, args.cutover_id, args.output)}
        if args.action == "status":
            return {"items": await export_status(session, actor, args.cutover_id, args.output)}
        if args.action in {"raygate-proof", "fanyu-proof"}:
            with args.input.open("rb") as handle:
                raw = handle.read(MAX_PROOF_BYTES + 1)
            if len(raw) > MAX_PROOF_BYTES:
                raise ValueError("對帳證明檔案過大")
            if args.action == "fanyu-proof":
                return await apply_fanyu_proof(session, actor, settings, SignedFanyuEvidence.model_validate_json(raw))
            return await apply_raygate_proof(session, actor, settings, SignedRayGateEvidence.model_validate_json(raw))
        raw = args.input.read_bytes()
        if args.action == "arm":
            return {"new_holds": await arm_cutover(session, actor, Manifest.model_validate_json(raw))}
        return {"resolved": await resolve_hold(session, actor, Resolution.model_validate_json(raw))}


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="action", required=True)
    begin = commands.add_parser("begin")
    begin.add_argument("cutover_id", type=UUID)
    begin.add_argument("checkpoint_sha256")
    for command in ("inventory", "status"):
        export = commands.add_parser(command)
        export.add_argument("cutover_id", type=UUID)
        export.add_argument("output", type=Path)
    for command in ("arm", "resolve", "raygate-proof", "fanyu-proof"):
        commands.add_parser(command).add_argument("input", type=Path)
    args = parser.parse_args()
    if not get_settings().recovery_mode:
        parser.exit(1, "請在隔離還原模式執行此命令。\n")
    if not sys.stdin.isatty():
        parser.exit(1, "請在私有終端輸入隔離管理員憑證。\n")
    try:
        result = asyncio.run(run(args, getpass("隔離管理員短效憑證：")))
    except ValidationError:
        parser.exit(1, "隔離清單或對帳結果格式不正確。\n")
    except FileExistsError:
        parser.exit(1, "請選擇尚未存在的私有輸出檔案。\n")
    except (HTTPException, PermissionError):
        parser.exit(1, "需要有效的隔離管理員憑證。\n")
    except ValueError as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
