from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

import app.integrations.payment_service as payment_module
import app.jobs as jobs_module
from app.auth import make_token_pair
from app.config import Settings
from app.database import Base, get_session
from app.domain import order_available_actions
from app.integrations.ecpay import CheckoutForm
from app.integrations.invoice import InvoiceIssueResult
from app.integrations.invoice_service import issue_picked_up_order_invoice
from app.integrations.payment_service import (
    SQLAlchemyPaymentCallbackRepository,
    create_membership_payment_attempt,
    create_payment_attempt,
    release_attempt_reservations,
)
from app.jobs import (
    _reconcile_activities,
    _reconcile_expired_payments,
    _reconcile_meal_events,
    _reconcile_member_proposals,
    schedule_background_reconcile,
    should_reconcile_now,
)
from app.models import (
    Activity,
    ActivityRegistration,
    ActivityRegistrationStatus,
    ActivityStatus,
    FulfillmentMethod,
    FulfillmentState,
    FulfillmentStatus,
    InventoryReservation,
    Invoice,
    InvoiceStatus,
    Meal,
    MealEvent,
    MealEventOffering,
    MealEventStatus,
    MemberProposal,
    MemberProposalStatus,
    MemberProposalVote,
    MemberVoteChoice,
    Membership,
    MembershipApplication,
    MembershipApplicationStatus,
    MembershipCharge,
    MembershipChargeKind,
    MembershipChargeStatus,
    MembershipFeeSchedule,
    MembershipStatus,
    MembershipType,
    Notification,
    Order,
    OrderFulfillment,
    OrderItem,
    OrderKind,
    OutboxEvent,
    PaymentAttempt,
    PaymentStatus,
    Refund,
    ReservationStatus,
    SalesChannel,
    TaxType,
    User,
)
from app.routers.membership import membership_router


class FakePaymentAdapter:
    def __init__(self) -> None:
        self.query_result = {
            "MerchantID": "3002607",
            "TradeStatus": "0",
            "RtnCode": "0",
            "RtnMsg": "尚未付款",
        }
        self.query_count = 0

    def create_checkout_form(self, **kwargs) -> CheckoutForm:
        return CheckoutForm(
            action_url="https://payment-stage.example.test/checkout",
            fields={
                "MerchantTradeNo": kwargs["merchant_trade_no"],
                "TradeAmt": str(kwargs["amount"]),
            },
        )

    async def query_order(self, merchant_trade_no: str) -> dict[str, str]:
        self.query_count += 1
        return {
            **self.query_result,
            "MerchantTradeNo": merchant_trade_no,
        }


@pytest.mark.asyncio
async def test_background_reconcile_is_single_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    class SessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    async def fake_lazy_reconcile(session, settings):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()

    monkeypatch.setattr(jobs_module, "SessionLocal", SessionContext)
    monkeypatch.setattr(jobs_module, "lazy_reconcile", fake_lazy_reconcile)

    first = schedule_background_reconcile(payment_settings())
    await started.wait()
    second = schedule_background_reconcile(payment_settings())
    await asyncio.sleep(0)

    assert second is first
    assert calls == 1
    assert should_reconcile_now() is False

    release.set()
    await first


def payment_settings() -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        app_base_url="https://api.example.test",
        web_base_url="https://app.example.test",
        ecpay_payment_merchant_id="3002607",
        ecpay_payment_hash_key="pwFHCqoQZGmho4w6",
        ecpay_payment_hash_iv="EkRm7iFT261dpevs",
        payment_reservation_minutes=15,
    )


def auth_headers(user: User) -> dict[str, str]:
    token = make_token_pair(user)["access_token"]
    return {"Authorization": f"Bearer {token}"}


def successful_callback(
    attempt: PaymentAttempt,
    payment_time: datetime,
    trade_no: str,
) -> dict[str, str]:
    return {
        "MerchantID": "3002607",
        "MerchantTradeNo": attempt.merchant_trade_no,
        "TradeAmt": str(attempt.amount),
        "RtnCode": "1",
        "RtnMsg": "交易成功",
        "SimulatePaid": "0",
        "PaymentDate": payment_time.astimezone(
            timezone(timedelta(hours=8))
        ).strftime("%Y/%m/%d %H:%M:%S"),
        "TradeDate": payment_time.astimezone(
            timezone(timedelta(hours=8))
        ).strftime("%Y/%m/%d %H:%M:%S"),
        "TradeNo": trade_no,
    }


