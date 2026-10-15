from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

import app.jobs as jobs_module
import app.routers.payments as payments_module
from app.integrations.common import IntegrationResponseError
from app.integrations.payment_service import create_provider_aware_refund
from app.integrations.raygate import RayGatePaymentResult
from app.models import (
    FulfillmentMethod,
    FulfillmentState,
    FulfillmentStatus,
    InventoryReservation,
    MembershipType,
    Notification,
    Order,
    OrderFulfillment,
    OrderItem,
    OrderKind,
    OutboxEvent,
    PaymentAttempt,
    PaymentStatus,
    PointAccount,
    PointSourceType,
    PointTransaction,
    Product,
    Refund,
    RefundStatus,
    ReservationStatus,
    SalesChannel,
    Shipment,
    ShipmentStatus,
    ShippingChannel,
    ShippingTemperature,
    TaxType,
    User,
    UserRole,
)
from tests.support import make_test_settings
from tests.test_raygate import (
    STORE_IDENTIFIER,
    FakeRayGateRefundAdapter,
    _make_pending_raygate_refund,
)


def _callback_payload(
    attempt: PaymentAttempt,
    *,
    status: int,
    return_code: str,
    message: str,
    associated_order_id: str = "",
) -> dict[str, str]:
    return RayGatePaymentResult(
        order_id=attempt.provider_trade_no or "RG2026090200000999",
        amount=attempt.amount,
        pay_type="linepay",
        return_code=return_code,
        message=message,
        transaction_time="2026-09-02 12:30:00",
        store_name="十里方圓",
        store_code=STORE_IDENTIFIER,
        status=status,
        associated_order_id=associated_order_id,
        pos_order_number=attempt.merchant_trade_no,
    ).to_canonical_payload()


@pytest.mark.asyncio
async def test_late_success_for_old_attempt_refunds_without_consuming_twice(
    database_session,
) -> None:
    now = datetime.now(timezone.utc)
    user = User(
        email="raygate-duplicate-paid@example.test",
        display_name="雷門重複付款買家",
        password_hash="test",
    )
    product = Product(
        slug="raygate-duplicate-payment-stock",
        name="重複付款庫存商品",
        description="測試",
        category="測試",
        unit="份",
        member_price=450,
        nonmember_price=450,
        stock_quantity=4,
        tax_type=TaxType.TAX_EXEMPT,
    )
    database_session.add_all([user, product])
    await database_session.flush()
    order = Order(
        order_number="ORD-RG-DUPLICATE-PAID",
        order_kind=OrderKind.REGULAR,
        sales_channel=SalesChannel.REGULAR,
        fulfillment_method=FulfillmentMethod.COOPERATIVE_PICKUP,
        user=user,
        membership_type_snapshot=MembershipType.NONMEMBER,
        amount_total=450,
        contact_email=user.email,
        payment_status=PaymentStatus.PAID,
        paid_at=now,
        fulfillment_status=FulfillmentStatus.PREPARING,
        items=[
            OrderItem(
                product_name=product.name,
                unit_label="份",
                quantity=1,
                unit_price=450,
                subtotal=450,
                tax_type=TaxType.TAX_EXEMPT,
                source_product_id=product.id,
            )
        ],
    )
    paid_attempt = PaymentAttempt(
        order=order,
        provider="raygate",
        merchant_trade_no="260902DUPLICATEPAID",
        provider_trade_no="RG2026090200000901",
        amount=450,
        status=PaymentStatus.PAID,
        provider_response={"PaymentType": "linepay"},
        expires_at=now + timedelta(minutes=15),
        paid_at=now,
    )
    old_attempt = PaymentAttempt(
        order=order,
        provider="raygate",
        merchant_trade_no="260902OLDPENDING0001",
        amount=450,
        status=PaymentStatus.PENDING,
        expires_at=now + timedelta(minutes=15),
    )
    paid_reservation = InventoryReservation(
        order=order,
        payment_attempt=paid_attempt,
        source_product_id=product.id,
        quantity=1,
        status=ReservationStatus.CONSUMED,
        expires_at=paid_attempt.expires_at,
    )
    old_reservation = InventoryReservation(
        order=order,
        payment_attempt=old_attempt,
        source_product_id=product.id,
        quantity=1,
        status=ReservationStatus.RELEASED,
        expires_at=old_attempt.expires_at,
        released_at=now,
    )
    database_session.add_all(
        [order, paid_attempt, old_attempt, paid_reservation, old_reservation]
    )
    await database_session.flush()
    point_account = PointAccount(user_id=user.id)
    database_session.add(point_account)
    await database_session.flush()
    database_session.add(
        PointTransaction(
            account_id=point_account.id,
            amount=4,
            source_type=PointSourceType.PURCHASE,
            reference_id=order.id,
            note="消費累積",
        )
    )
    await database_session.commit()

    payload = _callback_payload(
        old_attempt,
        status=2,
        return_code="0000",
        message="交易成功",
    )
    repository = payments_module.SQLAlchemyPaymentCallbackRepository(
        database_session
    )
    await repository.apply_raygate_payment_callback(
        "old-attempt-success-after-other-paid",
        payload,
    )

    await database_session.refresh(order)
    await database_session.refresh(paid_attempt)
    await database_session.refresh(old_attempt)
    await database_session.refresh(old_reservation)
    await database_session.refresh(product)
    refund = await database_session.scalar(
        select(Refund).where(Refund.payment_attempt_id == old_attempt.id)
    )
    assert order.payment_status == PaymentStatus.PAID
    assert paid_attempt.status == PaymentStatus.PAID
    assert old_attempt.status != PaymentStatus.PAID
    assert old_reservation.status == ReservationStatus.RELEASED
    assert product.stock_quantity == 4
    assert refund is not None
    assert refund.status == RefundStatus.PENDING

    refunded_outcome = await repository.apply_raygate_payment_callback(
        "old-attempt-duplicate-refund-completed",
        _callback_payload(
            old_attempt,
            status=3,
            return_code="0000",
            message="退款成功",
            associated_order_id="RF2026090200000901",
        ),
    )
    await database_session.refresh(order)
    point_reversal = await database_session.scalar(
        select(PointTransaction.id).where(
            PointTransaction.account_id == point_account.id,
            PointTransaction.source_type == PointSourceType.REFUND,
        )
    )
    assert refunded_outcome == "refunded"
    assert order.payment_status == PaymentStatus.PAID
    assert point_reversal is None


