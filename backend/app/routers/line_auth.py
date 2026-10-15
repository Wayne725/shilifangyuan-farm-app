from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..auth import get_optional_user
from ..config import Settings, get_settings
from ..database import get_session
from ..identity_numbers import next_identity_number
from ..integrations.common import IntegrationConfigurationError, IntegrationError
from ..integrations.line_login import LineLoginClient, LineProfile
from ..models import ExternalIdentity, LineOAuthState, User
from ..rate_limit import LOGIN_RULE, client_key, enforce
from .auth import _new_token_pair_and_session, _validate_cookie_origin, token_response

line_auth_router = APIRouter(prefix="/v1/auth/line", tags=["auth"])
FLOW_COOKIE = "slf_line_flow"
REGISTRATION_COOKIE = "slf_line_registration"
COOKIE_PATH = "/v1/auth/line"


class LineStart(BaseModel):
    intent: Literal["member", "nonmember"] = "nonmember"
    remember_me: bool = True
    return_to: str = Field(default="/shop", max_length=500)
    mode: Literal["login", "link"] = "login"


class LineCallback(BaseModel):
    state: str = Field(min_length=20, max_length=128)
    code: str = Field(default="", max_length=2048)
    error: str = Field(default="", max_length=100)


class LineRegistration(BaseModel):
    email: EmailStr | None = None


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _return_path(value: str) -> str:
    parsed = urlsplit(value)
    path = parsed.path
    valid_path = path in {
        "/", "/shop", "/help", "/social", "/activities", "/governance", "/meetings",
        "/wishes", "/directory", "/membership", "/checkout", "/cart", "/orders",
        "/account", "/group-votes", "/group-votes/new", "/meal-orders", "/admin",
        "/admin/social", "/admin/catalog", "/admin/groups", "/admin/meals",
        "/admin/finance", "/admin/kitchen", "/admin/users", "/register", "/verify-email",
        "/forgot-password", "/reset-password",
    } or any(
        path.startswith(prefix) and path[len(prefix):]
        and all(char.isalnum() or char in "_-" for char in path[len(prefix):])
        for prefix in ("/meals/", "/groups/", "/products/", "/proposals/", "/group-votes/")
    )
    if parsed.scheme or parsed.netloc or not valid_path or "\\" in value or any(ord(char) < 32 for char in value):
        raise HTTPException(status_code=422, detail="登入返回頁面不合法")
    if path in {"/register", "/verify-email", "/forgot-password", "/reset-password"}:
        return "/account"
    return value


def _cookie(response: Response, name: str, value: str, settings: Settings) -> None:
    response.set_cookie(name, value, max_age=600, path=COOKIE_PATH,
        httponly=True, secure=settings.refresh_cookie_secure, samesite=settings.refresh_cookie_samesite)
    response.headers["Cache-Control"] = "no-store"


def _clear_flow(response: Response, settings: Settings) -> None:
    for name in (FLOW_COOKIE, REGISTRATION_COOKIE):
        response.delete_cookie(name, path=COOKIE_PATH, httponly=True,
            secure=settings.refresh_cookie_secure, samesite=settings.refresh_cookie_samesite)
    response.headers["Cache-Control"] = "no-store"


def _client(settings: Settings) -> LineLoginClient:
    client = LineLoginClient(settings)
    try:
        client.validate_settings()
    except IntegrationConfigurationError as exc:
        raise HTTPException(status_code=503, detail="LINE登入尚未完成設定，請先使用Email登入") from exc
    return client


@line_auth_router.get("/status")
async def line_status(response: Response, settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session), user: User | None = Depends(get_optional_user)) -> dict:
    ready = True
    try:
        LineLoginClient(settings).validate_settings()
    except IntegrationConfigurationError:
        ready = False
    linked = user is not None and await session.scalar(select(ExternalIdentity.id).where(
        ExternalIdentity.provider == "line", ExternalIdentity.user_id == user.id)) is not None
    response.headers["Cache-Control"] = "no-store"
    return {"ready": ready, "linked": bool(linked), "identity_verified": bool(linked),
        "message": "" if ready else "LINE登入尚未完成設定，可先使用Email登入"}


