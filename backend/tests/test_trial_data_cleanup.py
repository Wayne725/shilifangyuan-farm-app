from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select

from app.models import (
    AdminAudit, FulfillmentMethod, FulfillmentState, FulfillmentStatus, GroupCampaign, Meal, MealCancellationRequest, MealEvent,
    MealEventOffering, MealEventStatus, MembershipType, Notification, Order, OrderFulfillment, OrderItem,
    OrderItemOption, OrderKind, OutboxEvent, PaymentAttempt, PaymentStatus, PrintJob, Refund, RefundStatus,
    Product, SalesChannel, TargetType, TaxType, User, UserRole,
)
from app.trial_data_cleanup import apply_cleanup, plan_cleanup, report

NOW = datetime(2026, 11, 27, 8, tzinfo=timezone.utc)
CUTOFF = date(2026, 11, 30)
OLD = NOW - timedelta(days=3)


def taipei(day: int, hour: int = 11) -> datetime:
    return datetime(2026, 11, day, hour, tzinfo=timezone.utc) - timedelta(hours=8)


async def setup(session):
    admin = User(email="cleanup-admin@example.test", display_name="管理員", password_hash="test", user_role=UserRole.ADMIN)
    buyer = User(email="cleanup-buyer@example.test", display_name="社員", password_hash="test")
    meal = Meal(slug="cleanup-meal", name="測試便當", price=100)
    session.add_all([admin, buyer, meal])
    await session.flush()

    def event(day, title):
        row = MealEvent(title=title, location="合作社", status=MealEventStatus.PUBLISHED, created_by_id=admin.id,
                        ordering_starts_at=taipei(day, 0) - timedelta(days=2), ordering_ends_at=taipei(day, 0) - timedelta(hours=4),
                        pickup_starts_at=taipei(day), pickup_ends_at=taipei(day, 13))
        offering = MealEventOffering(event=row, meal=meal, price=100, capacity=100)
        session.add_all([row, offering])
        return row, offering

    events = {name: event(day, name) for name, day in [("empty-past", 20), ("past", 25), ("paid-past", 24), ("tomorrow", 28), ("launch", 30)]}
    product = Product(slug="cleanup-rice", name="白米", category="米", unit="包", member_price=100, nonmember_price=100)
    session.add(product)
    await session.flush()

    def campaign(title, deadline):
        row = GroupCampaign(title=title, target_type=TargetType.PRODUCT, target_id=product.id, member_price=100, nonmember_price=100,
                            min_paid_quantity=1, supply_cap=10, per_user_cap=5, deadline=deadline,
                            estimated_pickup_start=deadline + timedelta(days=1), estimated_pickup_end=deadline + timedelta(days=2),
                            created_by_id=admin.id)
        session.add(row)
        return row

    campaigns = {title: campaign(title, deadline) for title, deadline in [
        ("ended-empty", OLD), ("ended-test-order", OLD), ("ended-paid", OLD), ("open", NOW + timedelta(days=30))]}

    def order(label, *, channel=SalesChannel.MEAL_PREORDER, event_name=None, payment=PaymentStatus.EXPIRED, created_at=OLD, campaign_name=None):
        meal_event, offering = events[event_name] if event_name else (None, None)
        row = Order(
            order_number=label, user=buyer, order_kind=OrderKind.GROUP if channel == SalesChannel.GROUP else OrderKind.REGULAR,
            sales_channel=channel, membership_type_snapshot=MembershipType.NONMEMBER, amount_total=100,
            contact_email=buyer.email, payment_status=payment, fulfillment_status=FulfillmentStatus.PENDING_CONFIRMATION,
            created_at=created_at, meal_event=meal_event, group_campaign=campaigns[campaign_name] if campaign_name else None,
            items=[OrderItem(source_meal_offering_id=offering.id if offering else None, product_name="測試品項", unit_label="份",
                             quantity=1, unit_price=100, subtotal=100, tax_type=TaxType.TAX_EXEMPT,
                             selected_options=[OrderItemOption(group_name="主食", option_name="白飯", price_delta=0)])],
            fulfillment=OrderFulfillment(method=FulfillmentMethod.COOPERATIVE_PICKUP, status=FulfillmentState.PENDING_CONFIRMATION,
                                         pickup_at=meal_event.pickup_starts_at if meal_event else None),
        )
        session.add(row)
        return row

    await session.flush()
    orders = {
        "TEST-EXPIRED": order("TEST-EXPIRED", event_name="past"),
        "TEST-PAID": order("TEST-PAID", event_name="paid-past", payment=PaymentStatus.PAID),
        "LAUNCH": order("LAUNCH", event_name="launch", payment=PaymentStatus.PENDING),
        "GROUP-EXPIRED": order("GROUP-EXPIRED", channel=SalesChannel.GROUP, campaign_name="ended-test-order"),
        "GROUP-PAID": order("GROUP-PAID", channel=SalesChannel.GROUP, payment=PaymentStatus.PAID, campaign_name="ended-paid"),
        "REGULAR-REFUNDED": order("REGULAR-REFUNDED", channel=SalesChannel.REGULAR),
        "REGULAR-UNKNOWN": order("REGULAR-UNKNOWN", channel=SalesChannel.REGULAR),
        "REGULAR-FRESH": order("REGULAR-FRESH", channel=SalesChannel.REGULAR, payment=PaymentStatus.PENDING, created_at=NOW - timedelta(hours=2)),
    }
    await session.flush()
    expired = orders["TEST-EXPIRED"]
    session.add_all([
        PaymentAttempt(order=expired, merchant_trade_no="CLEANUP1", amount=100, status=PaymentStatus.EXPIRED, expires_at=OLD),
        PaymentAttempt(order=orders["REGULAR-UNKNOWN"], merchant_trade_no="CLEANUP2", amount=100, status=PaymentStatus.PENDING,
                       expires_at=OLD, settlement_unknown_since=OLD),
        Refund(order=orders["REGULAR-REFUNDED"], amount=100, status=RefundStatus.FAILED, requested_by_id=admin.id, reason="測試"),
        MealCancellationRequest(order=expired, requested_by_id=buyer.id, reason="測試退單"),
        Notification(user_id=buyer.id, event_type="meal_order", title="訂單", body="測試", data={"order_id": expired.id}),
        Notification(user_id=buyer.id, event_type="meal_event", title="場次", body="測試",
                     data={"meal_event_id": events["empty-past"][0].id}),
        Notification(user_id=buyer.id, event_type="meal_order", title="保留", body="測試", data={"order_id": orders["TEST-PAID"].id}),
        OutboxEvent(event_type="meal.order_expired", aggregate_type="order", aggregate_id=expired.id, payload={"order_id": expired.id}),
        PrintJob(event_key="cleanup-print", order_id=expired.id, kind="order", mode="dry_run", printer_fingerprint="x", snapshot={}),
    ])
    await session.commit()
    return admin, orders, events