@pytest.fixture
async def database_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def fake_payment(monkeypatch) -> FakePaymentAdapter:
    adapter = FakePaymentAdapter()
    monkeypatch.setattr(
        payment_module,
        "payment_adapter_from_settings",
        lambda _settings: adapter,
    )
    monkeypatch.setattr(
        jobs_module,
        "payment_adapter_from_settings",
        lambda _settings: adapter,
    )
    return adapter


async def make_pending_membership(database_session):
    user = User(
        email="membership-buyer@example.com",
        display_name="待付款申請人",
        password_hash="test",
        email_verified_at=datetime.now(timezone.utc),
    )
    application = MembershipApplication(
        user=user,
        status=MembershipApplicationStatus.APPROVED,
    )
    membership = Membership(
        user=user,
        application=application,
        status=MembershipStatus.PENDING_PAYMENT,
    )
    admission_schedule = MembershipFeeSchedule(
        charge_kind=MembershipChargeKind.ADMISSION_FEE,
        amount=500,
        effective_from=date(2026, 1, 1),
    )
    capital_schedule = MembershipFeeSchedule(
        charge_kind=MembershipChargeKind.SHARE_CAPITAL,
        amount=1000,
        effective_from=date(2026, 1, 1),
    )
    database_session.add_all(
        [
            user,
            application,
            membership,
            admission_schedule,
            capital_schedule,
        ]
    )
    await database_session.flush()
    admission_charge = MembershipCharge(
        user_id=user.id,
        application_id=application.id,
        membership_id=membership.id,
        fee_schedule_id=admission_schedule.id,
        charge_kind=MembershipChargeKind.ADMISSION_FEE,
        amount=500,
    )
    capital_charge = MembershipCharge(
        user_id=user.id,
        application_id=application.id,
        membership_id=membership.id,
        fee_schedule_id=capital_schedule.id,
        charge_kind=MembershipChargeKind.SHARE_CAPITAL,
        amount=1000,
    )
    database_session.add_all([admission_charge, capital_charge])
    await database_session.commit()
    return user, membership, admission_charge, capital_charge


async def make_meal_order(
    database_session,
    *,
    now: datetime,
    ordering_ends_at: datetime,
    pickup_ends_at: datetime | None = None,
    order_number: str = "MEAL-PAY-0001",
):
    user = User(
        email=f"{order_number.lower()}@example.com",
        display_name="便當預購人",
        password_hash="test",
        email_verified_at=now,
    )
    meal = Meal(
        slug=f"meal-{order_number.lower()}",
        name="時蔬豆腐便當",
        description="Sandbox 便當",
        price=120,
        tax_type=TaxType.TAXABLE,
    )
    event = MealEvent(
        title="校園午餐預購",
        location="校門口攤位",
        ordering_starts_at=now - timedelta(hours=1),
        ordering_ends_at=ordering_ends_at,
        pickup_starts_at=ordering_ends_at + timedelta(hours=1),
        pickup_ends_at=pickup_ends_at
        or ordering_ends_at + timedelta(hours=2),
        status=MealEventStatus.PUBLISHED,
        created_by=user,
    )
    offering = MealEventOffering(
        event=event,
        meal=meal,
        price=125,
        capacity=1,
    )
    database_session.add_all([user, meal, event, offering])
    await database_session.flush()
    order = Order(
        order_number=order_number,
        order_kind=OrderKind.REGULAR,
        sales_channel=SalesChannel.MEAL_PREORDER,
        fulfillment_method=FulfillmentMethod.EVENT_PICKUP,
        user=user,
        meal_event=event,
        membership_type_snapshot=MembershipType.NONMEMBER,
        amount_total=125,
        contact_email=user.email,
        payment_status=PaymentStatus.PENDING,
        invoice_status=InvoiceStatus.NOT_ELIGIBLE,
        fulfillment_status=FulfillmentStatus.PENDING_CONFIRMATION,
        items=[
            OrderItem(
                source_meal_offering_id=offering.id,
                product_name=meal.name,
                unit_label="份",
                quantity=1,
                unit_price=125,
                subtotal=125,
                tax_type=meal.tax_type,
            )
        ],
        fulfillment=OrderFulfillment(
            method=FulfillmentMethod.EVENT_PICKUP,
            status=FulfillmentState.PENDING_CONFIRMATION,
            pickup_location=event.location,
            pickup_starts_at=event.pickup_starts_at,
            pickup_ends_at=event.pickup_ends_at,
            pickup_code="123456",
            pickup_qr_token_hash=order_number.lower().replace("-", ""),
        ),
    )
    database_session.add(order)
    await database_session.commit()
    return user, event, offering, order