@line_auth_router.post("/start")
async def line_start(body: LineStart, request: Request, response: Response,
    session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings),
    user: User | None = Depends(get_optional_user)) -> dict:
    _validate_cookie_origin(request, settings)
    enforce(client_key(request, "line-start"), LOGIN_RULE)
    client = _client(settings)
    if body.mode == "link" and user is None:
        raise HTTPException(status_code=401, detail="請先登入原帳號再連結LINE")
    if body.mode == "login" and user is not None:
        raise HTTPException(status_code=409, detail="你已登入，請在帳號中心連結LINE")
    now = datetime.now(timezone.utc)
    await session.execute(delete(LineOAuthState).where(LineOAuthState.expires_at <= now))
    raw_state, browser = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    record = LineOAuthState(state_hash=_hash(raw_state), browser_hash=_hash(browser),
        nonce=secrets.token_urlsafe(32), code_verifier=secrets.token_urlsafe(64),
        purpose=body.mode, target_user_id=user.id if user else None,
        target_token_version=int(user.token_version or 0) if user else None,
        registration_intent=body.intent, return_to=_return_path(body.return_to),
        remember_me=body.remember_me, expires_at=now + timedelta(minutes=10))
    session.add(record)
    await session.commit()
    _cookie(response, FLOW_COOKIE, browser, settings)
    response.delete_cookie(REGISTRATION_COOKIE, path=COOKIE_PATH)
    return {"authorization_url": client.authorization_url(raw_state, record.nonce, record.code_verifier)}


async def _sign_in(user: User, record: LineOAuthState, session: AsyncSession,
    response: Response, settings: Settings) -> dict:
    user = await session.scalar(select(User).where(User.id == user.id)
        .options(selectinload(User.membership)).execution_options(populate_existing=True))
    if user is None or not user.is_active:
        raise HTTPException(status_code=403, detail="此帳號已停用，請洽管理員")
    pair, refresh = _new_token_pair_and_session(user, settings, remember_me=record.remember_me)
    session.add(refresh)
    await session.commit()
    result = token_response(user, response, settings, pair, remember_me=record.remember_me)
    _clear_flow(response, settings)
    return {"status": "authenticated", "return_to": record.return_to, "intent": record.registration_intent,
        "session": result.model_dump(mode="json")}


async def _create_account(profile: LineProfile, email: str | None, record: LineOAuthState,
    session: AsyncSession) -> User:
    if email and await session.scalar(select(User.id).where(User.email == email)) is not None:
        raise HTTPException(status_code=409, detail="此Email已有帳號，請先使用原帳號登入，再於帳號中心連結LINE")
    user = User(email=email, display_name=profile.display_name,
        customer_number=await next_identity_number(session, User.customer_number, "SLF-C"),
        password_hash="!line-only", email_verified_at=None)
    session.add(user)
    await session.flush()
    session.add(ExternalIdentity(user_id=user.id, provider="line", subject=profile.subject,
        registration_intent=record.registration_intent))
    await session.flush()
    return user


@line_auth_router.post("/callback")
async def line_callback(body: LineCallback, request: Request, response: Response,
    session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings),
    user: User | None = Depends(get_optional_user)) -> dict:
    _validate_cookie_origin(request, settings)
    client = _client(settings)
    now = datetime.now(timezone.utc)
    record = await session.scalar(select(LineOAuthState).where(LineOAuthState.state_hash == _hash(body.state)))
    browser = request.cookies.get(FLOW_COOKIE, "")
    if record is None or not browser or not secrets.compare_digest(record.browser_hash, _hash(browser)):
        raise HTTPException(status_code=400, detail="LINE登入流程無效，請從原頁重新登入")
    claimed = await session.scalar(update(LineOAuthState).where(
        LineOAuthState.id == record.id, LineOAuthState.consumed_at.is_(None), LineOAuthState.expires_at > now
    ).values(consumed_at=now).returning(LineOAuthState.id).execution_options(synchronize_session=False))
    await session.commit()
    if claimed is None:
        raise HTTPException(status_code=400, detail="LINE登入流程已過期或已使用，請重新登入")
    if body.error:
        _clear_flow(response, settings)
        return {"status": "cancelled", "return_to": record.return_to, "message": "已取消LINE授權，原選餐仍保留"}
    if not body.code:
        raise HTTPException(status_code=400, detail="LINE沒有提供授權碼，請重新登入")
    if record.purpose == "link":
        if (user is None or user.id != record.target_user_id
            or int(user.token_version or 0) != record.target_token_version):
            raise HTTPException(status_code=409, detail="原登入帳號已變更，請回原帳號重新連結LINE")
    elif user is not None:
        raise HTTPException(status_code=409, detail="目前已登入另一工作階段，請確認帳號後重新操作")
    try:
        profile = await client.exchange(body.code, record.code_verifier, record.nonce)
    except IntegrationError as exc:
        raise HTTPException(status_code=502, detail="無法確認LINE授權，請稍後重新登入") from exc
    if record.purpose == "link":
        user = await session.scalar(select(User).where(User.id == record.target_user_id)
            .execution_options(populate_existing=True).with_for_update())
        if (user is None or not user.is_active
            or int(user.token_version or 0) != record.target_token_version):
            raise HTTPException(status_code=409, detail="原登入帳號已變更，請回原帳號重新連結LINE")
    identity = await session.scalar(select(ExternalIdentity).where(
        ExternalIdentity.provider == "line", ExternalIdentity.subject == profile.subject).with_for_update())
    try:
        if record.purpose == "link":
            if identity is not None and identity.user_id != user.id:
                raise HTTPException(status_code=409, detail="此LINE已連結其他帳號，請洽管理員核對")
            existing = await session.scalar(select(ExternalIdentity).where(
                ExternalIdentity.provider == "line", ExternalIdentity.user_id == user.id))
            if existing is not None and existing.subject != profile.subject:
                raise HTTPException(status_code=409, detail="此帳號已連結另一個LINE，請洽管理員核對")
            if identity is None:
                session.add(ExternalIdentity(user_id=user.id, provider="line", subject=profile.subject,
                    registration_intent=record.registration_intent))
            await session.commit()
            _clear_flow(response, settings)
            return {"status": "linked", "return_to": record.return_to, "message": "LINE已連結原帳號，下次可直接使用LINE登入"}
        if identity is not None:
            account = await session.get(User, identity.user_id)
            if account is None or not account.is_active:
                raise HTTPException(status_code=403, detail="此帳號已停用，請洽管理員")
            identity.last_login_at = now
            return await _sign_in(account, record, session, response, settings)
        email = profile.email
        if email and await session.scalar(select(User.id).where(User.email == email)) is not None:
            email = None
        account = await _create_account(profile, email, record, session)
        return await _sign_in(account, record, session, response, settings)
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="帳號或LINE已建立連結，請重新登入或使用原帳號連結") from exc


