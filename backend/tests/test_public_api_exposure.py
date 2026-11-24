from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select

import app.jobs as jobs
import app.main as main_module
from app.config import get_settings
from app.database import get_session
from app.models import (
    Activity, ActivityStatus, MealEvent, MealEventStatus, Notification,
    OutboxEvent, Product, ProposalStatus, ProposalTargetType, Supplier, Vote, VoteProposal,
)
from app.routers.catalog import catalog_router
from app.routers.proposals import proposals_router
from tests.support import auth_headers, make_test_settings, tomorrow_meal_timeline
from tests.test_v2_routers import v2_context


IDENTITIES = [None, "member_b", "member_a", "admin"]


@pytest.fixture
async def exposure_context(v2_context):
    ctx = v2_context
    session = ctx["session"]
    ctx["client"]._transport.app.include_router(catalog_router)
    ctx["client"]._transport.app.include_router(proposals_router)
    now = datetime.now(timezone.utc)
    supplier = Supplier(
        business_name="公開供應者", responsible_person_encrypted="private",
        contact_person_encrypted="private", phone_encrypted="private",
        email_encrypted="private", bank_account_encrypted="private",
    )
    product = Product(
        slug="public-api-product", name="欄位測試商品", category="當季蔬果", unit="袋",
        member_price=90, nonmember_price=110, stock_quantity=12,
        supplier=supplier, sku="INTERNAL-SKU", product_number="INTERNAL-NUMBER",
    )
    activity = Activity(
        created_by_id=ctx["member_a"].id, title="公開活動", location="合作社",
        starts_at=now + timedelta(days=3), ends_at=now + timedelta(days=3, hours=2),
        registration_deadline=now + timedelta(days=2), capacity=10,
        status=ActivityStatus.PUBLISHED, reviewed_at=now, review_reason="活動內部審核",
        registrations=[],
    )
    proposal = VoteProposal(
        proposer_id=ctx["member_a"].id, target_type=ProposalTargetType.CUSTOM,
        target_name_snapshot="公開提案", request_details="需求內容",
        status=ProposalStatus.VOTING, threshold=1,
        deadline=now + timedelta(days=1), review_reason="提案內部審核",
        votes=[Vote(user_id=ctx["member_a"].id, estimated_quantity=2)],
    )
    meal_event = MealEvent(
        created_by_id=ctx["member_a"].id, title="公開便當", location="合作社",
        status=MealEventStatus.PUBLISHED, offerings=[], **tomorrow_meal_timeline(now),
    )
    session.add_all([product, activity, proposal, meal_event])
    await session.commit()
    yield {**ctx, "product": product, "activity": activity, "proposal": proposal, "event": meal_event}


@pytest.mark.parametrize("identity", IDENTITIES)
async def test_public_products_exclude_internal_fields_and_admin_has_separate_read(exposure_context, identity):
    ctx = exposure_context
    headers = auth_headers(ctx[identity]) if identity else {}
    product = ctx["product"]
    for path in ("/v1/products", f"/v1/products/{product.id}"):
        response = await ctx["client"].get(path, headers=headers)
        assert response.status_code == 200, response.text
        data = response.json()[0] if isinstance(response.json(), list) else response.json()
        assert {"supplier_id", "sku", "product_number"}.isdisjoint(data)
        assert data["supplier_name"] == "公開供應者"
        assert data["stock_quantity"] == 12
    response = await ctx["client"].get("/v1/admin/products", headers=headers)
    if identity == "admin":
        assert response.status_code == 200
        assert response.json()[0]["supplier_id"] == product.supplier_id
        assert response.json()[0]["sku"] == "INTERNAL-SKU"
        assert response.json()[0]["product_number"] == "INTERNAL-NUMBER"
    else:
        assert response.status_code == (403 if identity else 401)


@pytest.mark.parametrize("identity", IDENTITIES)
@pytest.mark.parametrize("resource", ["activity", "proposal"])
async def test_creator_ids_are_hidden_and_reviews_are_private(exposure_context, identity, resource):
    ctx = exposure_context
    headers = auth_headers(ctx[identity]) if identity else {}
    prefix = "/v1/activities" if resource == "activity" else "/v1/vote-proposals"
    for path in (prefix, f"{prefix}/{ctx[resource].id}"):
        response = await ctx["client"].get(path, headers=headers)
        assert response.status_code == 200, response.text
        data = response.json()[0] if isinstance(response.json(), list) else response.json()
        assert {"created_by_id", "proposer_id"}.isdisjoint(data)
        assert data["is_mine"] is (identity == "member_a")
        can_review = identity in {"member_a", "admin"}
        assert data["review_reason"] == (ctx[resource].review_reason if can_review else None)
        if resource == "activity":
            assert (data["reviewed_at"] is not None) is can_review
    if identity == "admin" and resource == "activity":
        response = await ctx["client"].get("/v1/admin/activities", headers=headers)
        assert response.json()[0]["created_by_id"] == ctx["member_a"].id
        assert response.json()[0]["reviewed_at"] is not None


