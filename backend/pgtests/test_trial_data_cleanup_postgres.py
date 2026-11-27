import pytest
from alembic import command
from sqlalchemy import func, select

from app.models import AdminAudit, GroupCampaign, MealEvent, Notification, Order, OrderItemOption, OutboxEvent
from app.trial_data_cleanup import apply_cleanup, plan_cleanup
from pgtests.test_walk_in_sales_migration import migrated_database  # noqa: F401
from tests.test_trial_data_cleanup import CUTOFF, NOW, setup


@pytest.mark.asyncio
async def test_cleanup_runs_on_the_migrated_production_schema(migrated_database):
    await migrated_database.migrate(command.upgrade, "head")
    async with migrated_database.sessions() as session:
        admin, _orders, _events = await setup(session)
        plan = await plan_cleanup(session, CUTOFF, NOW)
        assert sorted(entry["order_number"] for entry in plan.orders) == ["GROUP-EXPIRED", "TEST-EXPIRED"]
        counts = await apply_cleanup(session, plan, admin)
        await session.commit()
        assert counts["orders"] == 2 and counts["meal_events"] == 2 and counts["notifications"] == 2 and counts["group_campaigns"] == 2
        assert await session.scalar(select(func.count()).select_from(Order)) == 6
        assert await session.scalar(select(func.count()).select_from(OrderItemOption)) == 6
        assert {row for row in await session.scalars(select(GroupCampaign.title))} == {"ended-paid", "open"}
        assert await session.scalar(select(func.count()).select_from(OutboxEvent)) == 0
        assert [row for row in await session.scalars(select(Notification.title))] == ["保留"]
        assert {row for row in await session.scalars(select(MealEvent.title))} == {"paid-past", "tomorrow", "launch"}
        assert await session.scalar(select(func.count()).select_from(AdminAudit)) == 1
