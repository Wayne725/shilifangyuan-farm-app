from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
from fastapi import HTTPException, Request, Response
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .auth import create_token, decode_token
from .config import Settings
from .models import BrowserRefreshSelection, RefreshSession, User

PROTOCOL_HEADER = "X-Refresh-Protocol"
OPERATION_HEADER = "X-Refresh-Operation"
REFRESH_COOKIE_RACE = "refresh_cookie_race"
RECOVERY_WINDOW = timedelta(seconds=20)
BROWSER_REFRESH_TYPE = "browser_refresh"
_DEVICE_LIFETIME = 31 * 24 * 60 * 60
_HEX_ID = re.compile(r"[0-9a-f]{32}")
_COOKIE_PATH = "/v1/auth"

PreparedBrowserLogin = tuple[str, BrowserRefreshSelection | None]
BrowserLoginGeneration = tuple[BrowserRefreshSelection, RefreshSession]


def _device_cookie(settings: Settings) -> str:
    return f"{settings.refresh_cookie_name}_device"


def _generation_prefix(settings: Settings) -> str:
    return f"{settings.refresh_cookie_name}_v2_"


def _generation_cookie(settings: Settings, family_id: str, session_id: str) -> str:
    family = family_id.replace("-", "")
    generation = session_id.replace("-", "")
    return f"{_generation_prefix(settings)}{family}_{generation}"


def _generation_cookies(request: Request, settings: Settings) -> list[str]:
    prefix = _generation_prefix(settings)
    return [
        name for name in request.cookies
        if name.startswith(prefix)
        and len(name) == len(prefix) + 65
        and _HEX_ID.fullmatch(name[len(prefix):-33])
        and name[-33] == "_"
        and _HEX_ID.fullmatch(name[-32:])
    ]


def uses_browser_refresh(request: Request, settings: Settings) -> bool:
    return _device_cookie(settings) in request.cookies or bool(
        _generation_cookies(request, settings)
    )


def _cookie_options(settings: Settings) -> dict:
    return {
        "path": _COOKIE_PATH,
        "secure": settings.refresh_cookie_secure,
        "httponly": True,
        "samesite": settings.refresh_cookie_samesite,
    }


def _clear_old_cookies(
    request: Request, response: Response, settings: Settings, *, keep: set[str]
) -> None:
    for name in _generation_cookies(request, settings):
        if name not in keep:
            response.delete_cookie(name, **_cookie_options(settings))
    if settings.refresh_cookie_name in request.cookies:
        response.delete_cookie(settings.refresh_cookie_name, **_cookie_options(settings))


def _signed_device_id(device_id: str, settings: Settings) -> str:
    signature = hmac.new(
        settings.jwt_secret.encode(), f"browser-refresh-device:{device_id}".encode(), hashlib.sha256
    ).hexdigest()
    return f"{device_id}.{signature}"


def _request_device_id(request: Request, settings: Settings) -> str | None:
    value = request.cookies.get(_device_cookie(settings), "")
    device_id, separator, signature = value.partition(".")
    if separator and _HEX_ID.fullmatch(device_id) and len(signature) == 64 and signature.isascii():
        if hmac.compare_digest(value, _signed_device_id(device_id, settings)):
            return device_id
    return None


async def _locked_selection(
    session: AsyncSession, device_id: str | None
) -> BrowserRefreshSelection | None:
    if device_id is None:
        return None
    if session.get_bind().dialect.name == "sqlite":
        # SQLite ignores FOR UPDATE. Acquire its write lock before reading the selector.
        await session.execute(
            update(BrowserRefreshSelection)
            .where(BrowserRefreshSelection.id == device_id)
            .values(id=BrowserRefreshSelection.id)
            .execution_options(synchronize_session=False)
        )
    return await session.scalar(
        select(BrowserRefreshSelection)
        .where(BrowserRefreshSelection.id == device_id)
        .with_for_update()
    )


