from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .config import Settings, get_settings
from .database import get_session
from .models import (
    Membership,
    MembershipStatus,
    MembershipType,
    User,
    UserRole,
)


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/v1/auth/login")
optional_oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/v1/auth/login", auto_error=False
)


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}")


def hash_password(password: str, iterations: int = 210_000) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return f"pbkdf2_sha256${iterations}${_b64encode(salt)}${_b64encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_raw, salt_raw, digest_raw = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            _b64decode(salt_raw),
            int(iterations_raw),
        )
        return hmac.compare_digest(candidate, _b64decode(digest_raw))
    except (ValueError, TypeError):
        return False


def create_token(
    user: User,
    token_type: str,
    expires_delta: timedelta,
    settings: Optional[Settings] = None,
) -> str:
    active_settings = settings or get_settings()
    now = datetime.now(timezone.utc)
    header = {"alg": active_settings.jwt_algorithm, "typ": "JWT"}
    membership = user.__dict__.get("membership")
    membership_type = (
        MembershipType.MEMBER
        if membership is not None
        and membership.status == MembershipStatus.ACTIVE
        else MembershipType.NONMEMBER
    )
    payload = {
        "sub": user.id,
        "type": token_type,
        "role": user.user_role.value,
        "membership": membership_type.value,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
        "jti": str(uuid4()),
    }
    encoded_header = _b64encode(
        json.dumps(header, separators=(",", ":")).encode("utf-8")
    )
    encoded_payload = _b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    )
    unsigned = f"{encoded_header}.{encoded_payload}"
    signature = hmac.new(
        active_settings.jwt_secret.encode("utf-8"),
        unsigned.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{unsigned}.{_b64encode(signature)}"


def decode_token(
    token: str,
    expected_type: str,
    settings: Optional[Settings] = None,
) -> Dict[str, Any]:
    active_settings = settings or get_settings()
    try:
        encoded_header, encoded_payload, encoded_signature = token.split(".")
        unsigned = f"{encoded_header}.{encoded_payload}"
        expected_signature = hmac.new(
            active_settings.jwt_secret.encode("utf-8"),
            unsigned.encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(
            expected_signature, _b64decode(encoded_signature)
        ):
            raise ValueError("invalid signature")
        header = json.loads(_b64decode(encoded_header))
        payload = json.loads(_b64decode(encoded_payload))
        if header.get("alg") != active_settings.jwt_algorithm:
            raise ValueError("unexpected algorithm")
        if payload.get("type") != expected_type:
            raise ValueError("unexpected token type")
        if int(payload["exp"]) <= int(datetime.now(timezone.utc).timestamp()):
            raise ValueError("expired token")
        if not payload.get("sub"):
            raise ValueError("missing subject")
        return payload
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登入憑證無效或已過期",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def make_token_pair(
    user: User, settings: Optional[Settings] = None
) -> Dict[str, str]:
    active_settings = settings or get_settings()
    return {
        "access_token": create_token(
            user,
            "access",
            timedelta(minutes=active_settings.access_token_minutes),
            active_settings,
        ),
        "refresh_token": create_token(
            user,
            "refresh",
            timedelta(days=active_settings.refresh_token_days),
            active_settings,
        ),
        "token_type": "bearer",
    }


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_session),
) -> User:
    payload = decode_token(token, "access")
    user = await session.scalar(
        select(User)
        .where(User.id == payload["sub"], User.is_active.is_(True))
        .options(selectinload(User.membership))
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="找不到此使用者",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def get_optional_user(
    token: Optional[str] = Depends(optional_oauth2_scheme),
    session: AsyncSession = Depends(get_session),
) -> Optional[User]:
    if not token:
        return None
    payload = decode_token(token, "access")
    return await session.scalar(
        select(User)
        .where(User.id == payload["sub"], User.is_active.is_(True))
        .options(selectinload(User.membership))
    )


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.user_role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="需要管理員權限"
        )
    return user


def membership_type_for_user(user: User) -> MembershipType:
    membership = user.__dict__.get("membership")
    if (
        isinstance(membership, Membership)
        and membership.status == MembershipStatus.ACTIVE
    ):
        return MembershipType.MEMBER
    return MembershipType.NONMEMBER


async def require_active_member(
    user: User = Depends(get_current_user),
) -> User:
    if membership_type_for_user(user) != MembershipType.MEMBER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="此功能僅限有效社員使用",
        )
    return user


get_admin_user = require_admin
