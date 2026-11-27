"""試營運測試資料清除：預設只列清單，加 --apply 才刪除。

python -m app.trial_data_cleanup                 # 只列出會刪／會保留的資料
python -m app.trial_data_cleanup --apply         # 實際刪除（單一交易）

只刪「沒收過錢、沒開過發票」的訂單：便當限取餐日早於 --cutoff，一般與團購不限日期。
之後刪掉已結束且沒有剩餘訂單的便當場次，以及付款已截止、無人付款、也沒有剩餘訂單的共同購買。
社員、名冊、帳號、點數與稽核紀錄都不動。
"""

import argparse
import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    ActivityRefundRequest, AdminAudit, GroupCampaign, InventoryReservation, Invoice, InvoiceStatus, MealCancellationRequest, MealEvent,
    MealEventOffering, Notification, Order, OrderAmendment, OrderFulfillment, OrderItem, OrderItemOption, OutboxEvent,
    PaymentAttempt, PaymentStatus, PointTransaction, PrintJob, RecoveryHold, Refund, ReservationStatus, SalesChannel,
    Shipment, User, UserRole, WalkInSale, Wish,
)

TAIPEI = ZoneInfo("Asia/Taipei")
UNPAID = {PaymentStatus.PENDING, PaymentStatus.EXPIRED, PaymentStatus.FAILED}
NO_INVOICE = {InvoiceStatus.NOT_ELIGIBLE, InvoiceStatus.PENDING}
MIN_AGE = timedelta(days=1)


@dataclass
class CleanupPlan:
    cutoff: date
    orders: list[dict] = field(default_factory=list)
    kept: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    campaigns: list[dict] = field(default_factory=list)


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def _taipei_day(value: datetime | None) -> str:
    return _aware(value).astimezone(TAIPEI).strftime("%Y-%m-%d") if value else "未指定"


async def _ids(session: AsyncSession, query) -> set[str]:
    return {str(value) for value in await session.scalars(query)}


async def plan_cleanup(session: AsyncSession, cutoff: date, now: datetime | None = None) -> CleanupPlan:
    now = now or datetime.now(timezone.utc)
    cutoff_at = datetime.combine(cutoff, time.min, TAIPEI).astimezone(timezone.utc)
    pickup = func.coalesce(OrderFulfillment.pickup_at, OrderFulfillment.pickup_starts_at, MealEvent.pickup_starts_at)
    rows = (await session.execute(
        select(Order, pickup.label("pickup"))
        .outerjoin(OrderFulfillment, OrderFulfillment.order_id == Order.id)
        .outerjoin(MealEvent, MealEvent.id == Order.meal_event_id)
        .where(or_(Order.sales_channel != SalesChannel.MEAL_PREORDER, pickup < cutoff_at))
        .order_by(Order.created_at)
    )).all()
    ids = [order.id for order, _ in rows]
    attempts = select(PaymentAttempt.id).where(PaymentAttempt.order_id.in_(ids))
    blockers = {
        "有付款紀錄": await _ids(session, select(PaymentAttempt.order_id).where(PaymentAttempt.order_id.in_(ids), or_(
            PaymentAttempt.status.not_in(UNPAID), PaymentAttempt.settlement_unknown_since.is_not(None)))),
        "有發票紀錄": await _ids(session, select(Invoice.order_id).where(Invoice.order_id.in_(ids))),
        "有退款紀錄": await _ids(session, select(Refund.order_id).where(Refund.order_id.in_(ids))),
        "現場開票銷售": await _ids(session, select(WalkInSale.order_id).where(WalkInSale.order_id.in_(ids))),
        "有點數紀錄": await _ids(session, select(PointTransaction.reference_id).where(PointTransaction.reference_id.in_(ids))),
        "復原保留中": await _ids(session, select(Order.id).where(Order.id.in_(ids), or_(
            Order.id.in_(select(RecoveryHold.reference)), Order.order_number.in_(select(RecoveryHold.reference))))),
        "活動退款引用": await _ids(session, select(PaymentAttempt.order_id).where(
            PaymentAttempt.id.in_(select(ActivityRefundRequest.payment_attempt_id).where(ActivityRefundRequest.payment_attempt_id.in_(attempts))))),
        "商品保留中": await _ids(session, select(InventoryReservation.order_id).where(
            InventoryReservation.order_id.in_(ids), InventoryReservation.status == ReservationStatus.ACTIVE,
            InventoryReservation.source_meal_offering_id.is_(None))),
    }
    plan = CleanupPlan(cutoff=cutoff)
    for order, pickup_at in rows:
        reasons = [reason for reason, blocked in blockers.items() if order.id in blocked]
        if order.payment_status not in UNPAID or order.paid_at is not None:
            reasons.insert(0, "已付款或退款")
        if order.invoice_status not in NO_INVOICE:
            reasons.insert(0, "已開票或開票中")
        if _aware(order.created_at) > now - MIN_AGE:
            reasons.append("一天內建立")
        entry = {
            "id": order.id, "order_number": order.order_number, "channel": order.sales_channel.value,
            "day": _taipei_day(pickup_at if order.sales_channel == SalesChannel.MEAL_PREORDER else order.created_at),
            "payment_status": order.payment_status.value, "cancelled": order.cancelled_at is not None,
        }
        (plan.kept if reasons else plan.orders).append({**entry, "reasons": reasons} if reasons else entry)

    removed = [entry["id"] for entry in plan.orders]
    remaining = select(Order.meal_event_id).where(Order.meal_event_id.is_not(None), Order.id.not_in(removed))
    events = (await session.scalars(select(MealEvent).where(
        MealEvent.pickup_starts_at < cutoff_at, MealEvent.pickup_ends_at < now, MealEvent.id.not_in(remaining),
    ).order_by(MealEvent.pickup_starts_at))).all()
    plan.events = [{"id": event.id, "day": _taipei_day(event.pickup_starts_at), "period": event.meal_period or "特殊",
                    "title": event.title, "status": event.status.value} for event in events]
    remaining_campaigns = select(Order.group_campaign_id).where(Order.group_campaign_id.is_not(None), Order.id.not_in(removed))
    campaigns = (await session.scalars(select(GroupCampaign).where(
        GroupCampaign.deadline < now, GroupCampaign.paid_quantity == 0, GroupCampaign.id.not_in(remaining_campaigns),
    ).order_by(GroupCampaign.deadline))).all()
    plan.campaigns = [{"id": campaign.id, "title": campaign.title, "deadline": _taipei_day(campaign.deadline)} for campaign in campaigns]
    return plan