@pytest.mark.asyncio
async def test_two_membership_charge_payments_activate_and_create_receipts(
    database_session,
    fake_payment,
) -> None:
    user, membership, admission, capital = await make_pending_membership(
        database_session
    )
    settings = payment_settings()
    now = datetime(2026, 7, 31, 2, 0, tzinfo=timezone.utc)
    repository = SQLAlchemyPaymentCallbackRepository(database_session)

    first_attempt = await create_membership_payment_attempt(
        database_session,
        admission.id,
        user,
        settings,
        now=now,
    )
    first_result = await repository.apply_ecpay_payment_callback(
        "membership-payment-1",
        successful_callback(
            first_attempt,
            now + timedelta(minutes=1),
            "MEMBER-TRADE-1",
        ),
    )

    assert first_result == "paid"
    await database_session.refresh(membership)
    await database_session.refresh(admission)
    assert admission.status == MembershipChargeStatus.PAID
    assert admission.receipt_number
    assert membership.status == MembershipStatus.PENDING_PAYMENT
    assert membership.member_number is None

    second_attempt = await create_membership_payment_attempt(
        database_session,
        capital.id,
        user,
        settings,
        now=now + timedelta(minutes=2),
    )
    second_result = await repository.apply_ecpay_payment_callback(
        "membership-payment-2",
        successful_callback(
            second_attempt,
            now + timedelta(minutes=3),
            "MEMBER-TRADE-2",
        ),
    )

    assert second_result == "paid"
    await database_session.refresh(membership)
    await database_session.refresh(capital)
    assert capital.status == MembershipChargeStatus.PAID
    assert capital.receipt_number
    assert admission.receipt_number != capital.receipt_number
    assert membership.status == MembershipStatus.ACTIVE
    assert membership.member_number
    assert membership.member_number.startswith("SLF-")
    assert await database_session.scalar(
        select(func.count(Invoice.id))
    ) == 0

    application = FastAPI()
    application.include_router(membership_router)

    async def override_get_session():
        yield database_session

    application.dependency_overrides[get_session] = override_get_session
    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        for charge in (admission, capital):
            response = await client.get(
                f"/v1/membership/charges/{charge.id}/receipt",
                headers=auth_headers(user),
            )
            assert response.status_code == 200
            assert response.json() == {
                "receipt_number": charge.receipt_number,
                "charge_kind": charge.charge_kind.value,
                "amount": charge.amount,
                "paid_at": charge.paid_at.isoformat().replace("+00:00", "Z"),
                "invoice": None,
            }


@pytest.mark.asyncio
async def test_withdrawn_membership_application_cannot_start_payment(
    database_session,
    fake_payment,
) -> None:
    user, membership, admission, _capital = await make_pending_membership(
        database_session
    )
    application = await database_session.get(
        MembershipApplication,
        membership.application_id,
    )
    assert application is not None
    application.status = MembershipApplicationStatus.WITHDRAWN
    await database_session.commit()

    with pytest.raises(payment_module.PaymentApplicationError):
        await create_membership_payment_attempt(
            database_session,
            admission.id,
            user,
            payment_settings(),
            now=datetime(2026, 7, 31, 2, 0, tzinfo=timezone.utc),
        )


@pytest.mark.asyncio
async def test_duplicate_membership_charge_payment_records_sandbox_refund(
    database_session,
    fake_payment,
) -> None:
    user, _membership, admission, _capital = await make_pending_membership(
        database_session
    )
    settings = payment_settings()
    now = datetime(2026, 7, 31, 2, 0, tzinfo=timezone.utc)
    first_attempt = await create_membership_payment_attempt(
        database_session,
        admission.id,
        user,
        settings,
        now=now,
    )
    first_attempt.status = PaymentStatus.EXPIRED
    await database_session.commit()
    second_attempt = await create_membership_payment_attempt(
        database_session,
        admission.id,
        user,
        settings,
        now=now + timedelta(minutes=16),
    )
    repository = SQLAlchemyPaymentCallbackRepository(database_session)
    assert await repository.apply_ecpay_payment_callback(
        "membership-current-payment",
        successful_callback(
            second_attempt,
            now + timedelta(minutes=17),
            "MEMBER-CURRENT-TRADE",
        ),
    ) == "paid"

    late_result = await repository.apply_ecpay_payment_callback(
        "membership-old-attempt-paid-late",
        successful_callback(
            first_attempt,
            now + timedelta(minutes=5),
            "MEMBER-LATE-TRADE",
        ),
    )
    refund = await database_session.scalar(
        select(Refund).where(
            Refund.membership_charge_id == admission.id
        )
    )

    assert late_result == "late_paid_refund_required"
    assert refund is not None
    assert refund.amount == admission.amount


