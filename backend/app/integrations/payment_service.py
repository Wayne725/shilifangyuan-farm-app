from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Mapping, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..config import Settings
from ..domain import apply_paid_quantity
from ..models import (
    ExternalEvent,
    FulfillmentMethod,
    FulfillmentStatus,
    GroupCampaign,
    GroupDecisionStatus,
    GroupIntakeStatus,
    InventoryReservation,
    InvoiceCarrierType,
    MealEvent,
    MealEventOffering,
    MealEventStatus,
    Membership,
    MembershipApplication,
    MembershipCharge,
    MembershipChargeStatus,
    MembershipApplicationStatus,
    MembershipStatus,
    MembershipType,
    Order,
    OrderFulfillment,
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
    ShipmentStatus,
    User,
    UserRole,
)
from .ecpay import (
    ECPayAIOAdapter,
    ECPayAIOSettings,
    callback_event_key,
    create_merchant_trade_no,
)
from .notifications import (
    NotificationCommand,
    NotificationService,
    SQLAlchemyNotificationRepository,
)
from .invoice import is_mobile_barcode_format


class PaymentApplicationError(ValueError):
    pass


SUCCESS_CALLBACK_TERMINAL_STATUSES = {
    PaymentStatus.PAID,
    PaymentStatus.LATE_PAID_REFUND_REQUIRED,
    PaymentStatus.REFUND_PENDING,
    PaymentStatus.REFUNDED,
}


def payment_adapter_from_settings(settings: Settings) -> ECPayAIOAdapter:
    return ECPayAIOAdapter(
        ECPayAIOSettings(
            merchant_id=settings.ecpay_payment_merchant_id,
            hash_key=settings.ecpay_payment_hash_key,
            hash_iv=settings.ecpay_payment_hash_iv,
            checkout_url=settings.ecpay_payment_aio_url,
            query_url=settings.ecpay_payment_query_url,
            timeout_seconds=getattr(settings, "integration_timeout_seconds", 15.0),
        )
    )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _group_quantity(order: Order) -> int:
    return sum(item.quantity for item in order.items)


def _checkout_url(settings: Settings, attempt_id: str) -> str:
    return "{}/payments/{}/checkout".format(
        settings.app_base_url.rstrip("/"), attempt_id
    )


def payment_attempt_read(
    attempt: PaymentAttempt, settings: Settings
) -> Dict[str, Any]:
    return {
        "id": attempt.id,
        "attempt_id": attempt.id,
        "payment_url": _checkout_url(settings, attempt.id),
        "status": attempt.status.value,
        "expires_at": attempt.expires_at,
    }


