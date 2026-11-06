from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models import AdminAudit, PointTransaction, Wish, WishStatus
from tests.support import auth_headers
from tests.test_cooperative_core import cooperative_context


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["launched_product_id", "launched_campaign_id"])
async def test_wish_launch_rejects_missing_target_without_points_or_audit(cooperative_context, field):
    context = cooperative_context
    client, session = context["client"], context["session"]
    created = await client.post("/v1/wishes", headers=auth_headers(context["member_a"]),
        json={"name": "非法參照不可成案", "description": "驗證關聯目標"})
    assert created.status_code == 201
    wish_id = created.json()["id"]
    previous_points = await session.scalar(select(func.count(PointTransaction.id)))
    previous_audits = await session.scalar(select(func.count(AdminAudit.id)))
    rejected = await client.put(f"/v1/admin/wishes/{wish_id}/status",
        headers=auth_headers(context["admin"]),
        json={"status": "launched", field: str(uuid4()), "proposer_points": 120})
    assert rejected.status_code == 422, rejected.text
    wish = await session.get(Wish, wish_id)
    await session.refresh(wish)
    assert wish.status == WishStatus.SUBMITTED
    assert wish.launched_product_id is None
    assert wish.launched_campaign_id is None
    assert await session.scalar(select(func.count(PointTransaction.id))) == previous_points
    assert await session.scalar(select(func.count(AdminAudit.id))) == previous_audits