@pytest.mark.asyncio
async def test_late_final_charge_preserves_active_membership_and_refunds_once(
    database_session,
    fake_payment,
) -> None:
    user, membership, admission, capital = await make_pending_membership(
        database_session
    )
    settings = payment_settings()
    now = datetime(2026, 7, 31, 2, 0, tzinfo=timezone.utc)
    admission.status = MembershipChargeStatus.PAID
    admission.paid_at = now
    admission.receipt_number = "SLFR-20260731-ADMISSION"
    await database_session.commit()

    old_attempt = await create_membership_payment_attempt(
        database_session,
        capital.id,
        user,
        settings,
        now=now,
    )
    old_attempt.status = PaymentStatus.EXPIRED
    await database_session.commit()
    current_attempt = await create_membership_payment_attempt(
        database_session,
        capital.id,
        user,
        settings,
        now=now + timedelta(minutes=16),
    )
    repository = SQLAlchemyPaymentCallbackRepository(database_session)
    current_result = await repository.apply_ecpay_payment_callback(
        "membership-final-current",
        successful_callback(
            current_attempt,
            now + timedelta(minutes=17),
            "MEMBER-FINAL-CURRENT",
        ),
    )
    await database_session.refresh(membership)
    await database_session.refresh(capital)
    original_receipt = capital.receipt_number

    assert current_result == "paid"
    assert membership.status == MembershipStatus.ACTIVE
    assert capital.status == MembershipChargeStatus.PAID
    assert original_receipt

    late_payload = successful_callback(
        old_attempt,
        now + timedelta(minutes=5),
        "MEMBER-FINAL-LATE",
    )
    late_result = await repository.apply_ecpay_payment_callback(
        "membership-final-old-late",
        late_payload,
    )
    await database_session.refresh(membership)
    await database_session.refresh(capital)

    assert late_result == "late_paid_refund_required"
    assert membership.status == MembershipStatus.ACTIVE
    assert capital.status == MembershipChargeStatus.PAID
    assert capital.receipt_number == original_receipt
    assert await database_session.scalar(
        select(func.count(Refund.id)).where(
            Refund.membership_charge_id == capital.id
        )
    ) == 1

    duplicate_result = await repository.apply_ecpay_payment_callback(
        "membership-final-old-late-second-event",
        late_payload,
    )
    assert duplicate_result == "duplicate"
    assert await database_session.scalar(
        select(func.count(Refund.id)).where(
            Refund.membership_charge_id == capital.id
        )
    ) == 1


@pytest.mark.asyncio
async def test_membership_payment_after_termination_is_refunded(
    database_session,
    fake_payment,
) -> None:
    user, membership, _admission, capital = await make_pending_membership(
        database_session
    )
    now = datetime(2026, 7, 31, 2, 0, tzinfo=timezone.utc)
    attempt = await create_membership_payment_attempt(
        database_session,
        capital.id,
        user,
        payment_settings(),
        now=now,
    )
    membership.status = MembershipStatus.TERMINATED
    membership.ended_at = now + timedelta(minutes=1)
    membership.status_reason = "管理員終止待付款會籍"
    await database_session.commit()

    result = await SQLAlchemyPaymentCallbackRepository(
        database_session
    ).apply_ecpay_payment_callback(
        "membership-terminated-late-payment",
        successful_callback(
            attempt,
            now + timedelta(minutes=2),
            "MEMBER-TERMINATED-LATE",
        ),
    )
    await database_session.refresh(membership)
    await database_session.refresh(capital)
    await database_session.refresh(attempt)

    assert result == "late_paid_refund_required"
    assert membership.status == MembershipStatus.TERMINATED
    assert capital.status == MembershipChargeStatus.REFUNDED
    assert attempt.status == PaymentStatus.LATE_PAID_REFUND_REQUIRED
    assert await database_session.scalar(
        select(func.count(Refund.id)).where(
            Refund.membership_charge_id == capital.id
        )
    ) == 1


