from datetime import datetime, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.models import OrderNumberCounter
from app.order_numbers import allocate_order_number
from tests.test_cash_payments import cash_context, create_cash


async def test_meal_numbers_are_sequential_beyond_one_thousand_in_the_same_second(database_session):
    fixed = datetime(2026, 11, 6, 4, tzinfo=timezone.utc)
    numbers = [await allocate_order_number(database_session, "M", fixed) for _ in range(1001)]
    assert len(set(numbers)) == 1001
    assert numbers[:2] == ["M2026110601", "M2026110602"]
    assert numbers[98:101] == ["M2026110699", "M20261106100", "M20261106101"]
    assert numbers[-1] == "M202611061001"


async def test_number_day_changes_at_taiwan_midnight_and_prefixes_are_independent(database_session):
    before = datetime(2026, 11, 6, 15, 59, 59, tzinfo=timezone.utc)
    after = datetime(2026, 11, 6, 16, tzinfo=timezone.utc)
    assert await allocate_order_number(database_session, "M", before) == "M2026110601"
    assert await allocate_order_number(database_session, "O", before) == "O2026110601"
    assert await allocate_order_number(database_session, "M", after) == "M2026110701"
    assert await allocate_order_number(database_session, "M", after) == "M2026110702"


async def test_aborted_order_number_rolls_back_with_transaction(database_session):
    fixed = datetime(2026, 11, 6, 4, tzinfo=timezone.utc)
    assert await allocate_order_number(database_session, "O", fixed) == "O2026110601"
    await database_session.rollback()
    assert await allocate_order_number(database_session, "O", fixed) == "O2026110601"
    await database_session.commit()
    assert await allocate_order_number(database_session, "O", fixed) == "O2026110602"
    await database_session.rollback()
    assert await allocate_order_number(database_session, "O", fixed) == "O2026110602"


@pytest.mark.parametrize("prefix,now", [
    ("X", datetime(2026, 11, 6, tzinfo=timezone.utc)),
    ("M", datetime(2026, 11, 6)),
])
async def test_invalid_allocation_scope_is_rejected(database_session, prefix, now):
    with pytest.raises(ValueError):
        await allocate_order_number(database_session, prefix, now)
    assert list(await database_session.scalars(select(OrderNumberCounter))) == []


async def test_meal_retry_returns_original_order_without_allocating_again(cash_context):
    request_id = str(uuid4())
    first = await create_cash(cash_context, client_request_id=request_id)
    repeated = await create_cash(cash_context, client_request_id=request_id)
    assert first.status_code == repeated.status_code == 201
    assert first.json()["id"] == repeated.json()["id"]
    assert first.json()["order_number"] == repeated.json()["order_number"]
    day = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Taipei")).date()
    counter = await cash_context.session.get(OrderNumberCounter, ("M", day))
    assert counter.last_value == 1
    next_order = await create_cash(cash_context, client_request_id=str(uuid4()))
    assert next_order.status_code == 201, next_order.text
    assert next_order.json()["order_number"] == f"M{day:%Y%m%d}02"
