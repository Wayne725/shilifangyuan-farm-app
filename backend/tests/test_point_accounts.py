from uuid import uuid4

from sqlalchemy import func, select

from app.models import PointAccount, PointTransaction, User
from app.point_accounts import point_account


async def test_new_point_account_and_ledger_remain_in_callers_transaction(database_session):
    session = database_session
    user = User(email="point-rollback@example.com", display_name="社員", password_hash="test", notification_email="original@example.com")
    session.add(user)
    await session.commit()
    user_id = user.id
    user.notification_email = "rollback@example.com"
    account = await point_account(session, user_id)
    session.add(PointTransaction(account_id=account.id, amount=7, source_type="admin_adjustment", reference_id=str(uuid4()), note="回滾驗證"))
    await session.flush()
    await session.rollback()
    assert await session.scalar(select(func.count(PointAccount.id))) == 0
    assert await session.scalar(select(func.count(PointTransaction.id))) == 0
    assert (await session.get(User, user_id)).notification_email == "original@example.com"


async def test_existing_point_account_identity_and_ledger_are_not_replaced(database_session):
    session = database_session
    user = User(email="point-history@example.com", display_name="社員", password_hash="test")
    session.add(user)
    await session.flush()
    account = PointAccount(user_id=user.id)
    session.add(account)
    await session.flush()
    session.add(PointTransaction(account_id=account.id, amount=9, source_type="admin_adjustment", reference_id=str(uuid4()), note="歷史流水"))
    await session.commit()
    original_id, original_created = account.id, account.created_at
    existing = await point_account(session, user.id)
    assert existing.id == original_id
    assert existing.created_at.replace(tzinfo=None) == original_created.replace(tzinfo=None)
    await session.commit()
    assert await session.scalar(select(func.count(PointAccount.id))) == 1
    assert await session.scalar(select(func.sum(PointTransaction.amount))) == 9