@pytest.mark.asyncio
async def test_meal_payment_reserves_expires_releases_and_success_consumes(
    database_session,
    fake_payment,
) -> None:
    now = datetime(2026, 7, 31, 2, 0, tzinfo=timezone.utc)
    user, _event, offering, order = await make_meal_order(
        database_session,
        now=now,
        ordering_ends_at=now + timedelta(hours=2),
    )
    settings = payment_settings()

    first_attempt = await create_payment_attempt(
        database_session,
        order.id,
        user,
        settings,
        now=now,
    )
    first_reservation = await database_session.scalar(
        select(InventoryReservation).where(
            InventoryReservation.payment_attempt_id == first_attempt.id
        )
    )

    await database_session.refresh(offering)
    assert offering.reserved_quantity == 1
    assert offering.paid_quantity == 0
    assert first_reservation is not None
    assert first_reservation.status == ReservationStatus.ACTIVE

    processed = await _reconcile_expired_payments(
        database_session,
        settings,
        now + timedelta(minutes=16),
        100,
    )
    await database_session.refresh(first_attempt)
    await database_session.refresh(first_reservation)
    await database_session.refresh(offering)
    assert processed == 1
    assert fake_payment.query_count == 1
    assert first_attempt.status == PaymentStatus.EXPIRED
    assert first_reservation.status == ReservationStatus.RELEASED
    assert offering.reserved_quantity == 0
    assert offering.paid_quantity == 0

    second_attempt = await create_payment_attempt(
        database_session,
        order.id,
        user,
        settings,
        now=now + timedelta(minutes=17),
    )
    second_reservation = await database_session.scalar(
        select(InventoryReservation).where(
            InventoryReservation.payment_attempt_id == second_attempt.id
        )
    )
    await database_session.refresh(offering)
    assert offering.reserved_quantity == 1
    assert second_reservation is not None
    assert second_reservation.status == ReservationStatus.ACTIVE
    second_attempt_id = second_attempt.id
    second_reservation_id = second_reservation.id

    result = await SQLAlchemyPaymentCallbackRepository(
        database_session
    ).apply_ecpay_payment_callback(
        "meal-payment-success",
        successful_callback(
            second_attempt,
            now + timedelta(minutes=18),
            "MEAL-TRADE-1",
        ),
    )
    await database_session.refresh(second_attempt)
    await database_session.refresh(second_reservation)
    await database_session.refresh(offering)
    await database_session.refresh(order)

    assert result == "paid"
    assert await database_session.scalar(
        select(PaymentAttempt.status).where(
            PaymentAttempt.id == second_attempt_id
        )
    ) == PaymentStatus.PAID
    assert await database_session.scalar(
        select(InventoryReservation.status).where(
            InventoryReservation.id == second_reservation_id
        )
    ) == ReservationStatus.CONSUMED
    assert offering.reserved_quantity == 0
    assert offering.paid_quantity == 1
    assert order.payment_status == PaymentStatus.PAID


@pytest.mark.asyncio
async def test_meal_valid_hold_reacquires_after_ordering_deadline(
    database_session,
    fake_payment,
) -> None:
    now = datetime(2026, 7, 31, 2, 0, tzinfo=timezone.utc)
    user, _event, offering, order = await make_meal_order(
        database_session,
        now=now,
        ordering_ends_at=now + timedelta(minutes=5),
        order_number="MEAL-LATE-0001",
    )
    settings = payment_settings()
    attempt = await create_payment_attempt(
        database_session,
        order.id,
        user,
        settings,
        now=now,
    )
    loaded_attempt = await database_session.scalar(
        select(PaymentAttempt)
        .where(PaymentAttempt.id == attempt.id)
        .options(selectinload(PaymentAttempt.reservations))
    )
    assert loaded_attempt is not None
    await release_attempt_reservations(database_session, loaded_attempt)
    loaded_attempt.status = PaymentStatus.EXPIRED
    await database_session.commit()

    result = await SQLAlchemyPaymentCallbackRepository(
        database_session
    ).apply_ecpay_payment_callback(
        "meal-payment-valid-hold",
        successful_callback(
            loaded_attempt,
            now + timedelta(minutes=10),
            "MEAL-TRADE-VALID-HOLD",
        ),
    )
    reservation = loaded_attempt.reservations[0]
    await database_session.refresh(offering)
    await database_session.refresh(order)
    await database_session.refresh(reservation)

    assert result == "paid"
    assert order.payment_status == PaymentStatus.PAID
    assert reservation.status == ReservationStatus.CONSUMED
    assert offering.reserved_quantity == 0
    assert offering.paid_quantity == 1


