from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import String, and_, case, cast, func, literal, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from .domain import aware
from .integrations.pii_crypto import VersionedPIICipher
from .meal_booking import TAIPEI
from .meal_finance import order_receipts
from .member_claims import normalize_email, normalize_member_number, normalize_name, roster_aad
from .models import (
    Invoice, MemberProfile, MemberRosterEntry, Membership, MembershipType, Order, Refund, RefundStatus,
    SalesChannel, User, WalkInSale,
)


CATEGORIES = {
    "regular": "一般商品",
    "group": "共同購買",
    "meal": "便當",
    "business": "會餐",
    "walk_in": "現場銷售",
}
STATUS_LABELS = {
    "active": "正式社員",
    "inactive": "未啟用",
    "pending_payment": "待繳款",
    "trainee": "實習社員",
    "suspended": "已停權",
    "resigned": "已退社",
    "terminated": "已終止",
}
METHOD_LABELS = {"online": "線上", "cash_on_pickup": "現金", "cash_on_delivery": "現金"}
ReportDate = Annotated[date, Field(ge=date(2, 1, 1), le=date(9998, 12, 31))]


class SpendPeriod(BaseModel):
    starts_on: ReportDate | None = None
    ends_on: ReportDate | None = None
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_period(self):
        if (self.starts_on is None) != (self.ends_on is None):
            raise ValueError("請同時提供開始和結束日期")
        if self.starts_on is not None and self.ends_on is not None and self.ends_on < self.starts_on:
            raise ValueError("結束日期不能早於開始日期")
        return self

    def dates(self) -> tuple[date, date]:
        if self.starts_on is not None and self.ends_on is not None:
            return self.starts_on, self.ends_on
        year = datetime.now(TAIPEI).year
        return date(year, 1, 1), date(year, 12, 31)

    def bounds(self) -> tuple[datetime, datetime]:
        start, end = self.dates()
        return (
            datetime.combine(start, time.min, TAIPEI).astimezone(timezone.utc),
            datetime.combine(end + timedelta(days=1), time.min, TAIPEI).astimezone(timezone.utc),
        )


class SpendFilters(SpendPeriod):
    q: str = Field(default="", max_length=120)
    member_number: str = Field(default="", max_length=32)
    status: Literal["active", "all"] = "active"
    sort: Literal["amount_desc", "amount_asc", "name_asc", "name_desc"] = "amount_desc"


def _roster_memberships():
    claimed = aliased(Membership)
    numbered = aliased(Membership)
    return (
        select(MemberRosterEntry.id.label("roster_id"), func.coalesce(claimed.id, numbered.id).label("membership_id"))
        .outerjoin(claimed, claimed.user_id == MemberRosterEntry.claimed_user_id)
        .outerjoin(numbered, numbered.member_number == MemberRosterEntry.member_number)
        .cte("roster_memberships")
    )


def _people(roster_memberships):
    members = (
        select(
            Membership.id.label("id"),
            func.coalesce(Membership.member_number, Membership.trainee_number, "").label("member_number"),
            cast(Membership.status, String).label("status"),
            User.id.label("user_id"),
            MemberProfile.legal_name_encrypted.label("name_encrypted"),
            User.display_name.label("name"),
            User.email.label("email"),
            User.notification_email.label("notification_email"),
            literal(None, type_=String).label("email_encrypted"),
        )
        .join(User, User.id == Membership.user_id)
        .outerjoin(MemberProfile, MemberProfile.user_id == User.id)
    )
    roster = (
        select(
            MemberRosterEntry.id.label("id"), MemberRosterEntry.member_number,
            case((MemberRosterEntry.is_active.is_(True), "active"), else_="inactive").label("status"),
            literal(None, type_=String).label("user_id"),
            MemberRosterEntry.legal_name_encrypted.label("name_encrypted"),
            literal(None, type_=String).label("name"),
            literal(None, type_=String).label("email"),
            literal(None, type_=String).label("notification_email"),
            MemberRosterEntry.email_encrypted,
        )
        .join(roster_memberships, roster_memberships.c.roster_id == MemberRosterEntry.id)
        .where(roster_memberships.c.membership_id.is_(None))
    )
    return union_all(members, roster).cte("spend_people")