async def prepare_browser_login(
    request: Request, session: AsyncSession, settings: Settings
) -> PreparedBrowserLogin | None:
    opted_in = settings.browser_refresh_protocol_enabled and request.headers.get(PROTOCOL_HEADER) == "2"
    if not opted_in and not uses_browser_refresh(request, settings):
        return None
    device_id = _request_device_id(request, settings) or secrets.token_hex(16)
    return device_id, await _locked_selection(session, device_id)


def _new_generation(user: User, settings: Settings, now: datetime) -> RefreshSession:
    issued_at = now.replace(microsecond=0)
    return RefreshSession(
        id=str(uuid4()),
        user_id=user.id,
        created_at=issued_at,
        expires_at=issued_at + timedelta(days=settings.refresh_token_days),
    )


async def establish_browser_login(
    session: AsyncSession,
    prepared: PreparedBrowserLogin,
    user: User,
    settings: Settings,
) -> BrowserLoginGeneration:
    device_id, selection = prepared
    generation = _new_generation(user, settings, datetime.now(timezone.utc))
    session.add(generation)
    await session.flush()
    if selection is None:
        # A signed selector can outlive its expired database row.
        insert = sqlite_insert if session.get_bind().dialect.name == "sqlite" else pg_insert
        values = {
            "family_id": generation.id,
            "current_session_id": generation.id,
            "predecessor_id": None,
            "recovery_proof_hash": None,
            "recovery_until": None,
        }
        statement = insert(BrowserRefreshSelection).values(id=device_id, **values)
        selection = (await session.execute(
            statement.on_conflict_do_update(index_elements=["id"], set_=values)
            .returning(BrowserRefreshSelection)
        )).scalar_one()
    else:
        selection.family_id = generation.id
        selection.current_session_id = generation.id
        selection.predecessor_id = None
        selection.recovery_proof_hash = None
        selection.recovery_until = None
    return selection, generation


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=value.tzinfo or timezone.utc)


def _refresh_token(
    generation: RefreshSession,
    selection: BrowserRefreshSelection,
    user: User,
    settings: Settings,
    *,
    remember_me: bool,
) -> str:
    return jwt.encode(
        {
            "sub": user.id,
            "type": BROWSER_REFRESH_TYPE,
            "ver": int(user.token_version or 0),
            "iat": int(_utc(generation.created_at).timestamp()),
            "exp": int(_utc(generation.expires_at).timestamp()),
            "jti": generation.id,
            "family": selection.family_id,
            "remember_me": remember_me,
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        headers={"typ": "JWT"},
    )


def browser_token_response(
    request: Request,
    response: Response,
    user: User,
    settings: Settings,
    selection: BrowserRefreshSelection,
    generation: RefreshSession,
    *,
    remember_me: bool,
) -> str:
    name = _generation_cookie(settings, selection.family_id, generation.id)
    keep = {name}
    if selection.predecessor_id:
        keep.add(_generation_cookie(settings, selection.family_id, selection.predecessor_id))
    _clear_old_cookies(request, response, settings, keep=keep)
    response.set_cookie(
        _device_cookie(settings), _signed_device_id(selection.id, settings),
        max_age=_DEVICE_LIFETIME, **_cookie_options(settings),
    )
    response.set_cookie(
        name,
        _refresh_token(generation, selection, user, settings, remember_me=remember_me),
        max_age=settings.refresh_token_days * 24 * 60 * 60 if remember_me else None,
        **_cookie_options(settings),
    )
    response.headers[PROTOCOL_HEADER] = "2"
    response.headers["Cache-Control"] = "no-store"
    return create_token(
        user, "access", timedelta(minutes=settings.access_token_minutes), settings,
        browser_family=selection.family_id, browser_device=selection.id,
    )


def _operation_hash(
    request: Request, selection: BrowserRefreshSelection, settings: Settings
) -> str | None:
    operation = request.headers.get(OPERATION_HEADER, "")
    if not _HEX_ID.fullmatch(operation):
        return None
    origin = request.headers.get("origin", "").rstrip("/")
    proof = f"browser-refresh-operation:{selection.id}:{selection.family_id}:{operation}:{origin}"
    return hmac.new(settings.jwt_secret.encode(), proof.encode(), hashlib.sha256).hexdigest()


def _invalid_session(*, cookie_race: bool = False) -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="登入工作階段已失效，請重新登入",
        headers={"X-Error-Code": REFRESH_COOKIE_RACE} if cookie_race else None,
    )