@line_auth_router.post("/register")
async def line_register(body: LineRegistration, request: Request, response: Response,
    session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings),
    user: User | None = Depends(get_optional_user)) -> dict:
    _validate_cookie_origin(request, settings)
    _client(settings)
    if user is not None:
        raise HTTPException(status_code=409, detail="已登入其他帳號，請先確認帳號後重新操作")
    now = datetime.now(timezone.utc)
    registration = request.cookies.get(REGISTRATION_COOKIE, "")
    browser = request.cookies.get(FLOW_COOKIE, "")
    record = await session.scalar(select(LineOAuthState).where(LineOAuthState.registration_hash == _hash(registration)))
    if record is None or not registration or not browser or not secrets.compare_digest(record.browser_hash, _hash(browser)):
        raise HTTPException(status_code=400, detail="LINE註冊流程無效，請重新登入")
    expires = record.expires_at.replace(tzinfo=record.expires_at.tzinfo or timezone.utc)
    if not record.pending_subject or record.registration_completed_at or expires <= now:
        raise HTTPException(status_code=400, detail="LINE註冊流程已過期或已完成，請重新登入")
    email = str(body.email).lower() if body.email else None
    if email and await session.scalar(select(User.id).where(User.email == email)) is not None:
        raise HTTPException(status_code=409, detail="此Email已有帳號，請先使用原帳號登入，再於帳號中心連結LINE")
    claimed = await session.scalar(update(LineOAuthState).where(
        LineOAuthState.id == record.id, LineOAuthState.registration_completed_at.is_(None), LineOAuthState.expires_at > now
    ).values(registration_completed_at=now).returning(LineOAuthState.id).execution_options(synchronize_session=False))
    await session.commit()
    if claimed is None:
        raise HTTPException(status_code=400, detail="LINE註冊流程已過期或已完成，請重新登入")
    try:
        account = await _create_account(LineProfile(record.pending_subject, record.pending_display_name or "LINE使用者", None), email, record, session)
        return await _sign_in(account, record, session, response, settings)
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="此LINE或Email已建立帳號，請重新登入或使用原帳號連結") from exc


@line_auth_router.get("/pending")
async def line_pending(request: Request, response: Response,
    session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)) -> dict:
    _client(settings)
    registration = request.cookies.get(REGISTRATION_COOKIE, "")
    browser = request.cookies.get(FLOW_COOKIE, "")
    record = await session.scalar(select(LineOAuthState).where(LineOAuthState.registration_hash == _hash(registration)))
    now = datetime.now(timezone.utc)
    if (record is None or not registration or not browser or not record.pending_subject
        or not secrets.compare_digest(record.browser_hash, _hash(browser))
        or record.registration_completed_at is not None
        or record.expires_at.replace(tzinfo=record.expires_at.tzinfo or timezone.utc) <= now):
        raise HTTPException(status_code=400, detail="LINE登入流程已過期，請重新登入")
    response.headers["Cache-Control"] = "no-store"
    return {"status": "needs_email", "return_to": record.return_to, "intent": record.registration_intent,
        "message": "LINE身分已確認，請補一次訂單與電子發票通知Email，不需要建立密碼"}