@pytest.mark.asyncio
async def test_meal_payment_arriving_after_event_cancel_is_refunded(
    database_session,
    fake_payment,
) -> None:
    now = datetime(2026, 7, 31, 2, 0, tzinfo=timezone.utc)
    user, event, offering, order = await make_meal_order(
        database_session,
        now=now,
        ordering_ends_at=now + timedelta(hours=2),
        order_number="MEAL-CANCEL-RACE-0001",
    )
    attempt = await create_payment_attempt(
        database_session,
        order.id,
        user,
        payment_settings(),
        now=now,
    )
    attempt_id = attempt.id
    reservation = await database_session.scalar(
        select(InventoryReservation).where(
            InventoryReservation.payment_attempt_id == attempt_id
        )
    )
    assert reservation is not None
    event.status = MealEventStatus.CANCELLED
    await database_session.commit()

    result = await SQLAlchemyPaymentCallbackRepository(
        database_session
    ).apply_ecpay_payment_callback(
        "meal-payment-after-event-cancel",
        successful_callback(
            attempt,
            now + timedelta(minutes=5),
            "MEAL-CANCELLED-TRADE",
        ),
    )
    await database_session.refresh(order)
    await database_session.refresh(offering)
    await database_session.refresh(reservation)
    refund = await database_session.scalar(
        select(Refund).where(Refund.order_id == order.id)
    )
    refund_event = None
    if refund is not None:
        refund_event = await database_session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "refund.requested",
                OutboxEvent.aggregate_id == refund.id,
            )
        )
    attempt_status = await database_session.scalar(
        select(PaymentAttempt.status).where(PaymentAttempt.id == attempt_id)
    )

    assert result == "late_paid_refund_required"
    assert attempt_status == PaymentStatus.LATE_PAID_REFUND_REQUIRED
    assert order.payment_status == PaymentStatus.LATE_PAID_REFUND_REQUIRED
    assert reservation.status == ReservationStatus.RELEASED
    assert offering.reserved_quantity == 0
    assert offering.paid_quantity == 0
    assert refund is not None
    assert refund.status.value == "pending"
    assert refund_event is not None


@pytest.mark.asyncio
async def test_meal_no_show_reconcile_issues_invoice(
    database_session,
) -> None:
    now = datetime(2026, 7, 31, 8, 0, tzinfo=timezone.utc)
    user, event, offering, order = await make_meal_order(
        database_session,
        now=now - timedelta(hours=4),
        ordering_ends_at=now - timedelta(hours=3),
        pickup_ends_at=now - timedelta(minutes=1),
        order_number="MEAL-NOSHOW-0001",
    )
    event.status = MealEventStatus.PICKUP_OPEN
    order.payment_status = PaymentStatus.PAID
    order.paid_at = now - timedelta(hours=2)
    order.fulfillment.status = FulfillmentState.READY_FOR_PICKUP
    order.fulfillment_status = FulfillmentStatus.READY_FOR_PICKUP
    offering.paid_quantity = 1
    await database_session.commit()

    changed = await _reconcile_meal_events(
        database_session,
        now,
        100,
    )
    await database_session.refresh(event)
    await database_session.refresh(order)
    fulfillment = await database_session.scalar(
        select(OrderFulfillment).where(
            OrderFulfillment.order_id == order.id
        )
    )
    assert fulfillment is not None
    assert changed == 1
    assert event.status == MealEventStatus.COMPLETED
    assert fulfillment.status == FulfillmentState.NO_SHOW
    assert fulfillment.fulfilled_at.replace(tzinfo=timezone.utc) == now
    assert order.fulfillment_status == FulfillmentStatus.PICKED_UP
    assert "refund" not in order_available_actions(order, viewer_is_admin=True)
    assert order.invoice_status == InvoiceStatus.PENDING
    assert await database_session.scalar(
        select(func.count(Refund.id)).where(Refund.order_id == order.id)
    ) == 0
    invoice_event = await database_session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.event_type == "invoice.issue_requested",
            OutboxEvent.aggregate_id == order.id,
        )
    )
    assert invoice_event is not None

    class FakeInvoiceAdapter:
        async def query_invoice(self, relate_number):
            return {"RtnCode": 0, "RtnMsg": "not found"}

        async def issue_invoice(self, request):
            assert request.items[0].name == "時蔬豆腐便當"
            return InvoiceIssueResult(
                relate_number=request.relate_number,
                invoice_number="AB87654321",
                invoice_date="2026-07-31 16:05:00",
                random_number="4321",
                raw={"RtnCode": 1, "InvoiceNo": "AB87654321"},
            )

    result = await issue_picked_up_order_invoice(
        database_session,
        order.id,
        FakeInvoiceAdapter(),
    )
    invoice = await database_session.scalar(
        select(Invoice).where(Invoice.order_id == order.id)
    )
    assert result.invoice_number == "AB87654321"
    assert invoice is not None
    assert invoice.status == InvoiceStatus.ISSUED
    assert order.invoice_status == InvoiceStatus.ISSUED
    assert user.id == order.user_id