@pytest.mark.asyncio
async def test_success_refreshes_cancelled_order_and_released_reservations(
    database_session,
) -> None:
    now = datetime.now(timezone.utc)
    user = User(
        email="raygate-cancel-race@example.test",
        display_name="付款取消競態買家",
        password_hash="test",
    )
    product = Product(
        slug="raygate-cancel-race-stock",
        name="付款取消競態商品",
        description="測試",
        category="測試",
        unit="份",
        member_price=450,
        nonmember_price=450,
        stock_quantity=3,
        tax_type=TaxType.TAX_EXEMPT,
    )
    order = Order(
        order_number="ORD-RG-CANCEL-RACE",
        order_kind=OrderKind.REGULAR,
        sales_channel=SalesChannel.REGULAR,
        fulfillment_method=FulfillmentMethod.COOPERATIVE_PICKUP,
        user=user,
        membership_type_snapshot=MembershipType.NONMEMBER,
        amount_total=450,
        contact_email=user.email,
        payment_status=PaymentStatus.PENDING,
        fulfillment_status=FulfillmentStatus.PENDING_CONFIRMATION,
    )
    attempt = PaymentAttempt(
        order=order,
        provider="raygate",
        merchant_trade_no="260902CANCELRACE001",
        amount=450,
        status=PaymentStatus.PENDING,
        expires_at=now + timedelta(minutes=15),
    )
    reservation = InventoryReservation(
        order=order,
        payment_attempt=attempt,
        source_product_id=product.id,
        quantity=1,
        status=ReservationStatus.ACTIVE,
        expires_at=attempt.expires_at,
    )
    database_session.add_all([user, product, order, attempt, reservation])
    await database_session.commit()

    await database_session.execute(
        update(Order)
        .where(Order.id == order.id)
        .values(
            fulfillment_status=FulfillmentStatus.CANCELLED,
            payment_status=PaymentStatus.EXPIRED,
            cancelled_at=now,
            cancellation_reason="買家取消",
        )
        .execution_options(synchronize_session=False)
    )
    await database_session.execute(
        update(InventoryReservation)
        .where(InventoryReservation.id == reservation.id)
        .values(status=ReservationStatus.RELEASED, released_at=now)
        .execution_options(synchronize_session=False)
    )
    await database_session.execute(
        update(Product)
        .where(Product.id == product.id)
        .values(stock_quantity=4)
        .execution_options(synchronize_session=False)
    )

    repository = payments_module.SQLAlchemyPaymentCallbackRepository(
        database_session
    )
    await repository._apply_success(
        attempt,
        _callback_payload(
            attempt,
            status=2,
            return_code="0000",
            message="交易成功",
        ),
    )

    await database_session.refresh(order)
    await database_session.refresh(attempt)
    await database_session.refresh(reservation)
    await database_session.refresh(product)
    assert order.fulfillment_status == FulfillmentStatus.CANCELLED
    assert order.payment_status == PaymentStatus.LATE_PAID_REFUND_REQUIRED
    assert attempt.status == PaymentStatus.LATE_PAID_REFUND_REQUIRED
    assert reservation.status == ReservationStatus.RELEASED
    assert product.stock_quantity == 4


