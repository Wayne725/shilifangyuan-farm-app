import asyncio

import pytest
from sqlalchemy import func, select

from app.models import AdminAudit, SystemSetting, User, UserRole
from app.platform_settings import PLATFORM_KEY
from app.routers.platform import platform_router
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.support import api_test_context, auth_headers


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [False, True])
async def test_two_admins_with_same_version_only_one_can_save(postgres_sessions, existing):
    async with postgres_sessions() as session:
        admins = [User(email=f"platform-admin-{index}@example.com", display_name="管理員", password_hash="test", user_role=UserRole.ADMIN) for index in range(2)]
        session.add_all(admins)
        if existing:
            session.add(SystemSetting(key=PLATFORM_KEY, value={"orders_open": True}))
        await session.commit()
        headers = [auth_headers(admin) for admin in admins]
    gate = asyncio.Event()

    async def update(index):
        await gate.wait()
        async with postgres_sessions() as session:
            async with api_test_context(session, [platform_router]) as client:
                return await client.put("/v1/admin/platform", headers=headers[index], json={
                    "expected_version": 0, "support_phone": f"022345678{index}", "reason": "同時編輯",
                })

    tasks = [asyncio.create_task(update(index)) for index in range(2)]
    gate.set()
    responses = await asyncio.wait_for(asyncio.gather(*tasks), 20)
    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = next(response.json() for response in responses if response.status_code == 200)
    async with postgres_sessions() as session:
        row = await session.get(SystemSetting, PLATFORM_KEY)
        assert row.value["_version"] == 1
        assert row.value["support_phone"] == winner["support_phone"]
        assert await session.scalar(select(func.count(AdminAudit.id)).where(AdminAudit.action == "platform.settings_updated")) == 1
