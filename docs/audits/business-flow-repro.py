"""離線業務流程稽核：只使用記憶體 SQLite，不呼叫金流、不讀寫正式資料。

從專案根目錄執行：PYTHONPATH=backend python docs/audits/business-flow-repro.py
這是已知問題重現，不是期望正確行為的回歸測試；修復後 assert 可能失敗。
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import MissingGreenlet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base
from app.config import Settings
from app.domain import DomainError, remove_paid_quantity, validate_group_join
from app.integrations.payment_service import create_payment_attempt
from app.jobs import _reconcile_activities, _reconcile_member_proposals
from app.models import (
    Activity, ActivityRegistration, ActivityRegistrationStatus, ActivityStatus,
    FulfillmentMethod, FulfillmentState, FulfillmentStatus, GroupCampaign,
    GroupDecisionStatus, GroupIntakeStatus, MemberProposal, MemberProposalStatus,
    MemberProposalType, MemberProposalVote, MembershipType, Notification,
    Order, OrderFulfillment, OrderItem, OrderKind, PaymentStatus,
    Product, ProductCategory, ProposalOption, SalesChannel, SurplusDistribution,
    TargetType, TaxType, User, UserRole,
)
from app.routers.community import mark_activity_attendance
from app.routers.cooperative import SurplusRunRequest, confirm_surplus
from app.routers.orders import cancel_order, review_order_cancellation, update_fulfillment
from app.routers.groups import join_campaign, update_campaign
from app.schemas import (
    CampaignUpdate, CancelRequest, FulfillmentUpdate, GroupJoinRequest, MealCancellationReview,
)


@asynccontextmanager
async def memory_database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def new_user(email, *, admin=False):
    email = email.replace("@example.test", "@example.com")
    return User(
        email=email, display_name=email.split("@")[0], password_hash="fixture-only",
        user_role=UserRole.ADMIN if admin else UserRole.CUSTOMER,
    )


def paid_order(user, number, now):
    return Order(
        order_number=number, user=user, order_kind=OrderKind.REGULAR,
        sales_channel=SalesChannel.REGULAR,
        fulfillment_method=FulfillmentMethod.COOPERATIVE_PICKUP,
        membership_type_snapshot=MembershipType.MEMBER,
        amount_total=100, contact_email=user.email, payment_status=PaymentStatus.PAID,
        paid_at=now, fulfillment_status=FulfillmentStatus.PENDING_CONFIRMATION,
        items=[OrderItem(product_name="稽核商品", unit_label="份", quantity=1,
                         unit_price=100, subtotal=100, tax_type=TaxType.TAXABLE)],
        fulfillment=OrderFulfillment(method=FulfillmentMethod.COOPERATIVE_PICKUP,
                                     status=FulfillmentState.PENDING_CONFIRMATION),
    )


def reproduce_group_reopen():
    now = datetime.now(timezone.utc)
    campaign = GroupCampaign(
        paid_quantity=5, reserved_quantity=0, supply_cap=5, min_paid_quantity=2,
        per_user_cap=5, decision_status=GroupDecisionStatus.CONFIRMED,
        intake_status=GroupIntakeStatus.FULL, deadline=now + timedelta(days=1),
    )
    remove_paid_quantity(campaign, 1, now)
    assert campaign.paid_quantity == 4
    assert campaign.intake_status == GroupIntakeStatus.FULL
    try:
        validate_group_join(campaign, 1, 0, now)
    except DomainError as exc:
        print(f"BF-01 已重現：釋出後剩 1 份，仍拒絕加入：{exc}")
    else:
        raise AssertionError("BF-01 不再重現，請更新稽核結果")


async def reproduce_multiple_choice_job():
    now = datetime.now(timezone.utc)
    async with memory_database() as factory:
        async with factory() as session:
            user = new_user("vote@example.test")
            proposal = MemberProposal(
                created_by=user, title="選取取貨地點", body="fixture",
                proposal_type=MemberProposalType.MULTIPLE_CHOICE,
                status=MemberProposalStatus.VOTING, minimum_voters=1,
                discussion_ends_at=now - timedelta(days=2),
                voting_ends_at=now - timedelta(hours=1),
                options=[ProposalOption(id="option-a", label="A", position=0)],
            )
            session.add(proposal)
            await session.flush()
            session.add(MemberProposalVote(
                proposal_id=proposal.id, user_id=user.id, option_id="option-a",
            ))
            await session.commit()
        async with factory() as session:
            try:
                await _reconcile_member_proposals(session, now, 100)
            except MissingGreenlet:
                print("BF-02 已重現：新 session 結算到期多選投票，拋出 MissingGreenlet")
            else:
                raise AssertionError("BF-02 不再重現，請更新稽核結果")


async def reproduce_cancellation_race():
    now = datetime.now(timezone.utc)
    async with memory_database() as factory:
        async with factory() as session:
            buyer = new_user("buyer@example.test")
            admin = new_user("admin@example.test", admin=True)
            order = paid_order(buyer, "AUDIT-CANCEL-1", now)
            session.add_all([buyer, admin, order])
            await session.commit()
            requested = await cancel_order(order.id, CancelRequest(reason="行程變更"), buyer, session)
            request_id = requested.cancellation_request.id
            assert requested.cancellation_request.status == "pending"
            assert await session.scalar(select(func.count(Notification.id))) == 0
            preparing = await update_fulfillment(
                order.id, FulfillmentUpdate(status=FulfillmentStatus.PREPARING), admin, session,
            )
            assert preparing.cancellation_request.status == "pending"
            try:
                await review_order_cancellation(
                    order.id, request_id, MealCancellationReview(decision="approve", reason="同意退單"),
                    admin, session,
                )
            except HTTPException as exc:
                assert exc.status_code == 409
                print(f"BF-03 已重現：待審退單仍可備貨；隨後核准退單回 409：{exc.detail}")
            else:
                raise AssertionError("BF-03 不再重現，請更新稽核結果")


async def reproduce_surplus_duplicate_period():
    now = datetime.now(timezone.utc)
    async with memory_database() as factory:
        async with factory() as session:
            buyer = new_user("member@example.test")
            admin = new_user("finance@example.test", admin=True)
            session.add_all([buyer, admin, paid_order(buyer, "AUDIT-SURPLUS-1", now)])
            await session.commit()
            for label in ("本期", "本期重送"):
                result = await confirm_surplus(SurplusRunRequest(
                    label=label, starts_on=now.date() - timedelta(days=1),
                    ends_on=now.date() + timedelta(days=1), total_cost=0, reserve_percentage=0,
                ), admin, session)
                assert result["distributable_surplus"] == 100
            total = await session.scalar(select(func.sum(SurplusDistribution.distribution_amount)))
            assert total == 200
            print("BF-04 已重現：相同期間改標籤重送，100 元交易建立兩份、共 200 元分配紀錄")


async def reproduce_activity_attendance_correction():
    now = datetime.now(timezone.utc)
    async with memory_database() as factory:
        async with factory() as session:
            buyer = new_user("attendee@example.test")
            admin = new_user("activity-admin@example.test", admin=True)
            activity = Activity(
                created_by=admin, title="已結束活動", location="合作社", capacity=10,
                status=ActivityStatus.PUBLISHED, starts_at=now - timedelta(hours=2),
                ends_at=now - timedelta(minutes=1),
                registration_deadline=now - timedelta(hours=3),
                registrations=[ActivityRegistration(
                    user=buyer, status=ActivityRegistrationStatus.REGISTERED, queue_position=1,
                )],
            )
            session.add(activity)
            await session.commit()
            registration = activity.registrations[0]
            await _reconcile_activities(session, now, 100)
            assert registration.status == ActivityRegistrationStatus.NO_SHOW
            try:
                await mark_activity_attendance(
                    activity.id, registration.id, ActivityRegistrationStatus.ATTENDED, admin, session,
                )
            except HTTPException as exc:
                assert exc.status_code == 409
                print(f"BF-05 已重現：活動自動結束標未出席後，無法補登出席：{exc.detail}")
            else:
                raise AssertionError("BF-05 不再重現，請更新稽核結果")


async def reproduce_changed_campaign_target():
    now = datetime.now(timezone.utc)
    settings = Settings(_env_file=None, environment="test", sales_scope="all",
                        app_base_url="https://api.example.com", web_base_url="https://web.example.com",
                        ecpay_payment_merchant_id="fixture", ecpay_payment_hash_key="fixture",
                        ecpay_payment_hash_iv="fixture")
    async with memory_database() as factory:
        async with factory() as session:
            buyer = new_user("group-buyer@example.test")
            buyer.membership = None
            admin = new_user("group-admin@example.test", admin=True)
            products = [Product(slug=f"audit-{index}", name=name, unit="份",
                                category=ProductCategory.SEASONAL_PRODUCE,
                                member_price=100, nonmember_price=100, stock_quantity=10,
                                tax_type=TaxType.TAXABLE) for index, name in enumerate(["商品 A", "商品 B"])]
            session.add_all([buyer, admin, *products])
            await session.flush()
            campaign = GroupCampaign(
                title="原商品 A 團購", created_by=admin, target_type=TargetType.PRODUCT,
                target_id=products[0].id, member_price=100, nonmember_price=100,
                min_paid_quantity=2, supply_cap=10, per_user_cap=5,
                deadline=now + timedelta(days=1), estimated_pickup_start=now + timedelta(days=2),
                estimated_pickup_end=now + timedelta(days=3),
            )
            session.add(campaign)
            await session.commit()
            order = await join_campaign(campaign.id, GroupJoinRequest(
                quantity=1, contact_email=buyer.email,
                fulfillment_method=FulfillmentMethod.COOPERATIVE_PICKUP,
            ), buyer, session, settings)
            await update_campaign(campaign.id, CampaignUpdate(
                target_id=products[1].id, member_price=200, nonmember_price=200,
            ), admin, session)
            attempt = await create_payment_attempt(session, order.id, buyer, settings, now)
            assert attempt.amount == 100
            assert campaign.target_id == products[1].id
            assert order.items[0].product_name == "商品 A"
            print("BF-06 已重現：團購改成商品 B／200 元後，原商品 A／100 元未付款訂單仍可開付款頁")


async def main():
    reproduce_group_reopen()
    await reproduce_multiple_choice_job()
    await reproduce_cancellation_race()
    await reproduce_surplus_duplicate_period()
    await reproduce_activity_attendance_correction()
    await reproduce_changed_campaign_target()


if __name__ == "__main__":
    asyncio.run(main())