async def count(session, model, *where):
    return await session.scalar(select(func.count()).select_from(model).where(*where))


async def test_plan_lists_only_old_unpaid_uninvoiced_orders_and_empty_past_events(database_session):
    session = database_session
    _, orders, events = await setup(session)
    plan = await plan_cleanup(session, CUTOFF, NOW)
    assert sorted(entry["order_number"] for entry in plan.orders) == ["GROUP-EXPIRED", "TEST-EXPIRED"]
    kept = {entry["order_number"]: entry["reasons"] for entry in plan.kept}
    assert kept == {
        "TEST-PAID": ["已付款或退款"], "GROUP-PAID": ["已付款或退款"], "REGULAR-REFUNDED": ["有退款紀錄"],
        "REGULAR-UNKNOWN": ["有付款紀錄"], "REGULAR-FRESH": ["一天內建立"],
    }
    assert "LAUNCH" not in kept
    assert [entry["title"] for entry in plan.events] == ["empty-past", "past"]
    assert sorted(entry["title"] for entry in plan.campaigns) == ["ended-empty", "ended-test-order"]
    text = report(plan)
    assert "TEST-EXPIRED" in text and "一天內建立" in text and "empty-past" in text
    assert await count(session, Order) == 8, "只列清單不能刪資料"


async def test_apply_deletes_children_keeps_members_and_records_audit(database_session):
    session = database_session
    admin, orders, events = await setup(session)
    expired_id = orders["TEST-EXPIRED"].id
    plan = await plan_cleanup(session, CUTOFF, NOW)
    counts = await apply_cleanup(session, plan, admin)
    await session.commit()
    assert counts["orders"] == 2 and counts["meal_events"] == 2 and counts["group_campaigns"] == 2
    assert {row for row in await session.scalars(select(Order.order_number))} == {
        "TEST-PAID", "GROUP-PAID", "LAUNCH", "REGULAR-REFUNDED", "REGULAR-UNKNOWN", "REGULAR-FRESH"}
    assert {row for row in await session.scalars(select(GroupCampaign.title))} == {"ended-paid", "open"}
    for model, column in [(OrderItem, OrderItem.order_id), (OrderFulfillment, OrderFulfillment.order_id),
                          (PaymentAttempt, PaymentAttempt.order_id), (MealCancellationRequest, MealCancellationRequest.order_id),
                          (PrintJob, PrintJob.order_id)]:
        assert await count(session, model, column == expired_id) == 0
    assert await count(session, OrderItemOption) == 6
    assert await count(session, OutboxEvent) == 0
    assert [row.title for row in await session.scalars(select(Notification))] == ["保留"]
    assert {row for row in await session.scalars(select(MealEvent.title))} == {"paid-past", "tomorrow", "launch"}
    assert await count(session, MealEventOffering) == 3
    assert await count(session, User) == 2
    audit = await session.scalar(select(AdminAudit).where(AdminAudit.action == "maintenance.trial_data_cleanup"))
    assert audit.actor_id == admin.id
    assert sorted(audit.data["order_numbers"]) == ["GROUP-EXPIRED", "TEST-EXPIRED"]
    assert audit.data["counts"]["orders"] == 2
    assert sorted(audit.data["group_campaigns"]) == ["ended-empty", "ended-test-order"]
    assert (await plan_cleanup(session, CUTOFF, NOW)).orders == []


async def test_apply_aborts_when_an_order_was_paid_after_planning(database_session):
    session = database_session
    admin, orders, _events = await setup(session)
    plan = await plan_cleanup(session, CUTOFF, NOW)
    orders["TEST-EXPIRED"].payment_status = PaymentStatus.PAID
    await session.flush()
    try:
        await apply_cleanup(session, plan, admin)
    except SystemExit as error:
        assert "狀態改變" in str(error)
    else:
        raise AssertionError("付款後不能刪除")
    await session.rollback()
    assert await count(session, Order) == 8
