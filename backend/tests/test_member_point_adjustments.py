from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.auth import create_token
from app.models import AdminAudit, Membership, MembershipStatus, PointAccount, PointSourceType, PointTransaction, User, UserRole
from app.routers.cooperative import cooperative_router
from app.routers.member_reports import member_reports_router
from tests.support import api_test_context, auth_headers, make_test_settings


URL = "/v1/admin/points-ledger"


def adjustment(amount=7, balance=0, number="ACTIVE-1", **fields):
    return {"request_id": str(uuid4()), "amount": amount, "expected_balance": balance,
            "member_number": number, **fields}


async def seed(session):
    admin = User(email="point-admin@example.com", display_name="管理員", password_hash="test", user_role=UserRole.ADMIN)
    member = User(email="point-member@example.com", display_name="有效社員", password_hash="test",
                  membership=Membership(status=MembershipStatus.ACTIVE, member_number="ACTIVE-1"))
    other = User(email="point-other@example.com", display_name="另一社員", password_hash="test",
                 membership=Membership(status=MembershipStatus.ACTIVE, member_number="ACTIVE-2"))
    inactive = User(email="point-inactive@example.com", display_name="停用帳號", password_hash="test", is_active=False,
                    membership=Membership(status=MembershipStatus.ACTIVE, member_number="INACTIVE"))
    trainee = User(email="point-trainee@example.com", display_name="實習社員", password_hash="test",
                   membership=Membership(status=MembershipStatus.TRAINEE, trainee_number="TRAINEE"))
    session.add_all([admin, member, other, inactive, trainee])
    await session.commit()
    return admin, member, other, inactive, trainee


@pytest.mark.asyncio
async def test_point_adjustment_scope_idempotency_signed_balance_and_audit(database_session):
    session = database_session
    admin, member, other, inactive, trainee = await seed(session)
    admin_id, member_id, other_id = admin.id, member.id, other.id
    invalid_targets = ((inactive.id, "INACTIVE"), (trainee.id, "TRAINEE"), (member_id, "ACTIVE-2"))
    path = f"{URL}/{member_id}/adjustments"
    body = adjustment(reason="  ")
    admin_headers, member_headers = auth_headers(admin), auth_headers(member)
    async with api_test_context(session, [member_reports_router, cooperative_router], settings=make_test_settings()) as client:
        listing = await client.get(URL, headers=admin_headers)
        assert {row["member_number"]: row["can_adjust"] for row in listing.json()["items"]} == {
            "ACTIVE-1": True, "ACTIVE-2": True, "INACTIVE": False,
        }
        expired = {"Authorization": f"Bearer {create_token(admin, 'access', -timedelta(minutes=5))}"}
        for headers, code in (({}, 401), (expired, 401), (member_headers, 403)):
            assert (await client.post(path, headers=headers, json=body)).status_code == code
            assert (await client.get(f"{path}/{body['request_id']}", headers=headers)).status_code == code
        for target_id, number in invalid_targets:
            refused = await client.post(f"{URL}/{target_id}/adjustments", headers=admin_headers,
                                        json=adjustment(number=number))
            assert refused.status_code == 409 and refused.headers["x-error-code"] == "point_target_changed"
        for amount in (0, True, 1.5, "1", 2**31):
            assert (await client.post(path, headers=admin_headers, json=adjustment(amount=amount))).status_code == 422
        assert await session.scalar(select(func.count(PointAccount.id))) == 0
        assert (await client.get(f"{path}/{body['request_id']}", headers=admin_headers)).status_code == 404

        saved = await client.post(path, headers=admin_headers, json=body)
        assert saved.status_code == 201 and saved.headers["cache-control"] == "no-store"
        receipt = saved.json()
        assert receipt == {"id": receipt["id"], "user_id": member_id, "member_number": "ACTIVE-1",
                           "amount": 7, "balance_before": 0, "balance_after": 7}
        assert (await client.get(f"{path}/{body['request_id']}", headers=admin_headers)).json() == receipt
        stale = await client.post(path, headers=admin_headers, json=adjustment(amount=-10))
        assert stale.status_code == 409 and stale.headers["x-error-code"] == "point_balance_changed"
        saved_negative = await client.post(path, headers=admin_headers, json=adjustment(amount=-10, balance=7, reason="調整原因"))
        assert saved_negative.status_code == 201 and saved_negative.json()["balance_after"] == -3
        assert (await client.post(path, headers=admin_headers, json=body)).json() == receipt
        for changed in ({**body, "amount": 8}, {**body, "reason": "不同原因"}, {**body, "expected_balance": -3}):
            conflict = await client.post(path, headers=admin_headers, json=changed)
            assert conflict.status_code == 409 and conflict.headers["x-error-code"] == "point_request_conflict"
        crossed = await client.post(f"{URL}/{other_id}/adjustments", headers=admin_headers, json={**body, "member_number": "ACTIVE-2"})
        assert crossed.status_code == 409 and crossed.headers["x-error-code"] == "point_request_conflict"
        assert (await client.get(f"{URL}/{other_id}/adjustments/{body['request_id']}", headers=admin_headers)).status_code == 404
        assert (await client.get("/v1/me/points", headers=member_headers)).json()["balance"] == -3
    rows = (await session.scalars(select(PointTransaction))).all()
    assert len(rows) == 2 and all(row.source_type == PointSourceType.ADMIN_ADJUSTMENT and row.created_by_id == admin_id for row in rows)
    audits = (await session.scalars(select(AdminAudit).where(AdminAudit.action == "points.adjusted"))).all()
    assert len(audits) == 2 and {row.aggregate_id for row in audits} == {row.id for row in rows}