async def _actor(session: AsyncSession, actor_id: str | None) -> User:
    admins = (await session.scalars(select(User).where(User.user_role == UserRole.ADMIN).order_by(User.created_at))).all()
    if actor_id:
        actor = next((admin for admin in admins if admin.id == actor_id), None)
        if actor is None:
            raise SystemExit("--actor-id 必須是管理員帳號")
        return actor
    if len(admins) != 1:
        listing = "\n".join(f"  {admin.id} {admin.display_name}" for admin in admins)
        raise SystemExit(f"有 {len(admins)} 個管理員，請用 --actor-id 指定執行人：\n{listing}")
    return admins[0]


async def apply_cleanup(session: AsyncSession, plan: CleanupPlan, actor: User) -> dict:
    order_ids = [entry["id"] for entry in plan.orders]
    event_ids = [entry["id"] for entry in plan.events]
    campaign_ids = [entry["id"] for entry in plan.campaigns]
    counts: dict[str, int] = {}

    async def run(name: str, statement) -> None:
        counts[name] = counts.get(name, 0) + ((await session.execute(statement)).rowcount or 0)

    if order_ids:
        still_unpaid = await _ids(session, select(Order.id).where(
            Order.id.in_(order_ids), Order.payment_status.in_(UNPAID), Order.paid_at.is_(None),
            Order.invoice_status.in_(NO_INVOICE)).with_for_update())
        if still_unpaid != set(order_ids):
            raise SystemExit(f"有 {len(set(order_ids) - still_unpaid)} 筆訂單在列清單後狀態改變，已全部取消，請重新執行")
        items = select(OrderItem.id).where(OrderItem.order_id.in_(order_ids))
        fulfillments = select(OrderFulfillment.id).where(OrderFulfillment.order_id.in_(order_ids))
        await run("outbox_events", delete(OutboxEvent).where(or_(
            OutboxEvent.aggregate_id.in_(order_ids), OutboxEvent.payload["order_id"].as_string().in_(order_ids))))
        await run("notifications", delete(Notification).where(Notification.data["order_id"].as_string().in_(order_ids)))
        await session.execute(update(PrintJob).where(PrintJob.order_id.in_(order_ids)).values(reprint_of_id=None))
        await run("print_jobs", delete(PrintJob).where(PrintJob.order_id.in_(order_ids)))
        await run("order_item_options", delete(OrderItemOption).where(OrderItemOption.order_item_id.in_(items)))
        await run("order_items", delete(OrderItem).where(OrderItem.order_id.in_(order_ids)))
        await run("shipments", delete(Shipment).where(Shipment.order_fulfillment_id.in_(fulfillments)))
        await run("order_fulfillments", delete(OrderFulfillment).where(OrderFulfillment.order_id.in_(order_ids)))
        await run("inventory_reservations", delete(InventoryReservation).where(InventoryReservation.order_id.in_(order_ids)))
        await run("payment_attempts", delete(PaymentAttempt).where(PaymentAttempt.order_id.in_(order_ids)))
        await run("meal_cancellation_requests", delete(MealCancellationRequest).where(MealCancellationRequest.order_id.in_(order_ids)))
        await run("order_amendments", delete(OrderAmendment).where(OrderAmendment.order_id.in_(order_ids)))
        await run("orders", delete(Order).where(Order.id.in_(order_ids)))
    if event_ids:
        offerings = select(MealEventOffering.id).where(MealEventOffering.meal_event_id.in_(event_ids))
        await run("notifications", delete(Notification).where(Notification.data["meal_event_id"].as_string().in_(event_ids)))
        await run("inventory_reservations", delete(InventoryReservation).where(InventoryReservation.source_meal_offering_id.in_(offerings)))
        await run("meal_event_offerings", delete(MealEventOffering).where(MealEventOffering.meal_event_id.in_(event_ids)))
        await run("meal_events", delete(MealEvent).where(MealEvent.id.in_(event_ids)))
    if campaign_ids:
        await run("outbox_events", delete(OutboxEvent).where(OutboxEvent.aggregate_id.in_(campaign_ids)))
        await run("notifications", delete(Notification).where(Notification.data["campaign_id"].as_string().in_(campaign_ids)))
        await session.execute(update(Wish).where(Wish.launched_campaign_id.in_(campaign_ids)).values(launched_campaign_id=None))
        await run("inventory_reservations", delete(InventoryReservation).where(InventoryReservation.group_campaign_id.in_(campaign_ids)))
        await run("group_campaigns", delete(GroupCampaign).where(GroupCampaign.id.in_(campaign_ids)))
    session.add(AdminAudit(
        actor_id=actor.id, action="maintenance.trial_data_cleanup", aggregate_type="maintenance", aggregate_id=str(uuid.uuid4()),
        reason=f"清除試營運測試資料（便當取餐日早於 {plan.cutoff.isoformat()}）",
        data={"cutoff": plan.cutoff.isoformat(), "counts": counts,
              "order_numbers": [entry["order_number"] for entry in plan.orders],
              "meal_events": [f"{entry['day']} {entry['period']} {entry['title']}" for entry in plan.events],
              "group_campaigns": [entry["title"] for entry in plan.campaigns]},
    ))
    return counts