@pytest.mark.asyncio
async def test_refund_refreshes_already_released_reservations(
    database_session,
) -> None:
    order, attempt, refund, _event = await _make_pending_raygate_refund(
        database_session,
        suffix="REG08",
    )
    now = datetime.now(timezone.utc)
    product = Product(
        slug="raygate-refund-race-stock",
        name="退款競態商品",
        description="測試",
        category="測試",
        unit="份",
        member_price=450,
        nonmember_price=450,
        stock_quantity=3,
        tax_type=TaxType.TAX_EXEMPT,
    )
    reservation = InventoryReservation(
        order=order,
        payment_attempt=attempt,
        source_product_id=product.id,
        quantity=1,
        status=ReservationStatus.CONSUMED,
        expires_at=attempt.expires_at,
    )
    database_session.add_all([product, reservation])
    await database_session.commit()

    await database_session.execute(
        update(Order)
        .where(Order.id == order.id)
        .values(
            fulfillment_status=FulfillmentStatus.CANCELLED,
            cancelled_at=now,
            cancellation_reason="買家取消",
        )
        .execution_options(synchronize_session=False)
    )
    await database_session.execute(
        update(InventoryReservation)
        .where(InventoryReservation.id == reservation.id)
        .values(status=ReservationStatus.RELEASED, released_at=now)
        .execution_options(synchronize_session=False)
    )
    await database_session.execute(
        update(Product)
        .where(Product.id == product.id)
        .values(stock_quantity=4)
        .execution_options(synchronize_session=False)
    )

    repository = payments_module.SQLAlchemyPaymentCallbackRepository(
        database_session
    )
    await repository._apply_provider_refund(
        attempt,
        _callback_payload(
            attempt,
            status=3,
            return_code="0000",
            message="退款成功",
            associated_order_id="RF2026090200000908",
        ),
    )

    await database_session.refresh(order)
    await database_session.refresh(attempt)
    await database_session.refresh(refund)
    await database_session.refresh(reservation)
    await database_session.refresh(product)
    assert order.payment_status == PaymentStatus.REFUNDED
    assert attempt.status == PaymentStatus.REFUNDED
    assert refund.status == RefundStatus.COMPLETED
    assert reservation.status == ReservationStatus.RELEASED
    assert product.stock_quantity == 4


@pytest.mark.asyncio
async def test_refunded_callback_completes_the_same_previously_failed_refund(
    database_session,
) -> None:
    order, attempt, refund, _event = await _make_pending_raygate_refund(
        database_session,
        suffix="REG01",
    )
    repository = payments_module.SQLAlchemyPaymentCallbackRepository(
        database_session
    )

    failed_outcome = await repository.apply_raygate_payment_callback(
        "refund-failed-before-confirmation",
        _callback_payload(
            attempt,
            status=4,
            return_code="RG000004",
            message="退款失敗",
        ),
    )
    assert failed_outcome == "refund_failed"
    await database_session.refresh(refund)
    assert refund.status == RefundStatus.FAILED

    refunded_outcome = await repository.apply_raygate_payment_callback(
        "refund-confirmed-after-failure",
        _callback_payload(
            attempt,
            status=3,
            return_code="0000",
            message="退款成功",
            associated_order_id="RF2026090200000902",
        ),
    )

    saved_refunds = list(
        await database_session.scalars(
            select(Refund).where(Refund.order_id == order.id)
        )
    )
    await database_session.refresh(refund)
    assert refunded_outcome == "refunded"
    assert len(saved_refunds) == 1
    assert saved_refunds[0].id == refund.id
    assert refund.status == RefundStatus.COMPLETED
    assert refund.provider_refund_id == "RF2026090200000902"


