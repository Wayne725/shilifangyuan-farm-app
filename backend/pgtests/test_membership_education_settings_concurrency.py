import asyncio

import pytest
from sqlalchemy import func, select

from app.models import AdminAudit, SystemSetting, User, UserRole
from app.membership_education_settings import MEMBERSHIP_EDUCATION_KEY
from app.routers.membership_education_admin import membership_education_admin_router
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.support import api_test_context, auth_headers, make_test_settings


@pytest.mark.asyncio
@pytest.mark.parametrize("initially_configured", [False, True])
async def test_concurrent_admin_saves_cannot_overwrite_the_same_version(postgres_sessions, initially_configured):
    async with postgres_sessions() as session:
        admins = [User(
            email=f"education-concurrency-{index}@example.test", display_name=f"測試管理員{index}",
            password_hash="test", user_role=UserRole.ADMIN,
        ) for index in range(2)]
        session.add_all(admins)
        await session.commit()

    async def save(admin, *, version, retry_limit):
        async with postgres_sessions() as session:
            async with api_test_context(session, [membership_education_admin_router], settings=make_test_settings()) as client:
                return await client.put("/v1/admin/membership-education/settings", headers=auth_headers(admin), json={
                    "expected_version": version, "retry_limit": retry_limit,
                    "retry_interval_minutes": 60, "reason": "並行儲存隔離驗證",
                })

    version = 0
    if initially_configured:
        assert (await save(admins[0], version=0, retry_limit=1)).status_code == 200
        version = 1

    start = asyncio.Event()

    async def compete(admin, retry_limit):
        await start.wait()
        return await save(admin, version=version, retry_limit=retry_limit)

    tasks = [asyncio.create_task(compete(admin, index + 2)) for index, admin in enumerate(admins)]
    start.set()
    results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=15)
    assert sorted(result.status_code for result in results) == [200, 409], [result.text for result in results]
    winner = next(result.json() for result in results if result.status_code == 200)
    assert winner["version"] == version + 1
    async with postgres_sessions() as session:
        stored = await session.get(SystemSetting, MEMBERSHIP_EDUCATION_KEY)
        assert stored.value["version"] == version + 1
        assert stored.value["retry_limit"] == winner["retry_limit"]
        assert stored.updated_by_id == winner["updated_by_id"]
        assert await session.scalar(select(func.count(AdminAudit.id)).where(
            AdminAudit.action == "membership.education_settings_updated",
        )) == version + 1