def _orders(roster_memberships, person_id: str | None = None):
    membership_id = func.coalesce(
        Order.verified_counter_membership_id, roster_memberships.c.membership_id,
        Order.verified_counter_roster_entry_id, Membership.id,
    )
    category = case(
        (or_(Order.customer_kind == "walk_in", WalkInSale.order_id.is_not(None)), "walk_in"),
        (Order.meal_booking_type == "business", "business"),
        (Order.sales_channel == SalesChannel.MEAL_PREORDER, "meal"),
        (Order.sales_channel == SalesChannel.GROUP, "group"),
        else_="regular",
    )
    query = (
        select(Order.id.label("order_id"), membership_id.label("person_id"), category.label("category"))
        .select_from(Order)
        .outerjoin(Membership, Membership.user_id == Order.user_id)
        .outerjoin(roster_memberships, roster_memberships.c.roster_id == Order.verified_counter_roster_entry_id)
        .outerjoin(WalkInSale, WalkInSale.order_id == Order.id)
        .where(Order.membership_type_snapshot.in_([MembershipType.MEMBER, MembershipType.TRAINEE]))
    )
    return query.where(membership_id == person_id) if person_id else query.where(membership_id.is_not(None))


def _transactions(period: SpendPeriod, roster_memberships, person_id: str | None = None):
    start, end = period.bounds()
    orders = _orders(roster_memberships, person_id).cte("member_orders")
    receipts = order_receipts(
        select(orders.c.order_id, orders.c.person_id, orders.c.category).join(Order, Order.id == orders.c.order_id),
    )
    payments = select(
        receipts.c.order_id, receipts.c.person_id, receipts.c.category,
        receipts.c.transaction_id, receipts.c.occurred_at,
        receipts.c.amount.label("amount"), literal(0).label("refunded"),
        literal("payment").label("kind"),
    ).where(receipts.c.occurred_at >= start, receipts.c.occurred_at < end)
    refunds = (
        select(
            orders.c.order_id, orders.c.person_id, orders.c.category,
            Refund.id.label("transaction_id"), Refund.completed_at.label("occurred_at"),
            literal(0).label("amount"), Refund.amount.label("refunded"),
            literal("refund").label("kind"),
        )
        .join(Refund, Refund.order_id == orders.c.order_id)
        .where(Refund.status == RefundStatus.COMPLETED, Refund.completed_at >= start, Refund.completed_at < end)
    )
    return union_all(payments, refunds).cte("member_transactions")


def _profile(row, cipher: VersionedPIICipher):
    aad = f"member-profile:{row.user_id}" if row.user_id else roster_aad(row.id)
    name = cipher.decrypt_text(row.name_encrypted, associated_data=aad) if row.name_encrypted else row.name
    email = cipher.decrypt_text(row.email_encrypted, associated_data=aad) if row.email_encrypted else row.email or row.notification_email
    return {
        "id": row.id, "member_number": row.member_number, "status": row.status,
        "name": name or row.name or "", "_emails": (email or "", row.notification_email or ""),
        "status_label": STATUS_LABELS[row.status],
    }


def _summary_row(row, cipher):
    return {
        **_profile(row, cipher),
        "net_amount": row.net_amount,
        "order_count": row.order_count,
        "latest_paid_date": aware(row.latest_paid_at).astimezone(TAIPEI).date() if row.latest_paid_at else None,
        "categories": {category: getattr(row, category) for category in CATEGORIES},
    }


def _matches(profile: dict, keyword: str):
    number = normalize_member_number(keyword)
    name = normalize_name(keyword).casefold()
    address = normalize_email(keyword)
    return (
        (bool(number) and number in normalize_member_number(profile["member_number"]))
        or (bool(name) and name in normalize_name(profile["name"]).casefold())
        or (bool(address) and any(address in normalize_email(email) for email in profile["_emails"]))
    )


def _public_profile(profile):
    return {key: value for key, value in profile.items() if key != "_emails"}