@pytest.mark.asyncio
async def test_refund_failure_does_not_restore_cancelled_released_order_to_paid(
    database_session,
) -> None:
    order, attempt, refund, _event = await _make_pending_raygate_refund(
        database_session,
        suffix="REG02",
    )
    product = Product(
        slug="raygate-failed-refund-released-stock",
        name="退款失敗已釋放商品",
        description="測試",
        category="測試",
        unit="份",
        member_price=450,
        nonmember_price=450,
        stock_quantity=7,
        tax_type=TaxType.TAX_EXEMPT,
    )
    admin = User(
        email="raygate-explicit-failure-admin@example.test",
        display_name="退款失敗管理員",
        password_hash="test",
        user_role=UserRole.ADMIN,
    )
    released_at = datetime.now(timezone.utc)
    reservation = InventoryReservation(
        order=order,
        payment_attempt=attempt,
        source_product_id=product.id,
        quantity=1,
        status=ReservationStatus.RELEASED,
        expires_at=released_at,
        released_at=released_at,
    )
    order.fulfillment_status = FulfillmentStatus.CANCELLED
    order.cancelled_at = released_at
    order.cancellation_reason = "買家取消"
    database_session.add_all([product, reservation, admin])
    await database_session.commit()

    repository = payments_module.SQLAlchemyPaymentCallbackRepository(
        database_session
    )
    outcome = await repository.apply_raygate_payment_callback(
        "cancelled-order-refund-failed",
        _callback_payload(
            attempt,
            status=4,
            return_code="RG000004",
            message="退款失敗",
        ),
    )

    await database_session.refresh(order)
    await database_session.refresh(attempt)
    await database_session.refresh(refund)
    await database_session.refresh(reservation)
    await database_session.refresh(product)
    admin_notification = await database_session.scalar(
        select(Notification).where(
            Notification.user_id == admin.id,
            Notification.event_type == "refund_manual_review_required",
        )
    )
    assert outcome == "refund_failed"
    assert order.payment_status != PaymentStatus.PAID
    assert attempt.status != PaymentStatus.PAID
    assert order.fulfillment_status == FulfillmentStatus.CANCELLED
    assert reservation.status == ReservationStatus.RELEASED
    assert product.stock_quantity == 7
    assert refund.status == RefundStatus.FAILED
    assert admin_notification is not None


@pytest.mark.asyncio
async def test_external_refund_does_not_release_an_in_transit_order(
    database_session,
) -> None:
    order, attempt, refund, _event = await _make_pending_raygate_refund(
        database_session,
        suffix="REG03",
    )
    product = Product(
        slug="raygate-in-transit-refund-stock",
        name="配送中退款商品",
        description="測試",
        category="測試",
        unit="份",
        member_price=450,
        nonmember_price=450,
        stock_quantity=4,
        tax_type=TaxType.TAX_EXEMPT,
    )
    admin = User(
        email="raygate-shipment-refund-admin@example.test",
        display_name="物流退款管理員",
        password_hash="test",
        user_role=UserRole.ADMIN,
    )
    fulfillment = OrderFulfillment(
        order=order,
        method=FulfillmentMethod.ECPAY_LOGISTICS,
        status=FulfillmentState.AWAITING_SHIPMENT,
    )
    shipment = Shipment(
        fulfillment=fulfillment,
        channel=ShippingChannel.HOME_DELIVERY,
        temperature=ShippingTemperature.AMBIENT,
        status=ShipmentStatus.IN_TRANSIT,
        shipping_fee=160,
    )
    reservation = InventoryReservation(
        order=order,
        payment_attempt=attempt,
        source_product_id=product.id,
        quantity=1,
        status=ReservationStatus.CONSUMED,
        expires_at=datetime.now(timezone.utc),
    )
    order.fulfillment_method = FulfillmentMethod.ECPAY_LOGISTICS
    database_session.add_all(
        [product, admin, fulfillment, shipment, reservation]
    )
    await database_session.commit()
    callback_payload = _callback_payload(
        attempt,
        status=3,
        return_code="0000",
        message="退款成功",
        associated_order_id="RF2026090200000903",
    )
    order_id = order.id
    fulfillment_id = fulfillment.id
    reservation_id = reservation.id
    product_id = product.id
    refund_id = refund.id
    shipment_id = shipment.id
    admin_id = admin.id
    database_session.expunge_all()

    repository = payments_module.SQLAlchemyPaymentCallbackRepository(
        database_session
    )
    outcome = await repository.apply_raygate_payment_callback(
        "external-refund-while-in-transit",
        callback_payload,
    )

    order = await database_session.get(Order, order_id)
    fulfillment = await database_session.get(OrderFulfillment, fulfillment_id)
    reservation = await database_session.get(InventoryReservation, reservation_id)
    product = await database_session.get(Product, product_id)
    refund = await database_session.get(Refund, refund_id)
    shipment = await database_session.get(Shipment, shipment_id)
    intervention = await database_session.scalar(
        select(Notification).where(
            Notification.user_id == admin_id,
            Notification.event_type == "refund_fulfillment_intervention",
        )
    )
    intervention_email = await database_session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.aggregate_id == admin_id,
            OutboxEvent.event_type == "send_email",
        )
    )
    assert outcome == "refunded"
    assert order.payment_status == PaymentStatus.REFUNDED
    assert fulfillment.status == FulfillmentState.AWAITING_SHIPMENT
    assert shipment.status == ShipmentStatus.IN_TRANSIT
    assert reservation.status == ReservationStatus.CONSUMED
    assert product.stock_quantity == 4
    assert refund.status == RefundStatus.COMPLETED
    assert intervention is not None
    assert intervention.data["order_id"] == order.id
    assert intervention.data["refund_id"] == refund.id
    assert intervention_email is not None
    assert (
        intervention_email.payload["event_type"]
        == "refund_fulfillment_intervention"
    )