async def create_payment_attempt(
    session: AsyncSession,
    order_id: str,
    user: User,
    settings: Settings,
    now: Optional[datetime] = None,
) -> PaymentAttempt:
    current = now or datetime.now(timezone.utc)
    order = await session.scalar(
        select(Order)
        .where(Order.id == order_id, Order.user_id == user.id)
        .options(
            selectinload(Order.items),
            selectinload(Order.payment_attempts),
            selectinload(Order.group_campaign),
            selectinload(Order.meal_event),
            selectinload(Order.fulfillment).selectinload(
                OrderFulfillment.shipment
            ),
        )
        .with_for_update()
    )
    if order is None:
        raise PaymentApplicationError("找不到訂單")
    if order.payment_status == PaymentStatus.PAID:
        raise PaymentApplicationError("此訂單已付款")
    if order.payment_status in {
        PaymentStatus.REFUND_PENDING,
        PaymentStatus.REFUNDED,
        PaymentStatus.LATE_PAID_REFUND_REQUIRED,
    }:
        raise PaymentApplicationError("此訂單目前無法付款")
    if order.amount_total <= 0:
        raise PaymentApplicationError("訂單金額必須大於零")
    if (
        order.invoice_carrier_type == InvoiceCarrierType.MOBILE_BARCODE
        and not is_mobile_barcode_format(order.invoice_carrier_value or "")
    ):
        raise PaymentApplicationError("手機條碼格式不正確")
    if order.fulfillment_method == FulfillmentMethod.ECPAY_LOGISTICS:
        # Paying before the store/address is locked in leaves an order that no
        # admin can ever turn into a shipment.
        shipment = (
            order.fulfillment.shipment if order.fulfillment is not None else None
        )
        if shipment is None or shipment.status not in {
            ShipmentStatus.READY_TO_CREATE,
            ShipmentStatus.CREATED,
        }:
            raise PaymentApplicationError("請先完成物流門市或地址選擇")

    for existing in sorted(
        order.payment_attempts, key=lambda item: item.created_at, reverse=True
    ):
        if (
            existing.status == PaymentStatus.PENDING
            and _aware(existing.expires_at) > current
            and existing.checkout_payload
        ):
            return existing
        if existing.status == PaymentStatus.PENDING:
            raise PaymentApplicationError(
                "前次付款結果仍在確認中，請稍後重新整理"
            )

    attempt_number = len(order.payment_attempts) + 1
    merchant_trade_no = create_merchant_trade_no(
        order.order_number, attempt_number, current
    )
    expires_at = current + timedelta(
        minutes=settings.payment_reservation_minutes
    )
    attempt = PaymentAttempt(
        order=order,
        merchant_trade_no=merchant_trade_no,
        amount=order.amount_total,
        status=PaymentStatus.PENDING,
        expires_at=expires_at,
    )
    session.add(attempt)
    await session.flush()

    if order.sales_channel == SalesChannel.MEAL_PREORDER:
        if order.meal_event is None:
            raise PaymentApplicationError("便當訂單缺少場次資料")
        if (
            order.meal_event.status != MealEventStatus.PUBLISHED
            or current >= _aware(order.meal_event.ordering_ends_at)
        ):
            raise PaymentApplicationError("便當預購已截止")
        for item in order.items:
            if item.source_meal_offering_id is None:
                raise PaymentApplicationError("便當訂單品項資料不完整")
            offering = await session.scalar(
                select(MealEventOffering)
                .where(
                    MealEventOffering.id
                    == item.source_meal_offering_id
                )
                .with_for_update()
            )
            if (
                offering is None
                or not offering.is_active
                or offering.capacity
                - offering.reserved_quantity
                - offering.paid_quantity
                < item.quantity
            ):
                raise PaymentApplicationError(
                    "{} 剩餘數量不足".format(item.product_name)
                )
            offering.reserved_quantity += item.quantity
            session.add(
                InventoryReservation(
                    order=order,
                    source_meal_offering_id=offering.id,
                    payment_attempt=attempt,
                    quantity=item.quantity,
                    status=ReservationStatus.ACTIVE,
                    expires_at=expires_at,
                )
            )
    elif order.order_kind == OrderKind.GROUP:
        if order.group_campaign_id is None:
            raise PaymentApplicationError("團購訂單缺少團購資料")
        campaign = await session.scalar(
            select(GroupCampaign)
            .where(GroupCampaign.id == order.group_campaign_id)
            .with_for_update()
        )
        if campaign is None:
            raise PaymentApplicationError("找不到團購")
        if campaign.intake_status != GroupIntakeStatus.OPEN:
            raise PaymentApplicationError("此團目前未開放付款")
        if campaign.decision_status not in {
            GroupDecisionStatus.RECRUITING,
            GroupDecisionStatus.CONFIRMED,
        }:
            raise PaymentApplicationError("此團目前無法付款")
        if current >= _aware(campaign.deadline):
            raise PaymentApplicationError("團購已截止")
        quantity = _group_quantity(order)
        if campaign.paid_quantity + campaign.reserved_quantity + quantity > (
            campaign.supply_cap
        ):
            raise PaymentApplicationError("團購剩餘名額不足")
        campaign.reserved_quantity += quantity
        reservation = InventoryReservation(
            order=order,
            group_campaign_id=campaign.id,
            payment_attempt=attempt,
            quantity=quantity,
            status=ReservationStatus.ACTIVE,
            expires_at=expires_at,
        )
        session.add(reservation)
    else:
        for item in order.items:
            if item.source_product_id is None:
                raise PaymentApplicationError("一般訂單品項缺少商品資料")
            product = await session.scalar(
                select(Product)
                .where(Product.id == item.source_product_id)
                .with_for_update()
            )
            if product is None or product.stock_quantity < item.quantity:
                raise PaymentApplicationError(
                    "{} 庫存不足".format(item.product_name)
                )
            product.stock_quantity -= item.quantity
            session.add(
                InventoryReservation(
                    order=order,
                    source_product_id=product.id,
                    payment_attempt=attempt,
                    quantity=item.quantity,
                    status=ReservationStatus.ACTIVE,
                    expires_at=expires_at,
                )
            )

    adapter = payment_adapter_from_settings(settings)
    form = adapter.create_checkout_form(
        merchant_trade_no=merchant_trade_no,
        amount=order.amount_total,
        item_name="#".join(
            "{} x{}".format(item.product_name, item.quantity)
            for item in order.items
        ),
        return_url="{}/webhooks/ecpay/payment".format(
            settings.app_base_url.rstrip("/")
        ),
        order_result_url="{}/payments/result".format(
            settings.app_base_url.rstrip("/")
        ),
        client_back_url="{}/orders?order_id={}".format(
            settings.web_base_url.rstrip("/"), order.id
        ),
        custom_fields={"CustomField1": order.id},
        now=current,
    )
    attempt.checkout_payload = dict(form.fields)
    await session.commit()
    await session.refresh(attempt)
    return attempt