def report(plan: CleanupPlan) -> str:
    lines = [f"截止日：{plan.cutoff.isoformat()}（便當取餐日早於此日才會刪）", "",
             f"【會刪除的訂單】{len(plan.orders)} 筆"]
    lines += [f"  {e['order_number']}  {e['channel']}  {e['day']}  {e['payment_status']}{'  已取消' if e['cancelled'] else ''}" for e in plan.orders]
    lines += ["", f"【保留的訂單】{len(plan.kept)} 筆"]
    lines += [f"  {e['order_number']}  {e['channel']}  {e['day']}  {e['payment_status']}：{'、'.join(e['reasons'])}" for e in plan.kept]
    lines += ["", f"【會刪除的過期場次】{len(plan.events)} 個"]
    lines += [f"  {e['day']}  {e['period']}  {e['title']}  {e['status']}" for e in plan.events]
    lines += ["", f"【會刪除的已結束共同購買】{len(plan.campaigns)} 個"]
    lines += [f"  {e['deadline']} 截止  {e['title']}" for e in plan.campaigns]
    return "\n".join(lines)


async def main(args: argparse.Namespace) -> None:
    from .database import SessionLocal

    async with SessionLocal() as session:
        async with session.begin():
            plan = await plan_cleanup(session, args.cutoff)
            print(report(plan))
            if not args.apply:
                print("\n目前只列清單，沒有刪除任何資料。確認後加 --apply 執行。")
                return
            actor = await _actor(session, args.actor_id)
            counts = await apply_cleanup(session, plan, actor)
        print(f"\n已刪除：{counts}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="清除試營運測試資料")
    parser.add_argument("--cutoff", type=date.fromisoformat, default=date(2026, 11, 30))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--actor-id")
    asyncio.run(main(parser.parse_args()))