@pytest.mark.asyncio
async def test_duplicate_refund_releases_its_active_stock_after_shipment_created(
    database_session,
) -> None:
    order, refunded_attempt, refund, _event = await _make_pending_raygate_refund(
        database_session,
        suffix="REG10",
    )
    now = datetime.now(timezone.utc)
    product = Product(
        id="raygate-shipped-duplicate-product",
        slug="raygate-shipped-duplicate-stock",
        name="已出貨重複付款保留商品",
        description="測試",
        category="測試",
        unit="份",
        member_price=450,
        nonmember_price=450,
        stock_quantity=3,
        tax_type=TaxType.TAX_EXEMPT,
    )
    main_attempt = PaymentAttempt(
        order=order,
        provider="raygate",
        merchant_trade_no="260902MAINPAIDREG10",
        provider_trade_no="RG20260902MAINREG10",
        amount=450,
        status=PaymentStatus.PAID,
        provider_response={"PaymentType": "linepay"},
        expires_at=now + timedelta(minutes=15),
        paid_at=now,
    )
    refunded_reservation = InventoryReservation(
        order=order,
        payment_attempt=refunded_attempt,
        source_product_id=product.id,
        quantity=1,
        status=ReservationStatus.ACTIVE,
        expires_at=refunded_attempt.expires_at,
    )
    fulfillment = OrderFulfillment(
        order=order,
        method=FulfillmentMethod.ECPAY_LOGISTICS,
        status=FulfillmentState.AWAITING_SHIPMENT,
        shipment=Shipment(
            channel=ShippingChannel.HOME_DELIVERY,
            temperature=ShippingTemperature.AMBIENT,
            status=ShipmentStatus.CREATED,
            shipping_fee=160,
        ),
    )
    order.payment_status = PaymentStatus.PAID
    order.fulfillment_method = FulfillmentMethod.ECPAY_LOGISTICS
    database_session.add_all(
        [product, main_attempt, refunded_reservation, fulfillment]
    )
    await database_session.commit()
    shipment_id = fulfillment.shipment.id

    outcome = await payments_module.SQLAlchemyPaymentCallbackRepository(
        database_session
    ).apply_raygate_payment_callback(
        "shipped-duplicate-refund-completed",
        _callback_payload(
            refunded_attempt,
            status=3,
            return_code="0000",
            message="退款成功",
            associated_order_id="RF2026090200000910",
        ),
    )

    await database_session.refresh(order)
    await database_session.refresh(refunded_attempt)
    await database_session.refresh(refunded_reservation)
    await database_session.refresh(product)
    saved_shipment = await database_session.get(Shipment, shipment_id)
    assert outcome == "refunded"
    assert order.payment_status == PaymentStatus.PAID
    assert refunded_attempt.status == PaymentStatus.REFUNDED
    assert refunded_reservation.status == ReservationStatus.RELEASED
    assert product.stock_quantity == 4
    assert saved_shipment.status == ShipmentStatus.CREATED


