from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..account_profiles import AccountProfileRead, AccountProfileUpdate, read_account_profile, update_account_profile
from ..auth import (
    decode_token,
    get_active_user_for_access,
    get_current_user,
    hash_password,
    make_token_pair,
    membership_type_for_user,
    oauth2_scheme,
    verify_and_update_password,
)
from ..browser_refresh import (
    BrowserLoginGeneration,
    PreparedBrowserLogin,
    browser_token_response,
    clear_logout_cookies,
    establish_browser_login,
    prepare_browser_login,
    refresh_browser_session,
    should_revoke_on_logout,
    uses_browser_refresh,
)
from ..config import Settings, get_settings
from ..platform_settings import platform_settings
from ..public_information import require_registration_consent
from ..credential_rate_limit import consume_credential_quota, reset_credential_quota
from ..database import get_session
from ..identity_numbers import next_identity_number
from ..integrations.common import IntegrationError
from ..integrations.invoice_service import invoice_adapter_from_settings
from ..integrations.pii_crypto import (
    encrypt_auth_outbox_credential,
    pii_cipher_from_settings,
)
from ..member_claims import (
    MemberClaimError,
    attach_roster_membership,
    normalize_email,
    reserve_roster_claim,
    verified_roster_entry,
)
from ..rate_limit import (
    LOGIN_RULE,
    PASSWORD_RESET_RULE,
    REGISTER_RULE,
    VERIFICATION_RULE,
    account_key,
    client_key,
)
from ..models import (
    EmailVerificationToken,
    MemberRosterEntry,
    OutboxEvent,
    PasswordResetToken,
    RefreshSession,
    User,
)
from ..schemas import (
    ExistingMemberRegistrationRequest,
    ForgotPasswordRequest,
    LoginRequest,
    RegisterRequest,
    ResendVerificationRequest,
    ResetPasswordRequest,
    TokenResponse,
    UserPreferencesUpdate,
    UserRead,
    VerifyEmailRequest,
)


auth_router = APIRouter(prefix="/v1/auth", tags=["auth"])


def user_read(user: User) -> UserRead:
    membership = user.__dict__.get("membership")
    return UserRead(
        id=user.id,
        customer_number=user.customer_number,
        email=user.email,
        notification_email=user.notification_email,
        saved_mobile_barcode=user.saved_mobile_barcode,
        display_name=user.display_name,
        user_role=user.user_role,
        membership_type=membership_type_for_user(user),
        membership_status=membership.status if membership is not None else None,
        email_verified_at=user.email_verified_at,
    )


def _set_refresh_cookie(
    response: Response,
    refresh_token: str,
    settings: Settings,
    *,
    remember_me: bool = True,
) -> None:
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=refresh_token,
        max_age=settings.refresh_token_days * 24 * 60 * 60 if remember_me else None,
        path="/v1/auth",
        secure=settings.refresh_cookie_secure,
        httponly=True,
        samesite=settings.refresh_cookie_samesite,
    )


def _validate_cookie_origin(request: Request, settings: Settings) -> None:
    origin = request.headers.get("origin")
    if not origin:
        return
    allowed_origins = {
        value.rstrip("/")
        for value in [*settings.cors_origins, settings.web_base_url]
        if value
    }
    if origin.rstrip("/") not in allowed_origins:
        raise HTTPException(status_code=403, detail="不允許的登入來源")


def token_response(
    user: User,
    response: Response,
    settings: Settings,
    pair: dict[str, str],
    *,
    remember_me: bool = True,
) -> TokenResponse:
    _set_refresh_cookie(response, pair["refresh_token"], settings, remember_me=remember_me)
    return TokenResponse(
        access_token=pair["access_token"],
        token_type=pair["token_type"],
        user=user_read(user),
    )


