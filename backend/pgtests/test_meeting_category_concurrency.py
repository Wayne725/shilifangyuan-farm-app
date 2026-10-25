import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, select

from app.models import AdminAudit, Meeting, MeetingCategory, User, UserRole
from app.routers.cooperative import create_meeting, create_meeting_category, update_meeting_category
from app.schemas import MeetingCategoryCreate, MeetingCategoryUpdate, MeetingCreate
from pgtests.test_transaction_concurrency import postgres_sessions


async def seed_category(factory):
    async with factory() as session:
        admin = User(email="category-admin@example.test", display_name="管理員", password_hash="test", user_role=UserRole.ADMIN)
        category = MeetingCategory(name="工作會議", sort_order=0)
        session.add_all([admin, category])
        await session.commit()
        return admin.id, category.id, category.updated_at


async def test_two_admin_edits_cannot_overwrite_same_category_version(postgres_sessions):
    admin_id, category_id, version = await seed_category(postgres_sessions)

    async def change(name):
        async with postgres_sessions() as session:
            admin = await session.get(User, admin_id)
            try:
                await update_meeting_category(category_id, MeetingCategoryUpdate(name=name, expected_updated_at=version), admin, session)
                return 200
            except HTTPException as exc:
                await session.rollback()
                return exc.status_code

    assert sorted(await asyncio.wait_for(asyncio.gather(change("會議甲"), change("會議乙")), timeout=10)) == [200, 409]
    async with postgres_sessions() as session:
        assert (await session.get(MeetingCategory, category_id)).name in {"會議甲", "會議乙"}
        assert await session.scalar(select(func.count(AdminAudit.id)).where(AdminAudit.action == "meeting_category.update")) == 1


async def test_disable_commits_before_waiting_meeting_creation_rechecks_active(postgres_sessions):
    admin_id, category_id, _ = await seed_category(postgres_sessions)
    creation_started = asyncio.Event()

    async def create():
        async with postgres_sessions() as session:
            admin = await session.get(User, admin_id)
            creation_started.set()
            try:
                await create_meeting(MeetingCreate(category_id=category_id, title="停用競爭",
                    starts_at=datetime.now(timezone.utc)+timedelta(days=1)), admin, session)
                return 201
            except HTTPException as exc:
                await session.rollback()
                return exc.status_code

    async with postgres_sessions() as session:
        category = await session.scalar(select(MeetingCategory).where(MeetingCategory.id == category_id).with_for_update())
        category.is_active = False
        await session.flush()
        pending = asyncio.create_task(create())
        await asyncio.wait_for(creation_started.wait(), timeout=5)
        await asyncio.sleep(0.05)
        assert not pending.done()
        await session.commit()
    assert await asyncio.wait_for(pending, timeout=10) == 409
    async with postgres_sessions() as session:
        assert await session.scalar(select(func.count(Meeting.id))) == 0


async def test_concurrent_duplicate_category_names_create_only_one_row(postgres_sessions):
    admin_id, _, _ = await seed_category(postgres_sessions)

    async def create():
        async with postgres_sessions() as session:
            admin = await session.get(User, admin_id)
            try:
                await create_meeting_category(MeetingCategoryCreate(name="  新類別  "), admin, session)
                return 201
            except HTTPException as exc:
                await session.rollback()
                return exc.status_code

    assert sorted(await asyncio.wait_for(asyncio.gather(create(), create()), timeout=10)) == [201, 409]
    async with postgres_sessions() as session:
        assert await session.scalar(select(func.count(MeetingCategory.id)).where(MeetingCategory.name == "新類別")) == 1