@pytest.mark.asyncio
async def test_irreversible_refund_never_restores_consumed_stock(
    database_session,
) -> None:
    order, refunded_attempt, _refund, _event = await _make_pending_raygate_refund(
        database_session,
        suffix="REG11",
    )
    now = datetime.now(timezone.utc)
    product = Product(
        id="raygate-in-transit-consumed-product",
        slug="raygate-in-transit-consumed-stock",
        name="配送中已消耗商品",
        description="測試",
        category="測試",
        unit="份",
        member_price=450,
        nonmember_price=450,
        stock_quantity=4,
        tax_type=TaxType.TAX_EXEMPT,
    )
    consumed = InventoryReservation(
        order=order,
        payment_attempt=refunded_attempt,
        source_product_id=product.id,
        quantity=1,
        status=ReservationStatus.CONSUMED,
        expires_at=refunded_attempt.expires_at,
    )
    other_refund_pending = PaymentAttempt(
        order=order,
        provider="raygate",
        merchant_trade_no="260902OTHERREFUNDREG11",
        provider_trade_no="RG20260902OTHERREG11",
        amount=450,
        status=PaymentStatus.REFUND_PENDING,
        expires_at=now + timedelta(minutes=15),
        paid_at=now,
    )
    fulfillment = OrderFulfillment(
        order=order,
        method=FulfillmentMethod.ECPAY_LOGISTICS,
        status=FulfillmentState.SHIPPED,
        shipment=Shipment(
            channel=ShippingChannel.HOME_DELIVERY,
            temperature=ShippingTemperature.AMBIENT,
            status=ShipmentStatus.IN_TRANSIT,
            shipping_fee=160,
        ),
    )
    order.fulfillment_method = FulfillmentMethod.ECPAY_LOGISTICS
    database_session.add_all(
        [product, consumed, other_refund_pending, fulfillment]
    )
    await database_session.commit()

    await payments_module.SQLAlchemyPaymentCallbackRepository(
        database_session
    ).apply_raygate_payment_callback(
        "in-transit-consumed-refund-completed",
        _callback_payload(
            refunded_attempt,
            status=3,
            return_code="0000",
            message="退款成功",
            associated_order_id="RF2026090200000911",
        ),
    )

    await database_session.refresh(order)
    await database_session.refresh(consumed)
    await database_session.refresh(product)
    assert order.payment_status == PaymentStatus.REFUND_PENDING
    assert consumed.status == ReservationStatus.CONSUMED
    assert product.stock_quantity == 4


@pytest.mark.asyncio
async def test_unbound_order_refund_skips_attempt_with_existing_refund(
    database_session,
    monkeypatch,
) -> None:
    order, duplicate_attempt, duplicate_refund, _event = (
        await _make_pending_raygate_refund(
            database_session,
            suffix="REG04",
        )
    )
    now = datetime.now(timezone.utc)
    main_attempt = PaymentAttempt(
        order=order,
        provider="raygate",
        merchant_trade_no="260902MAINREFUNDREG04",
        provider_trade_no="RG20260902MAINREG04",
        amount=450,
        status=PaymentStatus.PAID,
        provider_response={"PaymentType": "linepay"},
        expires_at=now + timedelta(minutes=15),
        paid_at=now - timedelta(minutes=5),
    )
    requested_refund = Refund(
        order=order,
        amount=450,
        status=RefundStatus.PENDING,
        reason="主付款退款",
        requested_by_id=order.user_id,
    )
    database_session.add_all([main_attempt, requested_refund])
    await database_session.flush()
    requested_event = OutboxEvent(
        event_type="refund.requested",
        aggregate_type="refund",
        aggregate_id=requested_refund.id,
        payload={"refund_id": requested_refund.id, "order_id": order.id},
    )
    database_session.add(requested_event)
    order.payment_status = PaymentStatus.REFUND_PENDING
    await database_session.commit()

    adapter = FakeRayGateRefundAdapter(
        {
            "MerchantTradeNo": main_attempt.merchant_trade_no,
            "TradeNo": main_attempt.provider_trade_no,
            "TradeAmt": "450",
            "PaymentType": "linepay",
            "PaymentDisposition": "paid",
            "RayGateStatus": "2",
            "RayGateAssociatedOrderID": "",
        }
    )
    monkeypatch.setattr(
        jobs_module,
        "refund_adapter_from_settings",
        lambda _settings, provider: adapter if provider == "raygate" else None,
    )

    await jobs_module._process_refund_event(
        session=database_session,
        settings=make_test_settings(payment_provider="raygate"),
        event=requested_event,
    )

    await database_session.refresh(requested_refund)
    await database_session.refresh(duplicate_refund)
    assert requested_refund.payment_attempt_id == main_attempt.id
    assert requested_refund.status == RefundStatus.COMPLETED
    assert duplicate_refund.payment_attempt_id == duplicate_attempt.id