async def refresh_browser_session(
    request: Request,
    session: AsyncSession,
    settings: Settings,
) -> tuple[User, BrowserRefreshSelection, RefreshSession, bool]:
    selection = await _locked_selection(session, _request_device_id(request, settings))
    if selection is None:
        raise _invalid_session()
    generation = await session.get(RefreshSession, selection.current_session_id)
    now = datetime.now(timezone.utc)
    if generation is None or generation.revoked_at is not None or _utc(generation.expires_at) <= now:
        raise _invalid_session()

    proof_hash = _operation_hash(request, selection, settings)
    recovery_until = _utc(selection.recovery_until) if selection.recovery_until else None
    receipt_open = bool(
        proof_hash and selection.recovery_proof_hash and recovery_until and recovery_until > now
    )
    requested_id = generation.id
    cookie = request.cookies.get(_generation_cookie(settings, selection.family_id, requested_id))
    if not cookie and receipt_open and selection.predecessor_id:
        requested_id = selection.predecessor_id
        cookie = request.cookies.get(_generation_cookie(settings, selection.family_id, requested_id))
    if not cookie:
        raise _invalid_session()
    payload = decode_token(cookie, BROWSER_REFRESH_TYPE, settings)
    if (
        payload["jti"] != requested_id
        or payload["sub"] != generation.user_id
        or payload.get("family") != selection.family_id
    ):
        raise _invalid_session()

    # Match the user-before-refresh-row lock order used by password reset.
    user = await session.scalar(
        select(User)
        .where(User.id == generation.user_id, User.is_active.is_(True))
        .options(selectinload(User.membership))
        .with_for_update()
    )
    if user is None or payload.get("ver") != int(user.token_version or 0):
        raise _invalid_session()

    receipt_open = bool(receipt_open and recovery_until and recovery_until > datetime.now(timezone.utc))
    is_recovery = bool(
        receipt_open and proof_hash and selection.recovery_proof_hash
        and hmac.compare_digest(proof_hash, selection.recovery_proof_hash)
    )
    if requested_id != generation.id and not is_recovery:
        raise _invalid_session(cookie_race=receipt_open)

    remember_me = bool(payload.get("remember_me", True))
    if is_recovery:
        return user, selection, generation, remember_me

    replacement = _new_generation(user, settings, now)
    session.add(replacement)
    await session.flush()
    generation.revoked_at = now
    generation.replaced_by_id = replacement.id
    selection.current_session_id = replacement.id
    selection.predecessor_id = generation.id if proof_hash else None
    selection.recovery_proof_hash = proof_hash
    selection.recovery_until = now + RECOVERY_WINDOW if proof_hash else None
    return user, selection, replacement, remember_me


async def should_revoke_on_logout(
    request: Request, session: AsyncSession, settings: Settings, payload: dict
) -> bool:
    device_id = payload.get("browser_device") or _request_device_id(request, settings)
    selection = await _locked_selection(session, device_id)
    if selection is None or payload.get("browser_family") == selection.family_id:
        return True
    selected = await session.get(RefreshSession, selection.current_session_id)
    return selected is None or selected.user_id != payload["sub"]


def clear_logout_cookies(
    request: Request, response: Response, settings: Settings, payload: dict
) -> None:
    family_id = payload.get("browser_family")
    if family_id:
        prefix = f"{_generation_prefix(settings)}{family_id.replace('-', '')}_"
        for name in _generation_cookies(request, settings):
            if name.startswith(prefix):
                response.delete_cookie(name, **_cookie_options(settings))
    else:
        response.delete_cookie(settings.refresh_cookie_name, **_cookie_options(settings))
