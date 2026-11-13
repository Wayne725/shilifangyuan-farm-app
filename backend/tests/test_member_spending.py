import csv
from datetime import datetime, timezone
from io import StringIO

from sqlalchemy import select

from app.integrations.pii_crypto import pii_cipher_from_settings
from app.member_claims import roster_aad
from app.models import (
    ActivityCharge, ActivityRegistration, ActivityRegistrationStatus, Activity, ActivityStatus,
    AdminAudit, FulfillmentMethod, Invoice, InvoiceBuyerType, InvoiceStatus, MemberRosterEntry, Membership,
    MembershipStatus, MembershipType, Order, OrderKind, PaymentAttempt, PaymentMethod, PaymentStatus, Refund,
    RefundStatus, SalesChannel, User, UserRole, new_id,
)
from app.routers.member_reports import member_reports_router
from tests.support import api_test_context, auth_headers, make_test_settings


def at(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def csv_rows(response):
    assert response.status_code == 200, response.text
    return list(csv.reader(StringIO(response.text.lstrip("\ufeff"))))


async def test_member_spending_matches_payment_and_refund_dates_and_verified_counter_identity(database_session):
    session = database_session
    settings = make_test_settings()
    admin = User(email="spend-admin@example.test", password_hash="test", display_name="管理員", user_role=UserRole.ADMIN)
    buyer = User(email="spend-buyer@example.test", password_hash="test", display_name="=不執行試算表公式")
    former = User(email="former@example.test", password_hash="test", display_name="舊社員")
    session.add_all([admin, buyer, former])
    await session.flush()
    membership = Membership(user_id=buyer.id, member_number="A001", status=MembershipStatus.ACTIVE)
    former_membership = Membership(user_id=former.id, member_number="B001", status=MembershipStatus.SUSPENDED)
    cipher = pii_cipher_from_settings(settings)

    def roster(number, name, email, claimed=None):
        entry_id = new_id()
        aad = roster_aad(entry_id)
        return MemberRosterEntry(
            id=entry_id, member_number=number, claimed_user_id=claimed,
            legal_name_encrypted=cipher.encrypt_text(name, associated_data=aad),
            email_encrypted=cipher.encrypt_text(email, associated_data=aad),
            phone_encrypted=cipher.encrypt_text("0912345678", associated_data=aad),
        )

    linked = roster("A001", "舊名冊姓名", "old@example.test", buyer.id)
    unlinked = roster("A002", "名冊社員", "ROSTER@example.test")
    session.add_all([membership, former_membership, linked, unlinked])
    await session.flush()

    paid_at = at("2026-10-01T00:00:00+08:00")
    before = at("2026-09-30T23:59:59+08:00")
    after = at("2026-11-01T00:00:00+08:00")

    def order(number, amount, *, channel=SalesChannel.REGULAR, paid=paid_at, owner=buyer,
              status=PaymentStatus.PAID, snapshot=MembershipType.MEMBER, business=False, verified=None, entry=None):
        onsite = verified is not None or entry is not None
        row = Order(
            id=new_id(), order_number=number, order_kind=OrderKind.GROUP if channel == SalesChannel.GROUP else OrderKind.REGULAR,
            user_id=None if onsite else owner.id, customer_kind="walk_in" if onsite else "account",
            verified_counter_membership_id=verified.id if verified else None,
            verified_counter_roster_entry_id=entry.id if entry else None,
            sales_channel=channel, fulfillment_method=FulfillmentMethod.COOPERATIVE_PICKUP,
            membership_type_snapshot=snapshot, amount_total=amount, contact_email="spend-buyer@example.test",
            payment_status=status, paid_at=paid,
            payment_method=PaymentMethod.CASH_ON_PICKUP if business or onsite else PaymentMethod.ONLINE,
            meal_booking_type="business" if business else "standard",
            invoice_buyer_type=InvoiceBuyerType.COMPANY if business or onsite else InvoiceBuyerType.PERSONAL,
            invoice_buyer_name="合作購買" if business else None, invoice_buyer_tax_id="24536806" if business else None,
            invoice_handling="pickup_day" if business else None, invoice_issue_after=paid_at if business else None,
        )
        session.add(row)
        return row

    regular = order("SPEND-REGULAR", 240)
    order("SPEND-GROUP", 90, channel=SalesChannel.GROUP)
    order("SPEND-MEAL", 120, channel=SalesChannel.MEAL_PREORDER)
    catering = order("SPEND-BUSINESS", 410, channel=SalesChannel.MEAL_PREORDER, business=True)
    order("SPEND-COUNTER", 80, verified=membership)
    order("SPEND-BEFORE-CLAIM", 40, entry=linked)
    order("SPEND-ROSTER", 55, entry=unlinked)
    order("SPEND-FORMER", 95, owner=former)
    order("SPEND-EXPIRED", 880, paid=None, status=PaymentStatus.EXPIRED)
    order("SPEND-NONMEMBER", 999, snapshot=MembershipType.NONMEMBER)
    previous = order("SPEND-SEPTEMBER", 100, paid=before)
    await session.flush()

    session.add_all([
        PaymentAttempt(order_id=regular.id, provider="sandbox", merchant_trade_no="SPEND-REAL-PAID", amount=240,
                       status=PaymentStatus.PAID, paid_at=paid_at, expires_at=paid_at),
        PaymentAttempt(order_id=regular.id, provider="sandbox", merchant_trade_no="SPEND-FAILED-ATTEMPT", amount=999,
                       status=PaymentStatus.FAILED, expires_at=paid_at),
        Refund(order_id=regular.id, amount=50, status=RefundStatus.COMPLETED, completed_at=after,
               requested_by_id=admin.id, reason="部分退款"),
        Refund(order_id=previous.id, amount=20, status=RefundStatus.COMPLETED, completed_at=paid_at,
               requested_by_id=admin.id, reason="前期購買"),
        Refund(order_id=regular.id, amount=55, status=RefundStatus.PENDING,
               requested_by_id=admin.id, reason="未完成"),
        Invoice(order_id=catering.id, relate_number="SPEND-INVOICE", invoice_number="AB12345678", status=InvoiceStatus.ISSUED),
    ])
    activity = Activity(
        title="不納入的活動", location="活動中心", created_by_id=admin.id, status=ActivityStatus.PUBLISHED,
        registration_deadline=before, starts_at=paid_at, ends_at=after, capacity=10,
    )
    session.add(activity)
    await session.flush()
    registration = ActivityRegistration(
        activity_id=activity.id, user_id=buyer.id, status=ActivityRegistrationStatus.REGISTERED, queue_position=1,
    )
    session.add(registration)
    await session.flush()
    charge = ActivityCharge(
        registration_id=registration.id, activity_id=activity.id, user_id=buyer.id, registration_revision=1,
        number="SPEND-ACTIVITY", activity_title=activity.title, contact_name=buyer.display_name,
        membership_type=MembershipType.MEMBER, amount=700, payment_status=PaymentStatus.PAID,
        paid_at=paid_at, expires_at=paid_at,
    )
    session.add(charge)
    await session.flush()
    session.add(PaymentAttempt(activity_charge_id=charge.id, provider="sandbox", merchant_trade_no="SPEND-ACTIVITY-PAID",
                               amount=700, status=PaymentStatus.PAID, expires_at=paid_at, paid_at=paid_at))
    await session.commit()

    base = "/v1/admin/member-spending"
    october = {"starts_on": "2026-10-01", "ends_on": "2026-10-31"}
    november = {"starts_on": "2026-11-01", "ends_on": "2026-11-30"}
    async with api_test_context(session, [member_reports_router], settings=settings) as client:
        headers = auth_headers(admin)
        assert (await client.get(base, headers=auth_headers(buyer))).status_code == 403
        assert (await client.get(base, headers=headers, params={"starts_on": "2026-11-01"})).status_code == 422
        assert (await client.get(base, headers=headers, params={"starts_on": "2026-11-01", "ends_on": "2026-10-01"})).status_code == 422
        reply = await client.get(base, headers=headers, params={**october, "limit": 1})
        assert reply.status_code == 200, reply.text
        data = reply.json()
        assert data["total"] == 2
        first = data["items"][0]
        assert first["id"] == membership.id
        assert first["categories"] == {"regular": 220, "group": 90, "meal": 120, "business": 410, "walk_in": 120}
        assert (first["net_amount"], first["order_count"], first["latest_paid_date"]) == (960, 6, "2026-10-01")
        second = await client.get(base, headers=headers, params={**october, "limit": 1, "offset": 1})
        assert second.json()["items"][0]["net_amount"] == 55
        for query in ("a001", "不執行試算表", "SPEND-BUYER@example.test"):
            result = await client.get(base, headers=headers, params={**october, "q": query})
            assert [row["id"] for row in result.json()["items"]] == [membership.id]
        result = await client.get(base, headers=headers, params={**october, "q": "roster@example.test"})
        assert result.json()["items"][0]["id"] == unlinked.id
        result = await client.get(base, headers=headers, params={**october, "status": "all", "sort": "amount_asc"})
        assert [row["net_amount"] for row in result.json()["items"]] == [55, 95, 960]
        result = await client.get(base, headers=headers, params={**october, "status": "all", "sort": "name_asc"})
        assert [row["id"] for row in result.json()["items"]] == [membership.id, unlinked.id, former_membership.id]
        result = await client.get(base, headers=headers, params={**november, "q": "A001"})
        assert (result.json()["items"][0]["net_amount"], result.json()["items"][0]["order_count"]) == (-50, 0)

        result = await client.get(f"{base}/{membership.id}", headers=headers, params={**october, "limit": 1})
        assert result.json()["total"] == 7
        result = await client.get(f"{base}/{membership.id}", headers=headers, params=october)
        details = {item["order_number"]: item for item in result.json()["items"]}
        assert details["SPEND-BUSINESS"]["payment_method"] == "現金"
        assert details["SPEND-BUSINESS"]["invoice_number"] == "AB12345678"
        assert details["SPEND-SEPTEMBER"]["date"] == "2026-10-01"
        assert (details["SPEND-SEPTEMBER"]["amount"], details["SPEND-SEPTEMBER"]["refunded"]) == (0, 20)
        result = await client.get(f"{base}/{membership.id}", headers=headers, params=november)
        assert [(row["order_number"], row["amount"], row["refunded"]) for row in result.json()["items"]] == [("SPEND-REGULAR", 0, 50)]

        summary_csv = csv_rows(await client.get(f"{base}.csv", headers=headers, params={**october, "limit": 1}))
        assert len(summary_csv) == 3 and summary_csv[1][1].startswith("'=")
        detail_csv = csv_rows(await client.get(f"{base}/{membership.id}/details.csv", headers=headers, params=november))
        assert detail_csv[1] == ["2026-11-01", "SPEND-REGULAR", "一般商品", "0", "線上", "", "50"]
    audits = (await session.scalars(select(AdminAudit).where(AdminAudit.action == "member_spending.exported"))).all()
    assert {(audit.actor_id, audit.data["kind"], audit.data["count"]) for audit in audits} == {
        (admin.id, "summary", 2), (admin.id, "details", 1),
    }