@pytest.mark.asyncio
async def test_refund_creation_skips_attempt_with_existing_refund(
    database_session,
) -> None:
    order, duplicate_attempt, duplicate_refund, _event = (
        await _make_pending_raygate_refund(
            database_session,
            suffix="REG06",
        )
    )
    now = datetime.now(timezone.utc)
    main_attempt = PaymentAttempt(
        order=order,
        provider="raygate",
        merchant_trade_no="260902MAINREFUNDREG06",
        provider_trade_no="RG20260902MAINREG06",
        amount=450,
        status=PaymentStatus.PAID,
        provider_response={"PaymentType": "linepay"},
        expires_at=now + timedelta(minutes=15),
        paid_at=now - timedelta(minutes=5),
    )
    database_session.add(main_attempt)
    await database_session.flush()

    requested_refund, queued = await create_provider_aware_refund(
        database_session,
        order=order,
        amount=order.amount_total,
        reason="取消主付款",
        requested_by_id=order.user_id,
    )

    assert queued is True
    assert requested_refund.payment_attempt_id == main_attempt.id
    assert requested_refund.status == RefundStatus.PENDING
    assert duplicate_refund.payment_attempt_id == duplicate_attempt.id


class _AmbiguousRefundAdapter:
    def __init__(self, attempt: PaymentAttempt) -> None:
        self.merchant_trade_no = attempt.merchant_trade_no
        self.provider_trade_no = attempt.provider_trade_no or ""
        self.amount = attempt.amount
        self.calls: list[tuple[str, str]] = []
        self.refund_calls = 0
        self.query_as_refunded = False

    async def query_order(self, merchant_trade_no: str) -> dict[str, str]:
        self.calls.append(("query", merchant_trade_no))
        if self.query_as_refunded:
            return {
                "MerchantTradeNo": self.merchant_trade_no,
                "TradeNo": self.provider_trade_no,
                "TradeAmt": str(self.amount),
                "PaymentType": "linepay",
                "PaymentDisposition": "refunded",
                "RayGateStatus": "3",
                "RayGateAssociatedOrderID": "RF20260902RECOVERED",
            }
        return {
            "MerchantTradeNo": self.merchant_trade_no,
            "TradeNo": self.provider_trade_no,
            "TradeAmt": str(self.amount),
            "PaymentType": "linepay",
            "PaymentDisposition": "paid",
            "RayGateStatus": "2",
            "RayGateAssociatedOrderID": "",
        }

    async def refund(self, **_kwargs) -> None:
        self.refund_calls += 1
        self.calls.append(("refund", self.provider_trade_no))
        if self.refund_calls == 1:
            raise IntegrationResponseError("退款請求送出後連線中斷")
        raise AssertionError("回應不明的退款不得再次送出")


@pytest.mark.asyncio
async def test_ambiguous_remote_refund_retry_only_queries_provider(
    database_session,
    monkeypatch,
) -> None:
    _order, attempt, refund, event = await _make_pending_raygate_refund(
        database_session,
        suffix="REG03",
    )
    adapter = _AmbiguousRefundAdapter(attempt)
    event_id = event.id
    refund_id = refund.id
    monkeypatch.setattr(
        jobs_module,
        "refund_adapter_from_settings",
        lambda _settings, provider: adapter if provider == "raygate" else None,
    )
    settings = make_test_settings(payment_provider="raygate")

    with pytest.raises(IntegrationResponseError, match="連線中斷"):
        await jobs_module._process_refund_event(
            session=database_session,
            settings=settings,
            event=event,
        )
    await database_session.rollback()

    persisted_event = await database_session.get(OutboxEvent, event_id)
    assert persisted_event is not None
    try:
        await jobs_module._process_refund_event(
            session=database_session,
            settings=settings,
            event=persisted_event,
        )
    except (IntegrationResponseError, ValueError):
        pass

    persisted_refund = await database_session.get(Refund, refund_id)
    assert persisted_refund is not None
    assert adapter.calls == [
        ("query", adapter.merchant_trade_no),
        ("refund", adapter.provider_trade_no),
        ("query", adapter.merchant_trade_no),
    ]
    assert adapter.refund_calls == 1
    assert persisted_refund.status == RefundStatus.PENDING