async def spend_summary(session: AsyncSession, filters: SpendFilters, cipher: VersionedPIICipher, *, export=False):
    roster_memberships = _roster_memberships()
    people = _people(roster_memberships)
    transactions = _transactions(filters, roster_memberships)
    net_amount = transactions.c.amount - transactions.c.refunded
    grouped = select(
        transactions.c.person_id, func.sum(net_amount).label("net_amount"),
        func.count(func.distinct(case((transactions.c.kind == "payment", transactions.c.order_id)))).label("order_count"),
        func.max(case((transactions.c.kind == "payment", transactions.c.occurred_at))).label("latest_paid_at"),
        *(func.sum(case((transactions.c.category == category, net_amount), else_=0)).label(category) for category in CATEGORIES),
    ).group_by(transactions.c.person_id).cte("member_spend_summary")
    query = select(
        *people.c,
        func.coalesce(grouped.c.net_amount, 0).label("net_amount"),
        func.coalesce(grouped.c.order_count, 0).label("order_count"),
        grouped.c.latest_paid_at,
        *(func.coalesce(grouped.c[category], 0).label(category) for category in CATEGORIES),
    ).outerjoin(grouped, grouped.c.person_id == people.c.id)
    conditions = []
    if filters.status != "all":
        conditions.append(people.c.status == filters.status)
    if filters.member_number:
        conditions.append(people.c.member_number == normalize_member_number(filters.member_number))
    keyword = filters.q.strip()
    by_name = filters.sort.startswith("name_")
    amount = func.coalesce(grouped.c.net_amount, 0)
    if not by_name:
        query = query.order_by(amount.desc() if filters.sort == "amount_desc" else amount.asc(), people.c.member_number, people.c.id)
    query = query.where(*conditions)
    if keyword or by_name:
        # Legal names and roster email addresses are encrypted. Match or sort these on the server after decrypting only the report fields.
        rows = [_summary_row(row, cipher) async for row in await session.stream(query)]
        if keyword:
            rows = [row for row in rows if _matches(row, keyword)]
        if by_name:
            rows.sort(key=lambda row: (row["member_number"], row["id"]))
            rows.sort(key=lambda row: normalize_name(row["name"]).casefold(), reverse=filters.sort == "name_desc")
        page = rows if export else rows[filters.offset:filters.offset + filters.limit]
        return {"items": [_public_profile(row) for row in page], "total": len(rows)}
    if export:
        rows = [_summary_row(row, cipher) async for row in await session.stream(query)]
        return {"items": [_public_profile(row) for row in rows], "total": len(rows)}
    total = await session.scalar(select(func.count()).select_from(people).where(*conditions))
    rows = [_summary_row(row, cipher) for row in await session.execute(query.limit(filters.limit).offset(filters.offset))]
    return {"items": [_public_profile(row) for row in rows], "total": total}


async def spend_details(session: AsyncSession, period: SpendPeriod, person_id: str, cipher: VersionedPIICipher, *, export=False):
    roster_memberships = _roster_memberships()
    people = _people(roster_memberships)
    person = (await session.execute(select(people).where(people.c.id == person_id))).one_or_none()
    if person is None:
        return None
    transactions = _transactions(period, roster_memberships, person_id)
    query = (
        select(transactions, Order.order_number, Order.payment_method, Invoice.invoice_number)
        .join(Order, Order.id == transactions.c.order_id)
        .outerjoin(Invoice, Invoice.order_id == transactions.c.order_id)
        .order_by(transactions.c.occurred_at.desc(), transactions.c.kind, transactions.c.transaction_id)
    )
    total = None
    if not export:
        total = await session.scalar(select(func.count()).select_from(transactions))
        query = query.limit(period.limit).offset(period.offset)
    items = [{
        "id": f"{row.kind}:{row.transaction_id}", "order_id": row.order_id, "order_number": row.order_number,
        "date": aware(row.occurred_at).astimezone(TAIPEI).date(), "category": row.category,
        "category_label": CATEGORIES[row.category], "amount": row.amount, "refunded": row.refunded,
        "payment_method": METHOD_LABELS[row.payment_method.value], "invoice_number": row.invoice_number,
    } for row in await session.execute(query)]
    return {"member": _public_profile(_profile(person, cipher)), "items": items, "total": len(items) if export else total}