@pytest.mark.parametrize("status,deadline_offset,conversion_offset,threshold,expected", [
    (ProposalStatus.VOTING, 1, None, 1, "voting"),
    (ProposalStatus.VOTING, -1, None, 2, "ended_unmet"),
    (ProposalStatus.VOTING, -1, None, 1, "conversion_pending"),
    (ProposalStatus.VOTING, -1000, None, 1, "expired_unhandled"),
    (ProposalStatus.CONVERSION_PENDING, -2, 1, 1, "conversion_pending"),
    (ProposalStatus.CONVERSION_PENDING, -2, -1, 1, "expired_unhandled"),
])
async def test_repeated_proposal_gets_never_write(
    exposure_context, monkeypatch, status, deadline_offset, conversion_offset, threshold, expected,
):
    ctx = exposure_context
    session, proposal = ctx["session"], ctx["proposal"]
    now = datetime.now(timezone.utc)
    proposal.status = status
    proposal.deadline = now + timedelta(hours=deadline_offset)
    proposal.conversion_deadline = now + timedelta(hours=conversion_offset) if conversion_offset is not None else None
    proposal.threshold = threshold
    await session.commit()
    original_deadline = proposal.conversion_deadline
    writes, commits = [], []
    settings = make_test_settings()
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    monkeypatch.setattr(main_module, "should_reconcile_now", lambda: False)
    application = main_module.create_app()

    async def session_override():
        yield session

    application.dependency_overrides[get_session] = session_override
    application.dependency_overrides[get_settings] = lambda: settings

    def capture_write(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
            writes.append(statement)

    def capture_commit(sync_session):
        commits.append(True)

    event.listen(session.bind.sync_engine, "before_cursor_execute", capture_write)
    event.listen(session.sync_session, "before_commit", capture_commit)
    try:
        async with AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client:
            for identity in IDENTITIES:
                headers = auth_headers(ctx[identity]) if identity else {}
                for _ in range(2):
                    for path in ("/v1/vote-proposals", f"/v1/vote-proposals/{proposal.id}"):
                        response = await client.get(path, headers=headers)
                        assert response.status_code == 200, response.text
                        data = response.json()[0] if isinstance(response.json(), list) else response.json()
                        assert data["status"] == expected
                        if expected in {"conversion_pending", "expired_unhandled"}:
                            assert data["conversion_deadline"] is not None
        assert writes == commits == []
        assert not session.new and not session.dirty and not session.deleted
        await session.refresh(proposal)
        assert proposal.status == status
        assert proposal.conversion_deadline == (
            original_deadline.replace(tzinfo=None) if original_deadline else None
        )
        assert proposal.frozen_vote_count is None
        for model in (Notification, OutboxEvent):
            assert await session.scalar(select(func.count()).select_from(model)) == 0
    finally:
        event.remove(session.bind.sync_engine, "before_cursor_execute", capture_write)
        event.remove(session.sync_session, "before_commit", capture_commit)


@pytest.mark.parametrize("threshold,hours,expected", [
    (2, 1, ProposalStatus.ENDED_UNMET),
    (1, 1, ProposalStatus.CONVERSION_PENDING),
    (1, 1000, ProposalStatus.EXPIRED_UNHANDLED),
])
async def test_existing_reconcile_advances_proposals_once(exposure_context, monkeypatch, threshold, hours, expected):
    ctx = exposure_context
    proposal, session = ctx["proposal"], ctx["session"]
    now = datetime.now(timezone.utc)
    proposal.deadline = now - timedelta(hours=hours)
    proposal.threshold = threshold
    await session.commit()

    async def skip_external_delivery(*args):
        return 0, 0

    monkeypatch.setattr(jobs, "_process_outbox", skip_external_delivery)
    report = await jobs.reconcile_once(session, make_test_settings(), now=now)
    assert report.proposals == 1
    await session.refresh(proposal)
    assert proposal.status == expected
    assert proposal.frozen_vote_count == 1
    notifications = select(func.count(Notification.id)).where(Notification.user_id == ctx["member_a"].id)
    assert await session.scalar(notifications) == 1
    repeated = await jobs.reconcile_once(session, make_test_settings(), now=now)
    assert repeated.proposals == 0
    assert await session.scalar(notifications) == 1
