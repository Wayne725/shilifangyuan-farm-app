from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_password, make_token_pair
from app.config import Settings, get_settings
from app.database import get_session
from app.models import User


def make_test_settings(**overrides: Any) -> Settings:
    values = {
        "environment": "test",
        "app_base_url": "https://api.example.test",
        "web_base_url": "https://app.example.test",
        **overrides,
    }
    return Settings(_env_file=None, **values)


def auth_headers(user: User) -> dict[str, str]:
    token = make_token_pair(user)["access_token"]
    return {"Authorization": f"Bearer {token}"}


def fast_password_hash(password: str) -> str:
    return hash_password(password, iterations=1_000)


@asynccontextmanager
async def api_test_context(
    session: AsyncSession,
    routers: Sequence[APIRouter],
    *,
    settings: Settings | None = None,
    follow_redirects: bool = False,
) -> AsyncIterator[AsyncClient]:
    application = FastAPI()
    for router in routers:
        application.include_router(router)

    async def override_get_session():
        yield session

    application.dependency_overrides[get_session] = override_get_session
    if settings is not None:
        application.dependency_overrides[get_settings] = lambda: settings

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
        follow_redirects=follow_redirects,
    ) as client:
        yield client
