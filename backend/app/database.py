from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Optional

from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings


NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def create_engine(database_url: Optional[str] = None) -> AsyncEngine:
    settings = get_settings()
    url = database_url or settings.async_database_url
    kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs.update(
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout=settings.database_pool_timeout_seconds,
            pool_recycle=settings.database_pool_recycle_seconds,
            connect_args={"timeout": settings.database_connect_timeout_seconds},
        )
    return create_async_engine(url, **kwargs)


engine = create_engine()
SessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def check_database_connection(timeout_seconds: float | None = None) -> None:
    timeout = timeout_seconds or get_settings().database_readiness_timeout_seconds

    async def execute_probe() -> None:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))

    await asyncio.wait_for(execute_probe(), timeout=timeout)


async def create_all(database_engine: Optional[AsyncEngine] = None) -> None:
    target_engine = database_engine or engine
    async with target_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def drop_all(database_engine: Optional[AsyncEngine] = None) -> None:
    target_engine = database_engine or engine
    async with target_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