def _new_token_pair_and_session(
    user: User,
    settings: Settings,
    *,
    remember_me: bool = True,
) -> tuple[dict[str, str], RefreshSession]:
    pair = make_token_pair(user, settings, remember_me=remember_me)
    payload = decode_token(pair["refresh_token"], "refresh", settings)
    return pair, RefreshSession(
        id=str(payload["jti"]),
        user_id=user.id,
        expires_at=datetime.fromtimestamp(
            int(payload["exp"]),
            tz=timezone.utc,
        ),
    )


async def _issue_login_session(
    user: User,
    session: AsyncSession,
    settings: Settings,
    browser: PreparedBrowserLogin | None,
    *,
    remember_me: bool,
) -> dict[str, str] | BrowserLoginGeneration:
    if browser is not None:
        return await establish_browser_login(session, browser, user, settings)
    pair, refresh_session = _new_token_pair_and_session(user, settings, remember_me=remember_me)
    session.add(refresh_session)
    return pair


def _login_token_response(
    user: User,
    request: Request,
    response: Response,
    settings: Settings,
    tokens: dict[str, str] | BrowserLoginGeneration,
    *,
    remember_me: bool,
) -> TokenResponse:
    if isinstance(tokens, dict):
        return token_response(user, response, settings, tokens, remember_me=remember_me)
    selection, generation = tokens
    access_token = browser_token_response(
        request, response, user, settings, selection, generation, remember_me=remember_me,
    )
    return TokenResponse(access_token=access_token, token_type="bearer", user=user_read(user))


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _email_token_hash(
    user_id: str,
    token: str,
    settings: Settings,
) -> str:
    message = f"email-verification:{user_id}:{token}".encode("utf-8")
    return hmac.new(
        settings.jwt_secret.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()


async def _issue_email_verification(
    session: AsyncSession,
    user: User,
    settings: Settings,
) -> str:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=10)
    raw_token = f"{secrets.randbelow(1_000_000):06d}"
    token = EmailVerificationToken(
        user_id=user.id,
        token_hash=_email_token_hash(user.id, raw_token, settings),
        expires_at=expires_at,
        created_at=now,
    )
    session.add(token)
    await session.flush()
    session.add(
        OutboxEvent(
            event_type="auth.email_verification_requested",
            aggregate_type="user",
            aggregate_id=user.id,
            payload={
                "recipient": user.email,
                "display_name": user.display_name,
                "credential_encrypted": encrypt_auth_outbox_credential(
                    settings,
                    "auth.email_verification_requested",
                    user.id,
                    raw_token,
                ),
                "verification_token_id": token.id,
            },
        )
    )
    pending_entry = await session.scalar(
        select(MemberRosterEntry)
        .where(MemberRosterEntry.pending_claim_user_id == user.id)
        .with_for_update()
    )
    if pending_entry is not None:
        pending_entry.pending_claim_expires_at = expires_at
    await session.commit()
    return raw_token


def _development_token_response(
    message: str,
    token: str,
    settings: Settings,
) -> dict[str, str]:
    response = {"message": message, "delivery_status": "queued"}
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
    await consume_credential_quota(session, client_key(request, "register"), REGISTER_RULE, settings)
    consent = require_registration_consent(body, (await platform_settings(session)).public_information)
    email = body.email.lower()
    existing = await session.scalar(select(User.id).where(User.email == email))
    if existing is not None:
        raise HTTPException(status_code=409, detail="此 Email 已註冊")
    user = User(
        customer_number=await next_identity_number(
            session,
            User.customer_number,
            "SLF-C",
        ),
        email=email,
        display_name=body.display_name.strip(),
        password_hash=hash_password(body.password),
        **consent,
    )
    session.add(user)
    await session.flush()
    token = await _issue_email_verification(session, user, settings)
    return _development_token_response(
        "註冊完成，驗證信已排入寄送佇列，請稍候",
        token,
        settings,
    )


