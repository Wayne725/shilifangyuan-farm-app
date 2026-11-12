import asyncio
import os

import pytest
from sqlalchemy import func, select, text

from app.models import AdminAudit, PointAccount, PointSourceType, PointTransaction
from app.routers.member_reports import member_reports_router
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_member_point_adjustments import URL, adjustment, seed


pytestmark = pytest.mark.skipif(not os.environ.get("TEST_POSTGRES_URL"), reason="Requires disposable PostgreSQL")


@pytest.mark.asyncio
@pytest.mark.parametrize("same_request", [True, False])
async def test_concurrent_point_adjustments_replay_or_require_updated_balance(postgres_sessions, same_request):
    async with postgres_sessions() as session:
        admin, member, *_ = await seed(session)
        headers = auth_headers(admin)
    path = f"{URL}/{member.id}/adjustments"
    first = adjustment()
    second = first if same_request else adjustment(amount=-2)
    start = asyncio.Event()

    async def post(body):
        async with postgres_sessions() as session:
            async with api_test_context(session, [member_reports_router], settings=make_test_settings()) as client:
                await start.wait()
                return await client.post(path, headers=headers, json=body)

    tasks = [asyncio.create_task(post(body)) for body in (first, second)]
    start.set()
    results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=15)
    assert sorted(row.status_code for row in results) == ([201, 201] if same_request else [201, 409])
    if same_request:
        assert results[0].json() == results[1].json()
    else:
        assert next(row for row in results if row.status_code == 409).headers["x-error-code"] == "point_balance_changed"
    async with postgres_sessions() as session:
        assert await session.scalar(select(func.count(PointAccount.id))) == 1
        assert await session.scalar(select(func.count(PointTransaction.id))) == 1
        assert await session.scalar(select(func.count(AdminAudit.id)).where(AdminAudit.action == "points.adjusted")) == 1


@pytest.mark.asyncio
async def test_point_confirmation_waits_for_an_existing_ledger_insert(postgres_sessions):
    async with postgres_sessions() as writer:
        admin, member, *_ = await seed(writer)
        headers = auth_headers(admin)
        account = PointAccount(user_id=member.id)
        writer.add(account)
        await writer.commit()
        writer.add(PointTransaction(account_id=account.id, amount=-4, source_type=PointSourceType.REFUND))
        await writer.flush()

        async def confirm():
            async with postgres_sessions() as session:
                async with api_test_context(session, [member_reports_router], settings=make_test_settings()) as client:
                    return await client.post(f"{URL}/{member.id}/adjustments", headers=headers, json=adjustment())

        task = asyncio.create_task(confirm())
        try:
            async with postgres_sessions() as observer:
                for _ in range(120):
                    await observer.execute(text("SELECT pg_stat_clear_snapshot()"))
                    waiting = await observer.scalar(text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname = current_database() AND wait_event_type = 'Lock' "
                        "AND query LIKE '%point_accounts%' "
                        "AND (query LIKE '%FOR UPDATE%' OR query LIKE '%INSERT INTO point_accounts%')"
                    ))
                    if waiting:
                        break
                    await asyncio.sleep(0.025)
                else:
                    pytest.fail("Point confirmation did not wait for the existing ledger insert")
            await writer.commit()
            result = await asyncio.wait_for(task, timeout=10)
            assert result.status_code == 409 and result.headers["x-error-code"] == "point_balance_changed"
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
