import asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from app.models import OrderNumberCounter
from app.order_numbers import allocate_order_number
from pgtests.test_transaction_concurrency import postgres_sessions


async def test_concurrent_connections_allocate_unique_daily_numbers(postgres_sessions):
    gate = asyncio.Event()
    fixed = datetime(2026, 11, 6, 4, tzinfo=timezone.utc)

    async def allocate(prefix):
        async with postgres_sessions() as session:
            await gate.wait()
            number = await allocate_order_number(session, prefix, fixed)
            await asyncio.sleep(0.005)
            await session.commit()
            return number

    tasks = [asyncio.create_task(allocate(prefix)) for prefix in ["M", "O"] * 16]
    gate.set()
    numbers = await asyncio.wait_for(asyncio.gather(*tasks), 15)
    assert set(numbers) == {f"{prefix}20261106{value:02d}" for prefix in ["M", "O"] for value in range(1, 17)}
    async with postgres_sessions() as session:
        counters = list(await session.scalars(select(OrderNumberCounter)))
        assert {(value.prefix, value.last_value) for value in counters} == {("M", 16), ("O", 16)}


async def test_waiting_connection_reuses_rolled_back_uncommitted_number(postgres_sessions):
    fixed = datetime(2026, 11, 6, 4, tzinfo=timezone.utc)
    async with postgres_sessions() as first:
        assert await allocate_order_number(first, "M", fixed) == "M2026110601"

        async def wait_and_commit():
            async with postgres_sessions() as second:
                number = await allocate_order_number(second, "M", fixed)
                await second.commit()
                return number

        pending = asyncio.create_task(wait_and_commit())
        await asyncio.sleep(0.05)
        assert not pending.done()
        await first.rollback()
        assert await asyncio.wait_for(pending, 5) == "M2026110601"