@auth_router.post(
    "/register-existing-member",
    status_code=status.HTTP_201_CREATED,
)
async def register_existing_member(
    body: ExistingMemberRegistrationRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    await consume_credential_quota(session,
        client_key(
            request,
            "register-existing-member",
            body.member_number,
        ),
        REGISTER_RULE, settings,
    )
    consent = require_registration_consent(body, (await platform_settings(session)).public_information)
    try:
        cipher = pii_cipher_from_settings(settings)
        roster_entry = await verified_roster_entry(
            session,
            cipher,
            member_number=body.member_number,
            legal_name=body.legal_name,
            email=str(body.email),
            phone=body.phone,
        )
    except IntegrationError as exc:
        raise HTTPException(
            status_code=503,
            detail="社員名冊驗證服務尚未完成設定",
        ) from exc
    except MemberClaimError as exc:
        if exc.code == "claimed":
            raise HTTPException(
                status_code=409,
                detail="此社員帳號已啟用，請直接登入或使用忘記密碼",
            ) from exc
        if exc.code == "pending":
            raise HTTPException(
                status_code=409,
                detail="此社員資料正在等待 Email 驗證，請使用原註冊信箱完成驗證",
            ) from exc
        raise HTTPException(
            status_code=400,
            detail="社員資料無法核對，請確認名冊登記內容",
        ) from exc

    email = normalize_email(str(body.email))
    existing = await session.scalar(select(User.id).where(User.email == email))
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail="此 Email 已有帳號，請先登入後在帳號中心認領社員資格",
        )
    user = User(
        customer_number=await next_identity_number(
            session,
            User.customer_number,
            "SLF-C",
        ),
        email=email,
        display_name=body.display_name.strip(),
        password_hash=hash_password(body.password),
        pending_member_claim=True,
        **consent,
    )
    session.add(user)
    await session.flush()
    await reserve_roster_claim(
        session,
        roster_entry,
        user,
        datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    token = await _issue_email_verification(session, user, settings)
    return _development_token_response(
        "社員資料核對完成，驗證信已排入寄送佇列",
        token,
        settings,
    )


@auth_router.post("/verify-email")
async def verify_email(
    body: VerifyEmailRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    email = normalize_email(str(body.email))
    verification_key = client_key(request, "verify-email", email)
    quota_budget = await consume_credential_quota(session, verification_key, VERIFICATION_RULE, settings)
    now = datetime.now(timezone.utc)
    user = await session.scalar(
        select(User).where(User.email == email).with_for_update()
    )
    if user is None:
        raise HTTPException(status_code=400, detail="驗證碼無效或已過期")
    record = await session.scalar(
        select(EmailVerificationToken)
        .where(
            EmailVerificationToken.user_id == user.id,
            EmailVerificationToken.token_hash
            == _email_token_hash(user.id, body.token, settings),
            EmailVerificationToken.used_at.is_(None),
        )
        .with_for_update()
    )
    if record is None or record.expires_at.replace(
        tzinfo=record.expires_at.tzinfo or timezone.utc
    ) <= now:
        raise HTTPException(status_code=400, detail="驗證碼無效或已過期")
    if user.pending_member_claim:
        roster_entry = await session.scalar(
            select(MemberRosterEntry)
            .where(MemberRosterEntry.pending_claim_user_id == user.id)
            .with_for_update()
        )
        pending_expires_at = (
            roster_entry.pending_claim_expires_at
            if roster_entry is not None
            else None
        )
        if (
            roster_entry is None
            or not roster_entry.is_active
            or roster_entry.claimed_user_id is not None
            or pending_expires_at is None
            or pending_expires_at.replace(
                tzinfo=pending_expires_at.tzinfo or timezone.utc
            )
            <= now
        ):
            raise HTTPException(
                status_code=409,
                detail="社員認領保留已失效，請聯絡合作社協助重新核對",
            )
        await attach_roster_membership(session, roster_entry, user)
    await session.execute(
        update(EmailVerificationToken)
        .where(
            EmailVerificationToken.user_id == record.user_id,
            EmailVerificationToken.used_at.is_(None),
        )
        .values(used_at=now)
    )
    user.email_verified_at = now
    await session.flush()
    await reset_credential_quota(session, verification_key, settings, budget=quota_budget)
    await session.commit()
    return {"message": "Email 驗證完成"}


@auth_router.post(
    "/resend-verification",
    status_code=status.HTTP_202_ACCEPTED,
)
async def resend_verification(
    body: ResendVerificationRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    await consume_credential_quota(session,
        client_key(request, "resend-verification", body.email),
        VERIFICATION_RULE, settings,
    )
    user = await session.scalar(
        select(User).where(User.email == body.email.lower())
    )
    token = ""
    if user is not None and (user.email_verified_at is None or user.pending_member_claim):
        token = await _issue_email_verification(session, user, settings)
    response = {
        "message": (
            "若帳號尚未驗證，重新寄送要求已排入寄送佇列；"
            "10 分鐘效期內，已產生的驗證碼皆可使用，"
            "任一驗證成功後全部失效"
        ),
        "delivery_status": "queued",
    }
    if (
        token
        and settings.environment.strip().lower() == "development"
    ):
        response["development_token"] = token
    return response


@auth_router.post(
    "/forgot-password",
    status_code=status.HTTP_202_ACCEPTED,
)
async def forgot_password(
    body: ForgotPasswordRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    await consume_credential_quota(session,
        client_key(request, "forgot-password", body.email),
        PASSWORD_RESET_RULE, settings,
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
                    "credential_encrypted": encrypt_auth_outbox_credential(
                        settings,
                        "auth.password_reset_requested",
                        user.id,
                        raw_token,
                    ),
                },
            )
        )
        await session.commit()
    response = {
        "message": "若帳號存在，密碼重設信的寄送要求已受理",
        "delivery_status": "queued",
    }
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
    user.token_version = int(user.token_version or 0) + 1
    await session.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
        )
        .values(used_at=now)
    )
    await session.execute(
        update(RefreshSession)
        .where(
            RefreshSession.user_id == user.id,
            RefreshSession.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    await session.commit()
    return {"message": "密碼已更新"}


@auth_router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    # Keyed per account as well as per client, so one IP cannot spray an inbox
    # list and one victim account cannot be hammered from a pool of clients.
    login_keys = client_key(request, "login", body.email)
    quota_budget = await consume_credential_quota(session, login_keys, LOGIN_RULE, settings)
    _validate_cookie_origin(request, settings)
    user = await session.scalar(
        select(User)
        .where(User.email == body.email.lower())
        .options(selectinload(User.membership))
    )
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email 或密碼錯誤",
        )
    original_hash = user.password_hash
    verified, updated_hash = verify_and_update_password(body.password, original_hash)
    if not verified:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email 或密碼錯誤",
        )
    if user.email_verified_at is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="請先完成 Email 驗證",
        )
    await session.execute(
        delete(RefreshSession)
        .where(
            RefreshSession.user_id == user.id,
            RefreshSession.expires_at <= datetime.now(timezone.utc),
        )
        .execution_options(synchronize_session=False)
    )
    browser = await prepare_browser_login(request, session, settings)
    if browser is not None:
        # An overlapping logout on this device may have advanced the account version.
        user = await session.scalar(
            select(User)
            .where(User.id == user.id, User.is_active.is_(True), User.email_verified_at.is_not(None))
            .options(selectinload(User.membership))
            .execution_options(populate_existing=True)
        )
        if user is None:
            raise HTTPException(status_code=401, detail="Email 或密碼錯誤")
        if user.password_hash != original_hash:
            verified, updated_hash = verify_and_update_password(body.password, user.password_hash)
            if not verified:
                raise HTTPException(status_code=401, detail="Email 或密碼錯誤")
    if updated_hash is not None:
        user.password_hash = updated_hash
    tokens = await _issue_login_session(user, session, settings, browser, remember_me=body.remember_me)
    await session.flush()
    await reset_credential_quota(session, account_key("login", user.email), settings, budget=quota_budget)
    await session.commit()
    return _login_token_response(
        user, request, response, settings, tokens, remember_me=body.remember_me,
    )