async def create_membership_payment_attempt(
    session: AsyncSession,
    charge_id: str,
    user: User,
    settings: Settings,
    now: Optional[datetime] = None,
) -> PaymentAttempt:
    current = now or datetime.now(timezone.utc)
    charge = await session.scalar(
        select(MembershipCharge)
        .where(
            MembershipCharge.id == charge_id,
            MembershipCharge.user_id == user.id,
        )
        .options(selectinload(MembershipCharge.payment_attempts))
        .options(
            selectinload(MembershipCharge.application),
            selectinload(MembershipCharge.membership),
        )
        .with_for_update()
    )
    if charge is None:
        raise PaymentApplicationError("找不到入社應繳款")
    if (
        charge.application.status
        not in {
            MembershipApplicationStatus.SUBMITTED,
            MembershipApplicationStatus.NEEDS_SUPPLEMENT,
            MembershipApplicationStatus.APPROVED,
        }
        or charge.membership.status
        != MembershipStatus.PENDING_PAYMENT
    ):
        raise PaymentApplicationError("此入社申請目前無法付款")
    if charge.status == MembershipChargeStatus.PAID:
        raise PaymentApplicationError("此應繳款已付款")
    if charge.status not in {
        MembershipChargeStatus.PENDING,
    }:
        raise PaymentApplicationError("此應繳款目前無法付款")
    for existing in sorted(
        charge.payment_attempts,
        key=lambda item: item.created_at,
        reverse=True,
    ):
        if (
            existing.status == PaymentStatus.PENDING
            and _aware(existing.expires_at) > current
            and existing.checkout_payload
        ):
            return existing
        if existing.status == PaymentStatus.PENDING:
            raise PaymentApplicationError(
                "前次付款結果仍在確認中，請稍後重新整理"
            )
    merchant_trade_no = create_merchant_trade_no(
        f"membership-{charge.id}",
        len(charge.payment_attempts) + 1,
        current,
    )
    attempt = PaymentAttempt(
        membership_charge=charge,
        merchant_trade_no=merchant_trade_no,
        amount=charge.amount,
        status=PaymentStatus.PENDING,
        expires_at=current
        + timedelta(minutes=settings.payment_reservation_minutes),
    )
    session.add(attempt)
    await session.flush()
    item_name = (
        "十里方圓入社費"
        if charge.charge_kind.value == "admission_fee"
        else "十里方圓股金"
    )
    adapter = payment_adapter_from_settings(settings)
    form = adapter.create_checkout_form(
        merchant_trade_no=merchant_trade_no,
        amount=charge.amount,
        item_name=item_name,
        return_url="{}/webhooks/ecpay/payment".format(
            settings.app_base_url.rstrip("/")
        ),
        order_result_url="{}/payments/result".format(
            settings.app_base_url.rstrip("/")
        ),
        client_back_url="{}/account".format(
            settings.web_base_url.rstrip("/")
        ),
        custom_fields={
            "CustomField1": charge.id,
            "CustomField2": "membership_charge",
        },
        now=current,
    )
    attempt.checkout_payload = dict(form.fields)
    await session.commit()
    await session.refresh(attempt)
    return attempt


