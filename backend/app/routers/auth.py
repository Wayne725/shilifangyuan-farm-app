from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..auth import (
    decode_token,
    get_current_user,
    hash_password,
    make_token_pair,
    membership_type_for_user,
    verify_password,
)
from ..config import Settings, get_settings
from ..database import get_session
from ..rate_limit import (
    LOGIN_RULE,
    PASSWORD_RESET_RULE,
    REGISTER_RULE,
    VERIFICATION_RULE,
    client_key,
    enforce,
    reset,
)
from ..models import (
    EmailVerificationToken,
    OutboxEvent,
    PasswordResetToken,
    User,
)
from ..schemas import (
    ForgotPasswordRequest,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    ResendVerificationRequest,
    ResetPasswordRequest,
    TokenResponse,
    UserRead,
    VerifyEmailRequest,
)


auth_router = APIRouter(prefix="/v1/auth", tags=["auth"])


def user_read(user: User) -> UserRead:
    membership = user.__dict__.get("membership")
    return UserRead(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        user_role=user.user_role,
        membership_type=membership_type_for_user(user),
        membership_status=membership.status if membership is not None else None,
        email_verified_at=user.email_verified_at,
    )


def token_response(user: User) -> TokenResponse:
    pair = make_token_pair(user)
    return TokenResponse(
        **pair,
        user=user_read(user),
    )


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def _issue_email_verification(
    session: AsyncSession,
    user: User,
) -> str:
    raw_token = f"{secrets.randbelow(1_000_000):06d}"
    session.add(
        EmailVerificationToken(
            user_id=user.id,
            token_hash=_token_hash(raw_token),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
    )
    session.add(
        OutboxEvent(
            event_type="auth.email_verification_requested",
            aggregate_type="user",
            aggregate_id=user.id,
            payload={
                "recipient": user.email,
                "display_name": user.display_name,
                "verification_token": raw_token,
            },
        )
    )
    await session.commit()
    return raw_token


def _development_token_response(
    message: str,
    token: str,
    settings: Settings,
) -> dict[str, str]:
    response = {"message": message}
    if settings.environment.strip().lower() == "development":
        response["development_token"] = token
    return response


@auth_router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    enforce(client_key(request, "register"), REGISTER_RULE)
    email = body.email.lower()
    existing = await session.scalar(select(User.id).where(User.email == email))
    if existing is not None:
        raise HTTPException(status_code=409, detail="此 Email 已註冊")
    user = User(
        email=email,
        display_name=body.display_name.strip(),
        password_hash=hash_password(body.password),
    )
    session.add(user)
    await session.flush()
    token = await _issue_email_verification(session, user)
    return _development_token_response(
        "註冊完成，請至 Email 完成驗證",
        token,
        settings,
    )


@auth_router.post("/verify-email")
async def verify_email(
    body: VerifyEmailRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    now = datetime.now(timezone.utc)
    record = await session.scalar(
        select(EmailVerificationToken)
        .where(
            EmailVerificationToken.token_hash == _token_hash(body.token),
            EmailVerificationToken.used_at.is_(None),
        )
        .with_for_update()
    )
    if record is None or record.expires_at.replace(
        tzinfo=record.expires_at.tzinfo or timezone.utc
    ) <= now:
        raise HTTPException(status_code=400, detail="驗證碼無效或已過期")
    user = await session.get(User, record.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="找不到此使用者")
    record.used_at = now
    user.email_verified_at = now
    await session.commit()
    return {"message": "Email 驗證完成"}


@auth_router.post("/resend-verification")
async def resend_verification(
    body: ResendVerificationRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    enforce(
        client_key(request, "resend-verification", body.email),
        VERIFICATION_RULE,
    )
    user = await session.scalar(
        select(User).where(User.email == body.email.lower())
    )
    token = ""
    if user is not None and user.email_verified_at is None:
        token = await _issue_email_verification(session, user)
    response = {"message": "若帳號尚未驗證，系統已重新寄出驗證信"}
    if (
        token
        and settings.environment.strip().lower() == "development"
    ):
        response["development_token"] = token
    return response


@auth_router.post("/forgot-password")
async def forgot_password(
    body: ForgotPasswordRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    enforce(
        client_key(request, "forgot-password", body.email),
        PASSWORD_RESET_RULE,
    )
    user = await session.scalar(
        select(User).where(User.email == body.email.lower())
    )
    raw_token = ""
    if user is not None and user.is_active:
        raw_token = secrets.token_urlsafe(32)
        session.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=_token_hash(raw_token),
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        )
        session.add(
            OutboxEvent(
                event_type="auth.password_reset_requested",
                aggregate_type="user",
                aggregate_id=user.id,
                payload={
                    "recipient": user.email,
                    "display_name": user.display_name,
                    "reset_token": raw_token,
                },
            )
        )
        await session.commit()
    response = {"message": "若帳號存在，系統已寄出密碼重設信"}
    if (
        raw_token
        and settings.environment.strip().lower() == "development"
    ):
        response["development_token"] = raw_token
    return response


@auth_router.post("/reset-password")
async def reset_password(
    body: ResetPasswordRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    now = datetime.now(timezone.utc)
    record = await session.scalar(
        select(PasswordResetToken)
        .where(
            PasswordResetToken.token_hash == _token_hash(body.token),
            PasswordResetToken.used_at.is_(None),
        )
        .with_for_update()
    )
    if record is None or record.expires_at.replace(
        tzinfo=record.expires_at.tzinfo or timezone.utc
    ) <= now:
        raise HTTPException(status_code=400, detail="重設連結無效或已過期")
    user = await session.get(User, record.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=404, detail="找不到此使用者")
    user.password_hash = hash_password(body.password)
    record.used_at = now
    await session.commit()
    return {"message": "密碼已更新"}


@auth_router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    # Keyed per account as well as per client, so one IP cannot spray an inbox
    # list and one victim account cannot be hammered from a pool of clients.
    account_key = client_key(request, "login", body.email)
    enforce(account_key, LOGIN_RULE)
    user = await session.scalar(
        select(User)
        .where(User.email == body.email.lower())
        .options(selectinload(User.membership))
    )
    if (
        user is None
        or not user.is_active
        or not verify_password(body.password, user.password_hash)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email 或密碼錯誤",
        )
    if user.email_verified_at is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="請先完成 Email 驗證",
        )
    reset(account_key)
    return token_response(user)


@auth_router.post("/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    payload = decode_token(body.refresh_token, "refresh")
    user = await session.scalar(
        select(User)
        .where(User.id == payload["sub"], User.is_active.is_(True))
        .options(selectinload(User.membership))
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="找不到此使用者",
        )
    return token_response(user)


@auth_router.get("/me", response_model=UserRead)
async def me(user: User = Depends(get_current_user)) -> UserRead:
    return user_read(user)


router = auth_router
