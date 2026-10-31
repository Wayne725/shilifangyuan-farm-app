from __future__ import annotations

from datetime import datetime, timezone

import pytest
import time_machine
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database import Base, create_engine
from app.rate_limit import reset_all


@pytest.fixture(autouse=True)
def isolated_credential_limits():
    reset_all()
    yield
    reset_all()


@pytest.fixture
async def database_session():
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def business_hours_clock():
    # Integration cases start inside ordering hours; boundary tests supply their own clock.
    today = datetime.now(timezone.utc).replace(hour=4, minute=0, second=0, microsecond=0)
    with time_machine.travel(today, tick=True):
        yield