class SQLAlchemyPaymentCallbackRepository:
    """Applies a verified ECPay callback once inside the request transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def apply_ecpay_payment_callback(
        self, event_key: str, payload: Mapping[str, str]
    ) -> str:
        existing_event = await self.session.scalar(
            select(ExternalEvent).where(
                ExternalEvent.provider == "ecpay_aio",
                ExternalEvent.external_event_key == event_key,
            )
        )
        if existing_event is not None:
            return "duplicate"

        tombstone = await self.session.scalar(
            select(ExternalEvent).where(
                ExternalEvent.provider == "ecpay_reset",
                ExternalEvent.external_event_key
                == payload["MerchantTradeNo"],
            )
        )
        if tombstone is not None:
            self.session.add(
                ExternalEvent(
                    provider="ecpay_aio",
                    external_event_key=event_key,
                    event_type="payment_callback_tombstoned",
                    payload=dict(payload),
                    processed=True,
                    processed_at=datetime.now(timezone.utc),
                )
            )
            try:
                await self.session.commit()
            except IntegrityError:
                await self.session.rollback()
            return "tombstoned"

        attempt = await self.session.scalar(
            select(PaymentAttempt)
            .where(
                PaymentAttempt.merchant_trade_no
                == payload["MerchantTradeNo"]
            )
            .options(
                selectinload(PaymentAttempt.order).selectinload(Order.items),
                selectinload(PaymentAttempt.order).selectinload(
                    Order.meal_event
                ),
                selectinload(PaymentAttempt.order).selectinload(
                    Order.fulfillment
                ),
                selectinload(
                    PaymentAttempt.membership_charge
                ).selectinload(MembershipCharge.membership),
                selectinload(
                    PaymentAttempt.membership_charge
                ).selectinload(MembershipCharge.application),
                selectinload(PaymentAttempt.reservations),
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if attempt is None:
            raise PaymentApplicationError("找不到付款嘗試")
        try:
            received_amount = int(payload.get("TradeAmt", ""))
        except ValueError as exc:
            raise PaymentApplicationError("付款回傳金額格式錯誤") from exc
        if received_amount != attempt.amount:
            raise PaymentApplicationError("付款回傳金額不符")

        event = ExternalEvent(
            provider="ecpay_aio",
            external_event_key=event_key,
            event_type="payment_callback",
            payload=dict(payload),
            processed=False,
        )
        self.session.add(event)
        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            return "duplicate"

        if payload.get("SimulatePaid") == "1":
            attempt.provider_response = dict(payload)
            if attempt.status == PaymentStatus.PENDING:
                attempt.status = PaymentStatus.FAILED
                await self._release_attempt_reservations(attempt)
            event.processed = True
            event.processed_at = datetime.now(timezone.utc)
            await self.session.commit()
            return "simulated_payment_rejected"

        if payload.get("RtnCode") != "1":
            if attempt.status == PaymentStatus.PENDING:
                attempt.status = PaymentStatus.FAILED
                attempt.provider_response = dict(payload)
                await self._release_attempt_reservations(attempt)
            event.processed = True
            event.processed_at = datetime.now(timezone.utc)
            await self.session.commit()
            return "failed"

        if attempt.status in SUCCESS_CALLBACK_TERMINAL_STATUSES:
            event.processed = True
            event.processed_at = datetime.now(timezone.utc)
            await self.session.commit()
            return "duplicate"

        await self._apply_success(attempt, payload)
        event.processed = True
        event.processed_at = datetime.now(timezone.utc)
        await self.session.commit()
        return attempt.status.value

    async def apply_query_result(
        self, attempt: PaymentAttempt, payload: Mapping[str, str]
    ) -> str:
        synthetic = dict(payload)
        synthetic.setdefault("MerchantTradeNo", attempt.merchant_trade_no)
        synthetic.setdefault("TradeAmt", str(attempt.amount))
        synthetic.setdefault("RtnCode", "1")
        key = callback_event_key(synthetic)
        return await self.apply_ecpay_payment_callback(key, synthetic)

    async def _apply_success(
        self, attempt: PaymentAttempt, payload: Mapping[str, str]
    ) -> None:
        current = datetime.now(timezone.utc)
        order = attempt.order
        attempt.provider_trade_no = payload.get("TradeNo") or payload.get(
            "TradeID"
        )
        attempt.provider_response = dict(payload)

        if attempt.membership_charge_id is not None:
            loaded_charge = attempt.membership_charge
            if loaded_charge is None:
                raise PaymentApplicationError("付款嘗試缺少入社款項")
            application = await self.session.scalar(
                select(MembershipApplication)
                .where(
                    MembershipApplication.id == loaded_charge.application_id
                )
                .execution_options(populate_existing=True)
                .with_for_update()
            )
            membership = await self.session.scalar(
                select(Membership)
                .where(Membership.id == loaded_charge.membership_id)
                .execution_options(populate_existing=True)
                .with_for_update()
            )
            charge = await self.session.scalar(
                select(MembershipCharge)
                .where(MembershipCharge.id == loaded_charge.id)
                .execution_options(populate_existing=True)
                .with_for_update()
            )
            if application is None or membership is None or charge is None:
                raise PaymentApplicationError("入社付款主體不完整")
            charge_user = await self.session.get(User, charge.user_id)
            service = NotificationService(
                SQLAlchemyNotificationRepository(self.session)
            )
            if (
                application.status
                not in {
                    MembershipApplicationStatus.SUBMITTED,
                    MembershipApplicationStatus.NEEDS_SUPPLEMENT,
                    MembershipApplicationStatus.APPROVED,
                }
                or membership.status != MembershipStatus.PENDING_PAYMENT
            ):
                attempt.status = PaymentStatus.LATE_PAID_REFUND_REQUIRED
                attempt.paid_at = current
                if charge.status != MembershipChargeStatus.PAID:
                    charge.status = MembershipChargeStatus.REFUNDED
                    charge.paid_at = charge.paid_at or current
                    charge.refunded_at = current
                self.session.add(
                    Refund(
                        membership_charge_id=charge.id,
                        amount=charge.amount,
                        status=RefundStatus.COMPLETED,
                        reason="入社申請已撤回或失效",
                        requested_by_id=charge.user_id,
                        completed_at=current,
                    )
                )
                await service.publish(
                    NotificationCommand(
                        user_id=charge.user_id,
                        event_type="membership_refund_completed",
                        title="入社款項已建立 Sandbox 退款",
                        body="申請已撤回或失效，本次付款已建立系統退款紀錄。",
                        data={"membership_charge_id": charge.id},
                        email=charge_user.email if charge_user else None,
                        dedupe_key=f"membership-refund:{attempt.id}",
                    )
                )
                return
            if charge.status != MembershipChargeStatus.PENDING:
                attempt.status = PaymentStatus.LATE_PAID_REFUND_REQUIRED
                attempt.paid_at = current
                self.session.add(
                    Refund(
                        membership_charge_id=charge.id,
                        amount=charge.amount,
                        status=RefundStatus.COMPLETED,
                        reason="入社款項重複付款",
                        requested_by_id=charge.user_id,
                        completed_at=current,
                    )
                )
                await service.publish(
                    NotificationCommand(
                        user_id=charge.user_id,
                        event_type="membership_refund_completed",
                        title="重複付款已建立 Sandbox 退款",
                        body="系統偵測到入社款項重複付款，已建立退款紀錄。",
                        data={"membership_charge_id": charge.id},
                        email=charge_user.email if charge_user else None,
                        dedupe_key=f"membership-refund:{attempt.id}",
                    )
                )
                return
            attempt.status = PaymentStatus.PAID
            attempt.paid_at = current
            charge.status = MembershipChargeStatus.PAID
            charge.paid_at = current
            charge.receipt_number = (
                f"SLFR-{current:%Y%m%d}-{charge.id.replace('-', '')[:8].upper()}"
            )
            from ..routers.membership import start_traineeship_if_fully_paid

            membership = await start_traineeship_if_fully_paid(
                self.session,
                charge.membership_id,
                current,
            )
            await service.publish(
                NotificationCommand(
                    user_id=charge.user_id,
                    event_type="membership_charge_paid",
                    title="入社款項付款成功",
                    body=f"{charge.charge_kind.value} 已付款成功。",
                    data={
                        "membership_charge_id": charge.id,
                        "membership_id": charge.membership_id,
                        "membership_status": (
                            membership.status.value
                            if membership is not None
                            else None
                        ),
                    },
                    email=charge_user.email if charge_user else None,
                    dedupe_key=f"membership-payment:{attempt.id}",
                )
            )
            return

        if order is None:
            raise PaymentApplicationError("付款嘗試缺少付款主體")

        if order.sales_channel == SalesChannel.MEAL_PREORDER:
            meal_event = None
            if order.meal_event_id is not None:
                meal_event = await self.session.scalar(
                    select(MealEvent)
                    .where(MealEvent.id == order.meal_event_id)
                    .execution_options(populate_existing=True)
                    .with_for_update()
                )
            if (
                order.fulfillment_status == FulfillmentStatus.CANCELLED
                or meal_event is None
                or meal_event.status == MealEventStatus.CANCELLED
                or not await self._consume_meal_reservations(
                    attempt,
                    payload,
                    current,
                )
            ):
                await release_attempt_reservations(self.session, attempt)
                await self._mark_late_refund(
                    order,
                    attempt,
                    "此筆付款未能重新取得便當預購數量",
                )
                await self._publish_payment_notification(
                    order,
                    attempt,
                    "payment_refund_required",
                    "付款已收到，正在處理退款",
                    "便當預購數量無法保留，系統會以 Sandbox 退款處理。",
                )
                return
        elif order.order_kind == OrderKind.GROUP:
            campaign = await self.session.scalar(
                select(GroupCampaign)
                .where(GroupCampaign.id == order.group_campaign_id)
                .with_for_update()
            )
            reservation = next(
                (
                    item
                    for item in attempt.reservations
                    if item.group_campaign_id is not None
                ),
                None,
            )
            payment_time = _provider_payment_time(payload) or current
            originally_in_window = (
                reservation is not None
                and payment_time <= _aware(attempt.expires_at)
            )
            can_reacquire = (
                campaign is not None
                and reservation is not None
                and campaign.paid_quantity
                + campaign.reserved_quantity
                + reservation.quantity
                <= campaign.supply_cap
                and (
                    originally_in_window
                    or payment_time < _aware(campaign.deadline)
                )
            )
            if (
                reservation is not None
                and reservation.status != ReservationStatus.ACTIVE
                and can_reacquire
            ):
                campaign.reserved_quantity += reservation.quantity
                reservation.status = ReservationStatus.ACTIVE
                reservation.released_at = None
            capacity_available = (
                campaign is not None
                and reservation is not None
                and reservation.status == ReservationStatus.ACTIVE
                and order.fulfillment_status != FulfillmentStatus.CANCELLED
                and (
                    originally_in_window
                    or payment_time < _aware(campaign.deadline)
                )
                and campaign.paid_quantity + reservation.quantity
                <= campaign.supply_cap
                and campaign.decision_status
                not in {
                    GroupDecisionStatus.REJECTED,
                    GroupDecisionStatus.FAILED_UNMET,
                    GroupDecisionStatus.EXPIRED_UNCONFIRMED,
                    GroupDecisionStatus.CANCELLED,
                }
            )
            if not capacity_available:
                await release_attempt_reservations(self.session, attempt)
                await self._mark_late_refund(
                    order,
                    attempt,
                    "此筆付款未能保留團購名額",
                )
                await self._publish_payment_notification(
                    order,
                    attempt,
                    "payment_refund_required",
                    "付款已收到，正在處理退款",
                    "此筆付款未能保留名額，系統會以 Sandbox 退款處理。",
                )
                return
            was_recruiting = (
                campaign.decision_status == GroupDecisionStatus.RECRUITING
            )
            reservation.status = ReservationStatus.CONSUMED
            apply_paid_quantity(campaign, reservation.quantity, current)
            if (
                was_recruiting
                and campaign.decision_status
                == GroupDecisionStatus.PENDING_CONFIRMATION
            ):
                admins = list(
                    await self.session.scalars(
                        select(User).where(User.user_role == UserRole.ADMIN)
                    )
                )
                service = NotificationService(
                    SQLAlchemyNotificationRepository(self.session)
                )
                for admin in admins:
                    await service.publish(
                        NotificationCommand(
                            user_id=admin.id,
                            event_type="group_threshold_reached",
                            title="團購已達付款門檻",
                            body="「{}」已達成團門檻，請在期限內確認。".format(
                                campaign.title
                            ),
                            data={"campaign_id": campaign.id},
                            dedupe_key="group-threshold:{}".format(campaign.id),
                        )
                    )
        else:
            if (
                order.fulfillment_status == FulfillmentStatus.CANCELLED
                or not await self._consume_regular_reservations(
                attempt, payload, current
                )
            ):
                await release_attempt_reservations(self.session, attempt)
                await self._mark_late_refund(
                    order,
                    attempt,
                    "此筆付款未能重新取得商品庫存",
                )
                await self._publish_payment_notification(
                    order,
                    attempt,
                    "payment_refund_required",
                    "付款已收到，正在處理退款",
                    "商品庫存無法重新保留，系統會以 Sandbox 退款處理。",
                )
                return

        attempt.status = PaymentStatus.PAID
        attempt.paid_at = current
        order.payment_status = PaymentStatus.PAID
        order.paid_at = current
        if order.membership_type_snapshot == MembershipType.MEMBER:
            account = await self.session.scalar(
                select(PointAccount).where(PointAccount.user_id == order.user_id)
            )
            if account is None:
                account = PointAccount(user_id=order.user_id)
                self.session.add(account)
                await self.session.flush()
            existing_points = await self.session.scalar(
                select(PointTransaction.id).where(
                    PointTransaction.account_id == account.id,
                    PointTransaction.source_type == PointSourceType.PURCHASE,
                    PointTransaction.reference_id == order.id,
                )
            )
            earned = order.amount_total // 100
            if existing_points is None and earned > 0:
                self.session.add(
                    PointTransaction(
                        account_id=account.id,
                        amount=earned,
                        source_type=PointSourceType.PURCHASE,
                        reference_id=order.id,
                        note="消費累積",
                    )
                )
        await self._publish_payment_notification(
            order,
            attempt,
            "payment_succeeded",
            "付款成功",
            "訂單 {} 已付款成功。".format(order.order_number),
        )

    async def _consume_meal_reservations(
        self,
        attempt: PaymentAttempt,
        payload: Mapping[str, str],
        current: datetime,
    ) -> bool:
        order = attempt.order
        if order is None or order.meal_event is None:
            return False
        payment_time = _provider_payment_time(payload) or current
        originally_in_window = payment_time <= _aware(attempt.expires_at)
        if payment_time > _aware(attempt.expires_at):
            await release_attempt_reservations(self.session, attempt)
        for reservation in attempt.reservations:
            if reservation.source_meal_offering_id is None:
                continue
            offering = await self.session.scalar(
                select(MealEventOffering)
                .where(
                    MealEventOffering.id
                    == reservation.source_meal_offering_id
                )
                .with_for_update()
            )
            if offering is None:
                return False
            if reservation.status != ReservationStatus.ACTIVE:
                if (
                    offering.capacity
                    - offering.reserved_quantity
                    - offering.paid_quantity
                    < reservation.quantity
                    or (
                        not originally_in_window
                        and payment_time
                        >= _aware(order.meal_event.ordering_ends_at)
                    )
                ):
                    return False
                offering.reserved_quantity += reservation.quantity
                reservation.status = ReservationStatus.ACTIVE
                reservation.released_at = None
        for reservation in attempt.reservations:
            if (
                reservation.status == ReservationStatus.ACTIVE
                and reservation.source_meal_offering_id is not None
            ):
                offering = await self.session.scalar(
                    select(MealEventOffering)
                    .where(
                        MealEventOffering.id
                        == reservation.source_meal_offering_id
                    )
                    .with_for_update()
                )
                if offering is None:
                    return False
                offering.reserved_quantity = max(
                    0,
                    offering.reserved_quantity - reservation.quantity,
                )
                offering.paid_quantity += reservation.quantity
                reservation.status = ReservationStatus.CONSUMED
        return True

    async def _mark_late_refund(
        self,
        order: Order,
        attempt: PaymentAttempt,
        reason: str,
    ) -> None:
        current = datetime.now(timezone.utc)
        attempt.status = PaymentStatus.LATE_PAID_REFUND_REQUIRED
        attempt.paid_at = current
        order.payment_status = PaymentStatus.LATE_PAID_REFUND_REQUIRED
        order.paid_at = current
        refund = Refund(
            order=order,
            amount=order.amount_total,
            status=RefundStatus.PENDING,
            reason=reason,
            requested_by_id=order.user_id,
        )
        self.session.add(refund)
        await self.session.flush()
        self.session.add(
            OutboxEvent(
                event_type="refund.requested",
                aggregate_type="refund",
                aggregate_id=refund.id,
                payload={"refund_id": refund.id, "order_id": order.id},
            )
        )

    async def _publish_payment_notification(
        self,
        order: Order,
        attempt: PaymentAttempt,
        event_type: str,
        title: str,
        body: str,
    ) -> None:
        service = NotificationService(
            SQLAlchemyNotificationRepository(self.session)
        )
        await service.publish(
            NotificationCommand(
                user_id=order.user_id,
                event_type=event_type,
                title=title,
                body=body,
                data={"order_id": order.id},
                email=order.contact_email,
                dedupe_key="payment:{}:{}".format(attempt.id, event_type),
            )
        )

    async def _consume_regular_reservations(
        self,
        attempt: PaymentAttempt,
        payload: Mapping[str, str],
        current: datetime,
    ) -> bool:
        payment_time = _provider_payment_time(payload) or current
        if payment_time > _aware(attempt.expires_at):
            await release_attempt_reservations(self.session, attempt)
        products_to_reacquire = []
        for reservation in attempt.reservations:
            if reservation.source_product_id is None:
                continue
            if reservation.status == ReservationStatus.ACTIVE:
                continue
            product = await self.session.scalar(
                select(Product)
                .where(Product.id == reservation.source_product_id)
                .with_for_update()
            )
            if product is None or product.stock_quantity < reservation.quantity:
                return False
            products_to_reacquire.append((product, reservation))
        for product, reservation in products_to_reacquire:
            product.stock_quantity -= reservation.quantity
            reservation.status = ReservationStatus.ACTIVE
            reservation.released_at = None
        for reservation in attempt.reservations:
            if reservation.status == ReservationStatus.ACTIVE:
                reservation.status = ReservationStatus.CONSUMED
        return True

    async def _release_attempt_reservations(
        self, attempt: PaymentAttempt
    ) -> None:
        await release_attempt_reservations(self.session, attempt)


async def release_attempt_reservations(
    session: AsyncSession, attempt: PaymentAttempt
) -> None:
    current = datetime.now(timezone.utc)
    for reservation in attempt.reservations:
        if reservation.status != ReservationStatus.ACTIVE:
            continue
        if reservation.group_campaign_id is not None:
            campaign = await session.scalar(
                select(GroupCampaign)
                .where(GroupCampaign.id == reservation.group_campaign_id)
                .with_for_update()
            )
            if campaign is not None:
                campaign.reserved_quantity = max(
                    0, campaign.reserved_quantity - reservation.quantity
                )
        elif reservation.source_product_id is not None:
            product = await session.scalar(
                select(Product)
                .where(Product.id == reservation.source_product_id)
                .with_for_update()
            )
            if product is not None:
                product.stock_quantity += reservation.quantity
        elif reservation.source_meal_offering_id is not None:
            offering = await session.scalar(
                select(MealEventOffering)
                .where(
                    MealEventOffering.id
                    == reservation.source_meal_offering_id
                )
                .with_for_update()
            )
            if offering is not None:
                offering.reserved_quantity = max(
                    0,
                    offering.reserved_quantity - reservation.quantity,
                )
        reservation.status = ReservationStatus.RELEASED
        reservation.released_at = current


def _provider_payment_time(payload: Mapping[str, str]) -> Optional[datetime]:
    raw = payload.get("PaymentDate") or payload.get("TradeDate")
    if not raw:
        return None
    try:
        from zoneinfo import ZoneInfo

        local = datetime.strptime(raw, "%Y/%m/%d %H:%M:%S").replace(
            tzinfo=ZoneInfo("Asia/Taipei")
        )
        return local.astimezone(timezone.utc)
    except ValueError:
        return None