@pytest.mark.asyncio
async def test_ambiguous_refund_converges_by_query_without_resending(
    database_session,
    monkeypatch,
) -> None:
    order, attempt, refund, event = await _make_pending_raygate_refund(
        database_session,
        suffix="REG07",
    )
    adapter = _AmbiguousRefundAdapter(attempt)
    event_id = event.id
    refund_id = refund.id
    monkeypatch.setattr(
        jobs_module,
        "refund_adapter_from_settings",
        lambda _settings, provider: adapter if provider == "raygate" else None,
    )
    settings = make_test_settings(payment_provider="raygate")

    with pytest.raises(IntegrationResponseError, match="連線中斷"):
        await jobs_module._process_refund_event(
            session=database_session,
            settings=settings,
            event=event,
        )
    await database_session.rollback()
    adapter.query_as_refunded = True
    persisted_event = await database_session.get(OutboxEvent, event_id)
    await jobs_module._process_refund_event(
        session=database_session,
        settings=settings,
        event=persisted_event,
    )

    persisted_refund = await database_session.get(Refund, refund_id)
    await database_session.refresh(order)
    assert adapter.refund_calls == 1
    assert persisted_refund.status == RefundStatus.COMPLETED
    assert persisted_refund.provider_refund_id == "RF20260902RECOVERED"
    assert order.payment_status == PaymentStatus.REFUNDED


@pytest.mark.asyncio
async def test_ambiguous_refund_exhaustion_alerts_admin_without_resending(
    database_session,
    monkeypatch,
) -> None:
    _order, attempt, refund, event = await _make_pending_raygate_refund(
        database_session,
        suffix="REG05",
    )
    admin = User(
        email="raygate-refund-review-admin@example.test",
        display_name="退款處理管理員",
        password_hash="test",
        user_role=UserRole.ADMIN,
    )
    event.attempts = 7
    event.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    event.payload = {
        **dict(event.payload),
        "provider_refund_query_only": True,
        "provider_refund_submission_started_at": datetime.now(
            timezone.utc
        ).isoformat(),
    }
    database_session.add(admin)
    await database_session.commit()
    adapter = _AmbiguousRefundAdapter(attempt)
    monkeypatch.setattr(
        jobs_module,
        "refund_adapter_from_settings",
        lambda _settings, provider: adapter if provider == "raygate" else None,
    )

    completed, failed = await jobs_module._process_outbox(
        session=database_session,
        settings=make_test_settings(payment_provider="raygate"),
        now=datetime.now(timezone.utc),
        limit=10,
    )

    await database_session.refresh(event)
    await database_session.refresh(refund)
    notification = await database_session.scalar(
        select(Notification).where(
            Notification.user_id == admin.id,
            Notification.event_type == "refund_manual_review_required",
        )
    )
    assert (completed, failed) == (0, 1)
    assert event.status.value == "failed"
    assert refund.status == RefundStatus.PENDING
    assert adapter.refund_calls == 0
    assert notification is not None
    assert notification.data["refund_id"] == refund.id


@pytest.mark.asyncio
async def test_refund_failure_before_submission_exhaustion_alerts_admin(
    database_session,
    monkeypatch,
) -> None:
    _order, _attempt, refund, event = await _make_pending_raygate_refund(
        database_session,
        suffix="REG09",
    )
    admin = User(
        email="raygate-preflight-failure-admin@example.test",
        display_name="退款前置失敗管理員",
        password_hash="test",
        user_role=UserRole.ADMIN,
    )
    event.attempts = 7
    event.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    database_session.add(admin)
    await database_session.commit()

    def unavailable_adapter(_settings, _provider):
        raise IntegrationResponseError("退款設定無法使用")

    monkeypatch.setattr(
        jobs_module,
        "refund_adapter_from_settings",
        unavailable_adapter,
    )

    completed, failed = await jobs_module._process_outbox(
        session=database_session,
        settings=make_test_settings(payment_provider="raygate"),
        now=datetime.now(timezone.utc),
        limit=10,
    )

    await database_session.refresh(event)
    await database_session.refresh(refund)
    notification = await database_session.scalar(
        select(Notification).where(
            Notification.user_id == admin.id,
            Notification.event_type == "refund_manual_review_required",
        )
    )
    assert (completed, failed) == (0, 1)
    assert event.status.value == "failed"
    assert not event.payload.get("provider_refund_query_only")
    assert refund.status == RefundStatus.PENDING
    assert notification is not None
    assert "處理失敗" in notification.title