@pytest.mark.asyncio
async def test_member_proposal_and_activity_reconcile(
    database_session,
) -> None:
    now = datetime(2026, 7, 31, 8, 0, tzinfo=timezone.utc)
    proposer = User(
        email="proposal-owner@example.com",
        display_name="提案社員",
        password_hash="test",
    )
    voter = User(
        email="proposal-voter@example.com",
        display_name="投票社員",
        password_hash="test",
    )
    discussion = MemberProposal(
        created_by=proposer,
        title="討論期結束提案",
        body="測試討論轉表決",
        status=MemberProposalStatus.DISCUSSION,
        minimum_voters=2,
        discussion_ends_at=now - timedelta(minutes=1),
        voting_ends_at=now + timedelta(days=1),
    )
    voting = MemberProposal(
        created_by=proposer,
        title="表決期結束提案",
        body="測試表決結果",
        status=MemberProposalStatus.VOTING,
        minimum_voters=2,
        discussion_ends_at=now - timedelta(days=1),
        voting_ends_at=now - timedelta(minutes=1),
        votes=[
            MemberProposalVote(user=proposer, choice=MemberVoteChoice.YES),
            MemberProposalVote(user=voter, choice=MemberVoteChoice.YES),
        ],
    )
    activity = Activity(
        created_by=proposer,
        title="已結束社員健行",
        description="測試活動結束",
        location="近郊步道",
        starts_at=now - timedelta(hours=3),
        ends_at=now - timedelta(hours=1),
        registration_deadline=now - timedelta(days=1),
        capacity=2,
        status=ActivityStatus.PUBLISHED,
        registrations=[
            ActivityRegistration(
                user=proposer,
                status=ActivityRegistrationStatus.REGISTERED,
                queue_position=1,
            ),
            ActivityRegistration(
                user=voter,
                status=ActivityRegistrationStatus.ATTENDED,
                queue_position=2,
                checked_in_at=now - timedelta(hours=2),
            ),
        ],
    )
    database_session.add_all(
        [proposer, voter, discussion, voting, activity]
    )
    await database_session.commit()

    proposal_changes = await _reconcile_member_proposals(
        database_session,
        now,
        100,
    )
    activity_changes = await _reconcile_activities(
        database_session,
        now,
        100,
    )
    await database_session.refresh(discussion)
    await database_session.refresh(voting)
    await database_session.refresh(activity)

    assert proposal_changes == 2
    assert discussion.status == MemberProposalStatus.VOTING
    assert voting.status == MemberProposalStatus.PASSED
    assert voting.closed_at.replace(tzinfo=timezone.utc) == now
    result_notification = await database_session.scalar(
        select(Notification).where(
            Notification.event_type == "member_proposal.result",
            Notification.user_id == proposer.id,
        )
    )
    assert result_notification is not None
    assert activity_changes == 1
    assert activity.status == ActivityStatus.COMPLETED
    registrations = {
        item.user_id: item.status
        for item in (
            await database_session.scalars(
                select(ActivityRegistration).where(
                    ActivityRegistration.activity_id == activity.id
                )
            )
        ).all()
    }
    assert registrations == {
        proposer.id: ActivityRegistrationStatus.NO_SHOW,
        voter.id: ActivityRegistrationStatus.ATTENDED,
    }
