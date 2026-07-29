from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import (
    decode_token,
    get_current_user,
    make_token_pair,
    verify_password,
)
from ..database import get_session
from ..models import User
from ..schemas import LoginRequest, RefreshRequest, TokenResponse, UserRead


auth_router = APIRouter(prefix="/v1/auth", tags=["auth"])


def token_response(user: User) -> TokenResponse:
    pair = make_token_pair(user)
    return TokenResponse(
        **pair,
        user=UserRead.model_validate(user),
    )


@auth_router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    user = await session.scalar(
        select(User).where(User.email == body.email.lower())
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
    return token_response(user)


@auth_router.post("/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    payload = decode_token(body.refresh_token, "refresh")
    user = await session.scalar(
        select(User).where(User.id == payload["sub"], User.is_active.is_(True))
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="找不到此使用者",
        )
    return token_response(user)


@auth_router.get("/me", response_model=UserRead)
async def me(user: User = Depends(get_current_user)) -> User:
    return user


router = auth_router