@auth_router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    _validate_cookie_origin(request, settings)
    if uses_browser_refresh(request, settings):
        user, selection, generation, remember_me = await refresh_browser_session(
            request, session, settings,
        )
        await session.commit()
        return _login_token_response(
            user, request, response, settings, (selection, generation), remember_me=remember_me,
        )
    refresh_token = request.cookies.get(settings.refresh_cookie_name)
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="找不到登入工作階段",
        )
    payload = decode_token(refresh_token, "refresh", settings)
    now = datetime.now(timezone.utc)
    stored_session = await session.scalar(
        select(RefreshSession)
        .where(
            RefreshSession.id == payload["jti"],
            RefreshSession.user_id == payload["sub"],
        )
        .with_for_update()
    )
    stored_expires_at = (
        stored_session.expires_at.replace(
            tzinfo=stored_session.expires_at.tzinfo or timezone.utc
        )
        if stored_session is not None
        else None
    )
    if (
        stored_session is None
        or stored_session.revoked_at is not None
        or stored_expires_at is None
        or stored_expires_at <= now
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登入工作階段已失效，請重新登入",
        )
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
    if int(payload.get("ver", -1)) != int(user.token_version or 0):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登入工作階段已失效，請重新登入",
        )
    remember_me = bool(payload.get("remember_me", True))
    pair, replacement = _new_token_pair_and_session(user, settings, remember_me=remember_me)
    session.add(replacement)
    await session.flush()
    stored_session.revoked_at = now
    stored_session.replaced_by_id = replacement.id
    await session.commit()
    return token_response(user, response, settings, pair, remember_me=remember_me)


