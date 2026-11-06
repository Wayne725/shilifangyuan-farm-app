from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.models import MealEvent, User


@pytest.mark.asyncio
async def test_metadata_cash_default_preserves_false_for_raw_import_and_orm(database_session):
    session = database_session
    owner = User(email="cash-default@example.com", display_name="測試者", password_hash="test")
    session.add(owner)
    await session.flush()
    now = datetime.now(timezone.utc)
    original = MealEvent(title="預設付款驗證", location="測試", created_by_id=owner.id,
        ordering_starts_at=now, ordering_ends_at=now+timedelta(hours=1),
        pickup_starts_at=now+timedelta(hours=2), pickup_ends_at=now+timedelta(hours=3))
    session.add(original)
    await session.commit()
    assert original.allow_cash_payment is False
    clone_id = str(uuid4())
    columns = [column.name for column in MealEvent.__table__.columns if column.name != "allow_cash_payment"]
    names = ",".join(f'"{name}"' for name in columns)
    values = ",".join(":clone_id" if name == "id" else f'"{name}"' for name in columns)
    await session.execute(text(f"INSERT INTO meal_events ({names}) SELECT {values} FROM meal_events WHERE id=:original_id"),
        {"clone_id": clone_id, "original_id": original.id})
    await session.commit()
    imported = await session.get(MealEvent, clone_id)
    assert imported.allow_cash_payment is False
    raw, sql_type = (await session.execute(text("SELECT allow_cash_payment,typeof(allow_cash_payment) FROM meal_events WHERE id=:id"),
        {"id": clone_id})).one()
    assert raw == 0 and sql_type == "integer"