@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    token: str = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> None:
    _validate_cookie_origin(request, settings)
    payload = decode_token(token, "access", settings)
    revoke = await should_revoke_on_logout(request, session, settings, payload)
    user = await get_active_user_for_access(payload, session, logout=True)
    if not revoke:
        clear_logout_cookies(request, response, settings, payload)
        return
    now = datetime.now(timezone.utc)
    user.token_version = int(user.token_version or 0) + 1
    await session.execute(
        update(RefreshSession)
        .where(
            RefreshSession.user_id == user.id,
            RefreshSession.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    await session.commit()
    clear_logout_cookies(request, response, settings, payload)


@auth_router.get("/me", response_model=UserRead)
async def me(user: User = Depends(get_current_user)) -> UserRead:
    return user_read(user)


@auth_router.get("/profile", response_model=AccountProfileRead)
async def get_profile(
    response: Response, user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings),
) -> AccountProfileRead:
    response.headers["Cache-Control"] = "private, no-store"
    return await read_account_profile(session, user, settings)


@auth_router.patch("/profile", response_model=AccountProfileRead)
async def edit_profile(
    body: AccountProfileUpdate, request: Request, response: Response, user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings),
) -> AccountProfileRead:
    if request.headers.get("X-Expected-User-ID", user.id) != user.id:
        raise HTTPException(409, "登入帳號已變更，請重新整理後再試")
    response.headers["Cache-Control"] = "private, no-store"
    return await update_account_profile(session, user.id, user, body, settings)


@auth_router.patch("/preferences", response_model=UserRead)
async def update_preferences(
    body: UserPreferencesUpdate,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> UserRead:
    expected_user_id = request.headers.get("X-Expected-User-ID")
    if expected_user_id is not None and expected_user_id != user.id:
        raise HTTPException(409, "登入帳號已變更，請重新整理後再試")
    barcode = body.saved_mobile_barcode
    if barcode is not None:
        try:
            result = await invoice_adapter_from_settings(settings).validate_mobile_barcode(barcode)
        except (IntegrationError, ValueError) as exc:
            raise HTTPException(503, "手機條碼驗證服務目前無法使用") from exc
        if not result.valid:
            raise HTTPException(422, result.message or "手機條碼驗證失敗")
        barcode = result.barcode
    user.saved_mobile_barcode = barcode
    await session.commit()
    return user_read(user)


router = auth_router
