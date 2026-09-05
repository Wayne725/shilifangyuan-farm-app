from __future__ import annotations

import hmac
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Mapping, Optional

from sqlalchemy import case, exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import set_committed_value

from ..config import Settings
from ..domain import (
    apply_paid_quantity,
    order_fulfillment_is_irreversible,
    remove_paid_quantity,
)
from ..models import (
    ExternalEvent,
    FulfillmentMethod,
    FulfillmentState,
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
    Shipment,
    ShipmentStatus,
    User,
    UserRole,
)
from .ecpay import (
    ECPayAIOAdapter,
    ECPayAIOSettings,
    LocalSandboxRefundAdapter,
    callback_event_key as ecpay_callback_event_key,
    create_merchant_trade_no,
)
from .raygate import (
    RAYGATE_PAYMENT_PROVIDER,
    RayGateAdapter,
    RayGateSettings,
    canonical_event_key as raygate_event_key,
)
from .notifications import (
    NotificationCommand,
    NotificationService,
    SQLAlchemyNotificationRepository,
)
from .invoice import is_mobile_barcode_format
from .invoice_service import (
    enqueue_invoice_adjustment_after_refund,
    enqueue_invoice_issue,
)


class PaymentApplicationError(ValueError):
    pass


RAYGATE_PREVIEW_ACCEPTANCE_AMOUNT = 10


SUCCESS_CALLBACK_TERMINAL_STATUSES = {
    PaymentStatus.PAID,
    PaymentStatus.LATE_PAID_REFUND_REQUIRED,
    PaymentStatus.REFUND_PENDING,
    PaymentStatus.REFUNDED,
}


def ensure_payment_runtime_enabled(
    settings: Settings,
    *,
    order_id: Optional[str] = None,
    amount: Optional[int] = None,
    fulfillment_method: Optional[FulfillmentMethod] = None,
    product_sku: Optional[str] = None,
    item_count: Optional[int] = None,
    item_quantity: Optional[int] = None,
    item_unit_price: Optional[int] = None,
    item_subtotal: Optional[int] = None,
    order_kind: Optional[OrderKind] = None,
) -> None:
    if settings.environment.strip().lower() != "preview":
        return
    configured_order_id = settings.raygate_payment_acceptance_order_id.strip()
    configured_sku = settings.raygate_payment_acceptance_sku.strip()
    common_requirements_met = (
        settings.payment_provider == RAYGATE_PAYMENT_PROVIDER
        and not settings.raygate_payment_stage
        and amount == RAYGATE_PREVIEW_ACCEPTANCE_AMOUNT
        and fulfillment_method == FulfillmentMethod.COOPERATIVE_PICKUP
    )
    order_allowlisted = (
        configured_order_id
        and order_id
        and hmac.compare_digest(configured_order_id, order_id)
    )
    sku_allowlisted = (
        configured_sku
        and product_sku
        and hmac.compare_digest(configured_sku, product_sku)
        and item_count == 1
        and item_quantity == 1
        and item_unit_price == RAYGATE_PREVIEW_ACCEPTANCE_AMOUNT
        and item_subtotal == RAYGATE_PREVIEW_ACCEPTANCE_AMOUNT
        and order_kind == OrderKind.REGULAR
    )
    if common_requirements_met and (order_allowlisted or sku_allowlisted):
        return
    raise PaymentApplicationError("Preview 展示環境不提供線上付款")


async def ensure_order_payment_runtime_enabled(
    session: AsyncSession,
    settings: Settings,
    order: Order,
    *,
    amount: Optional[int] = None,
) -> None:
    acceptance_item = order.items[0] if len(order.items) == 1 else None
    acceptance_product_sku = None
    if (
        settings.environment.strip().lower() == "preview"
        and settings.raygate_payment_acceptance_sku.strip()
        and acceptance_item is not None
        and acceptance_item.source_product_id is not None
    ):
        acceptance_product_sku = await session.scalar(
            select(Product.sku).where(
                Product.id == acceptance_item.source_product_id
            )
        )
    ensure_payment_runtime_enabled(
        settings,
        order_id=order.id,
        amount=order.amount_total if amount is None else amount,
        fulfillment_method=order.fulfillment_method,
        product_sku=acceptance_product_sku,
        item_count=len(order.items),
        item_quantity=(acceptance_item.quantity if acceptance_item else None),
        item_unit_price=(acceptance_item.unit_price if acceptance_item else None),
        item_subtotal=(acceptance_item.subtotal if acceptance_item else None),
        order_kind=order.order_kind,
    )


def allow_local_refund_without_payment_attempt(settings: Settings) -> bool:
    return settings.environment.strip().lower() in {
        "development",
        "test",
        "preview",
    }


def raygate_payment_adapter_from_settings(settings: Settings) -> RayGateAdapter:
    return RayGateAdapter(
        RayGateSettings(
            store_identifier=settings.raygate_payment_store_identifier,
            key_hex=settings.raygate_payment_key_hex,
            iv_hex=settings.raygate_payment_iv_hex,
            base_url=settings.raygate_payment_base_url,
            allowed_hostname=settings.raygate_payment_allowed_hostname,
            merchant_id=settings.raygate_payment_merchant_id,
            terminal_id=settings.raygate_payment_terminal_id,
            device_type=settings.raygate_payment_device_type,
            timeout_seconds=getattr(settings, "integration_timeout_seconds", 15.0),
        )
    )


def payment_adapter_from_settings(
    settings: Settings,
    provider: Optional[str] = None,
) -> Any:
    selected = (provider or settings.payment_provider).strip().lower()
    if selected == RAYGATE_PAYMENT_PROVIDER:
        return raygate_payment_adapter_from_settings(settings)
    if selected != "ecpay":
        raise ValueError("不支援的付款服務")
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


def refund_adapter_from_settings(settings: Settings, provider: str) -> Any:
    if provider == RAYGATE_PAYMENT_PROVIDER:
        return raygate_payment_adapter_from_settings(settings)
    if provider != "ecpay":
        raise PaymentApplicationError("不支援的退款服務")
    if settings.environment.strip().lower() == "production":
        raise PaymentApplicationError("正式環境尚未啟用 ECPay 自動退款")
    return LocalSandboxRefundAdapter()


async def reverse_order_purchase_points(
    session: AsyncSession,
    order: Order,
    refund_id: str,
) -> None:
    account = await session.scalar(
        select(PointAccount).where(PointAccount.user_id == order.user_id)
    )
    if account is None:
        return
    earned = await session.scalar(
        select(PointTransaction.amount).where(
            PointTransaction.account_id == account.id,
            PointTransaction.source_type == PointSourceType.PURCHASE,
            PointTransaction.reference_id == order.id,
        )
    )
    if not earned or earned <= 0:
        return
    existing = await session.scalar(
        select(PointTransaction.id).where(
            PointTransaction.account_id == account.id,
            PointTransaction.source_type == PointSourceType.REFUND,
            PointTransaction.reference_id == refund_id,
        )
    )
    if existing is None:
        session.add(
            PointTransaction(
                account_id=account.id,
                amount=-earned,
                source_type=PointSourceType.REFUND,
                reference_id=refund_id,
                note=f"訂單 {order.order_number} 退款沖銷",
            )
        )


async def remaining_subject_payment_status(
    session: AsyncSession,
    attempt: PaymentAttempt,
) -> Optional[PaymentStatus]:
    subject_filter = (
        PaymentAttempt.order_id == attempt.order_id
        if attempt.order_id is not None
        else PaymentAttempt.membership_charge_id == attempt.membership_charge_id
    )
    statuses = set(
        await session.scalars(
            select(PaymentAttempt.status).where(
                subject_filter,
                PaymentAttempt.id != attempt.id,
                PaymentAttempt.status.in_(
                    {
                        PaymentStatus.PAID,
                        PaymentStatus.REFUND_PENDING,
                        PaymentStatus.LATE_PAID_REFUND_REQUIRED,
                    }
                ),
            )
        )
    )
    if PaymentStatus.PAID in statuses:
        return PaymentStatus.PAID
    if statuses:
        return PaymentStatus.REFUND_PENDING
    return None


async def create_provider_aware_refund(
    session: AsyncSession,
    *,
    amount: int,
    reason: str,
    requested_by_id: str,
    order: Optional[Order] = None,
    membership_charge: Optional[MembershipCharge] = None,
    payment_attempt: Optional[PaymentAttempt] = None,
    defer_completion: bool = False,
    allow_unbound_local_completion: bool = False,
    now: Optional[datetime] = None,
) -> tuple[Refund, bool]:
    if (order is None) == (membership_charge is None):
        raise ValueError("退款必須且只能指定一種付款主體")
    if payment_attempt is None:
        subject_filter = (
            PaymentAttempt.order_id == order.id
            if order is not None
            else PaymentAttempt.membership_charge_id == membership_charge.id
        )
        payment_attempt = await session.scalar(
            select(PaymentAttempt)
            .where(
                subject_filter,
                PaymentAttempt.status.in_(
                    {
                        PaymentStatus.PAID,
                        PaymentStatus.LATE_PAID_REFUND_REQUIRED,
                        PaymentStatus.REFUND_PENDING,
                    }
                ),
                ~exists().where(
                    Refund.payment_attempt_id == PaymentAttempt.id
                ),
            )
            .order_by(
                case(
                    (PaymentAttempt.status == PaymentStatus.PAID, 0),
                    (
                        PaymentAttempt.status
                        == PaymentStatus.LATE_PAID_REFUND_REQUIRED,
                        1,
                    ),
                    else_=2,
                ),
                PaymentAttempt.paid_at.desc(),
                PaymentAttempt.created_at.desc(),
            )
            .limit(1)
            .with_for_update()
        )
    if payment_attempt is None and not allow_unbound_local_completion:
        raise PaymentApplicationError("找不到可退款的付款交易")
    provider = payment_attempt.provider if payment_attempt is not None else "ecpay"
    requires_provider_refund = (
        provider == RAYGATE_PAYMENT_PROVIDER or defer_completion
    )
    if (
        provider == RAYGATE_PAYMENT_PROVIDER
        and not payment_attempt.provider_trade_no
    ):
        raise PaymentApplicationError("雷門付款缺少交易編號，無法送出退款")
    if payment_attempt is not None:
        existing_refund_id = await session.scalar(
            select(Refund.id).where(
                Refund.payment_attempt_id == payment_attempt.id
            )
        )
        if existing_refund_id is not None:
            raise PaymentApplicationError("此筆付款已有退款紀錄")

    current = now or datetime.now(timezone.utc)
    refund = Refund(
        order=order,
        membership_charge=membership_charge,
        payment_attempt=payment_attempt,
        provider=provider,
        amount=amount,
        status=(
            RefundStatus.PENDING
            if requires_provider_refund
            else RefundStatus.COMPLETED
        ),
        reason=reason,
        requested_by_id=requested_by_id,
        completed_at=None if requires_provider_refund else current,
    )
    session.add(refund)
    await session.flush()
    if requires_provider_refund:
        if payment_attempt.status == PaymentStatus.PAID:
            payment_attempt.status = PaymentStatus.REFUND_PENDING
        session.add(
            OutboxEvent(
                event_type="refund.requested",
                aggregate_type="refund",
                aggregate_id=refund.id,
                payload={
                    "refund_id": refund.id,
                    "order_id": order.id if order is not None else None,
                    "membership_charge_id": (
                        membership_charge.id
                        if membership_charge is not None
                        else None
                    ),
                },
            )
        )
    return refund, requires_provider_refund


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


def _build_checkout_payload(
    *,
    settings: Settings,
    attempt: PaymentAttempt,
    amount: int,
    item_name: str,
    client_back_url: str,
    current: datetime,
    custom_fields: Mapping[str, str],
) -> Dict[str, str]:
    adapter = (
        payment_adapter_from_settings(settings)
        if attempt.provider == settings.payment_provider
        else payment_adapter_from_settings(settings, attempt.provider)
    )
    if attempt.provider == RAYGATE_PAYMENT_PROVIDER:
        return {
            "redirect_url": adapter.create_checkout_url(
                pos_order_number=attempt.merchant_trade_no,
                amount=amount,
                callback_url="{}/webhooks/raygate/payment".format(
                    settings.app_base_url.rstrip("/")
                ),
                return_url=(
                    "{}/payments/raygate/result?attempt_id={}".format(
                        settings.app_base_url.rstrip("/"), attempt.id
                    )
                ),
            )
        }
    form = adapter.create_checkout_form(
        merchant_trade_no=attempt.merchant_trade_no,
        amount=amount,
        item_name=item_name,
        return_url="{}/webhooks/ecpay/payment".format(
            settings.app_base_url.rstrip("/")
        ),
        order_result_url="{}/payments/result".format(
            settings.app_base_url.rstrip("/")
        ),
        client_back_url=client_back_url,
        custom_fields=custom_fields,
        now=current,
    )
    return dict(form.fields)


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
    await ensure_order_payment_runtime_enabled(session, settings, order)
    if order.cancelled_at is not None or order.fulfillment_status == FulfillmentStatus.CANCELLED:
        raise PaymentApplicationError("此訂單已取消，無法付款")
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
        provider=settings.payment_provider,
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
            .execution_options(populate_existing=True)
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

    attempt.checkout_payload = _build_checkout_payload(
        settings=settings,
        attempt=attempt,
        amount=order.amount_total,
        item_name="#".join(
            "{} x{}".format(item.product_name, item.quantity)
            for item in order.items
        ),
        client_back_url="{}/orders?order_id={}".format(
            settings.web_base_url.rstrip("/"), order.id
        ),
        custom_fields={"CustomField1": order.id},
        current=current,
    )
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
    ensure_payment_runtime_enabled(settings)
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
        provider=settings.payment_provider,
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
    attempt.checkout_payload = _build_checkout_payload(
        settings=settings,
        attempt=attempt,
        amount=charge.amount,
        item_name=item_name,
        client_back_url="{}/account".format(
            settings.web_base_url.rstrip("/")
        ),
        custom_fields={
            "CustomField1": charge.id,
            "CustomField2": "membership_charge",
        },
        current=current,
    )
    await session.commit()
    await session.refresh(attempt)
    return attempt


class SQLAlchemyPaymentCallbackRepository:
    """Applies a verified provider callback once inside the request transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def apply_ecpay_payment_callback(
        self, event_key: str, payload: Mapping[str, str]
    ) -> str:
        return await self.apply_payment_callback("ecpay", event_key, payload)

    async def apply_raygate_payment_callback(
        self, event_key: str, payload: Mapping[str, str]
    ) -> str:
        return await self.apply_payment_callback(
            RAYGATE_PAYMENT_PROVIDER, event_key, payload
        )

    async def apply_payment_callback(
        self,
        provider: str,
        event_key: str,
        payload: Mapping[str, str],
    ) -> str:
        event_provider = (
            "ecpay_aio" if provider == "ecpay" else "raygate_payment"
        )
        existing_event = await self.session.scalar(
            select(ExternalEvent).where(
                ExternalEvent.provider == event_provider,
                ExternalEvent.external_event_key == event_key,
            )
        )
        if existing_event is not None:
            return "duplicate"

        tombstone = await self.session.scalar(
            select(ExternalEvent).where(
                ExternalEvent.provider == f"{provider}_reset",
                ExternalEvent.external_event_key
                == payload["MerchantTradeNo"],
            )
        )
        if tombstone is not None:
            self.session.add(
                ExternalEvent(
                    provider=event_provider,
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
                selectinload(PaymentAttempt.order)
                .selectinload(Order.fulfillment)
                .selectinload(OrderFulfillment.shipment),
                selectinload(PaymentAttempt.order).selectinload(Order.invoice),
                selectinload(PaymentAttempt.order).selectinload(Order.user),
                selectinload(PaymentAttempt.order).selectinload(
                    Order.group_campaign
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
        if attempt.provider != provider:
            raise PaymentApplicationError("付款服務與付款嘗試不符")
        try:
            received_amount = int(payload.get("TradeAmt", ""))
        except ValueError as exc:
            raise PaymentApplicationError("付款回傳金額格式錯誤") from exc
        if received_amount != attempt.amount:
            raise PaymentApplicationError("付款回傳金額不符")

        event = ExternalEvent(
            provider=event_provider,
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

        disposition = payload.get("PaymentDisposition")
        if (
            provider == RAYGATE_PAYMENT_PROVIDER
            and disposition == "refunded"
        ):
            if attempt.status == PaymentStatus.REFUNDED:
                event.processed = True
                event.processed_at = datetime.now(timezone.utc)
                await self.session.commit()
                return "duplicate"
            await self._apply_provider_refund(attempt, payload)
            event.processed = True
            event.processed_at = datetime.now(timezone.utc)
            await self.session.commit()
            return "refunded"

        if (
            provider == RAYGATE_PAYMENT_PROVIDER
            and disposition == "refund_failed"
        ):
            await self._apply_provider_refund_failure(attempt, payload)
            event.processed = True
            event.processed_at = datetime.now(timezone.utc)
            await self.session.commit()
            return "refund_failed"

        if attempt.status in SUCCESS_CALLBACK_TERMINAL_STATUSES:
            event.processed = True
            event.processed_at = datetime.now(timezone.utc)
            await self.session.commit()
            return "duplicate"

        if provider == "ecpay" and payload.get("SimulatePaid") == "1":
            attempt.provider_response = dict(payload)
            if attempt.status == PaymentStatus.PENDING:
                attempt.status = PaymentStatus.FAILED
                await self._release_attempt_reservations(attempt)
            event.processed = True
            event.processed_at = datetime.now(timezone.utc)
            await self.session.commit()
            return "simulated_payment_rejected"

        if disposition == "pending":
            attempt.provider_response = dict(payload)
            event.processed = True
            event.processed_at = datetime.now(timezone.utc)
            await self.session.commit()
            return "pending"

        if payload.get("RtnCode") != "1":
            if attempt.status == PaymentStatus.PENDING:
                attempt.status = PaymentStatus.FAILED
                attempt.provider_response = dict(payload)
                await self._release_attempt_reservations(attempt)
            event.processed = True
            event.processed_at = datetime.now(timezone.utc)
            await self.session.commit()
            return "failed"

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
        if attempt.provider == RAYGATE_PAYMENT_PROVIDER:
            if synthetic.get("PaymentDisposition") not in {"paid", "refunded"}:
                synthetic["PaymentDisposition"] = "pending"
            key = raygate_event_key(synthetic)
            return await self.apply_raygate_payment_callback(key, synthetic)
        key = ecpay_callback_event_key(synthetic)
        return await self.apply_ecpay_payment_callback(key, synthetic)

    async def _apply_success(
        self, attempt: PaymentAttempt, payload: Mapping[str, str]
    ) -> None:
        current = datetime.now(timezone.utc)
        payment_time = _provider_payment_time(payload) or current
        order = attempt.order
        await self._assign_provider_trade_no(attempt, payload)
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
                attempt.paid_at = payment_time
                charge_was_paid = charge.status == MembershipChargeStatus.PAID
                charge.paid_at = charge.paid_at or payment_time
                _, refund_queued = await create_provider_aware_refund(
                    self.session,
                    membership_charge=charge,
                    payment_attempt=attempt,
                    amount=charge.amount,
                    reason="入社申請已撤回或失效",
                    requested_by_id=charge.user_id,
                    now=current,
                )
                if not charge_was_paid:
                    charge.status = (
                        MembershipChargeStatus.REFUND_PENDING
                        if refund_queued
                        else MembershipChargeStatus.REFUNDED
                    )
                    charge.refunded_at = None if refund_queued else current
                await service.publish(
                    NotificationCommand(
                        user_id=charge.user_id,
                        event_type=(
                            "membership_refund_requested"
                            if refund_queued
                            else "membership_refund_completed"
                        ),
                        title=(
                            "入社款項退款申請已送出"
                            if refund_queued
                            else "入社款項已建立 Sandbox 退款"
                        ),
                        body=(
                            "申請已撤回或失效，本次付款正在向金流確認退款。"
                            if refund_queued
                            else "申請已撤回或失效，本次付款已建立系統退款紀錄。"
                        ),
                        data={"membership_charge_id": charge.id},
                        email=charge_user.email if charge_user else None,
                        dedupe_key=f"membership-refund:{attempt.id}",
                    )
                )
                return
            if charge.status != MembershipChargeStatus.PENDING:
                attempt.status = PaymentStatus.LATE_PAID_REFUND_REQUIRED
                attempt.paid_at = payment_time
                _, refund_queued = await create_provider_aware_refund(
                    self.session,
                    membership_charge=charge,
                    payment_attempt=attempt,
                    amount=charge.amount,
                    reason="入社款項重複付款",
                    requested_by_id=charge.user_id,
                    now=current,
                )
                await service.publish(
                    NotificationCommand(
                        user_id=charge.user_id,
                        event_type=(
                            "membership_refund_requested"
                            if refund_queued
                            else "membership_refund_completed"
                        ),
                        title=(
                            "重複付款退款申請已送出"
                            if refund_queued
                            else "重複付款已建立 Sandbox 退款"
                        ),
                        body=(
                            "系統偵測到入社款項重複付款，正在向金流確認退款。"
                            if refund_queued
                            else "系統偵測到入社款項重複付款，已建立退款紀錄。"
                        ),
                        data={"membership_charge_id": charge.id},
                        email=charge_user.email if charge_user else None,
                        dedupe_key=f"membership-refund:{attempt.id}",
                    )
                )
                return
            attempt.status = PaymentStatus.PAID
            attempt.paid_at = payment_time
            charge.status = MembershipChargeStatus.PAID
            charge.paid_at = payment_time
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

        order = await self.session.scalar(
            select(Order)
            .where(Order.id == order.id)
            .options(
                selectinload(Order.items),
                selectinload(Order.meal_event),
                selectinload(Order.fulfillment).selectinload(
                    OrderFulfillment.shipment
                ),
                selectinload(Order.invoice),
                selectinload(Order.user),
                selectinload(Order.group_campaign),
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if order is None:
            raise PaymentApplicationError("找不到付款訂單")
        await self._reload_locked_attempt_reservations(attempt)
        remaining_payment_status = (
            await remaining_subject_payment_status(self.session, attempt)
        )
        if remaining_payment_status is not None:
            await release_attempt_reservations(self.session, attempt)
            attempt.status = PaymentStatus.LATE_PAID_REFUND_REQUIRED
            attempt.paid_at = payment_time
            refund, refund_queued = await create_provider_aware_refund(
                self.session,
                order=order,
                payment_attempt=attempt,
                amount=attempt.amount,
                reason="訂單重複付款",
                requested_by_id=order.user_id,
                now=current,
            )
            if not refund_queued:
                attempt.status = PaymentStatus.REFUNDED
            order.payment_status = remaining_payment_status
            await self._publish_payment_notification(
                order,
                attempt,
                (
                    "duplicate_payment_refund_requested"
                    if refund_queued
                    else "duplicate_payment_refunded"
                ),
                (
                    "偵測到重複付款，退款申請已送出"
                    if refund_queued
                    else "偵測到重複付款"
                ),
                (
                    f"訂單 {order.order_number} 的重複付款正在向金流確認退款。"
                    if refund_queued
                    else f"訂單 {order.order_number} 的重複付款已建立 Sandbox 退款紀錄。"
                ),
            )
            return

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
                    paid_at=payment_time,
                )
                await self._publish_payment_notification(
                    order,
                    attempt,
                    "payment_refund_required",
                    "付款已收到，正在處理退款",
                    "便當預購數量無法保留，系統已送出退款處理。",
                )
                return
        elif order.order_kind == OrderKind.GROUP:
            campaign = await self.session.scalar(
                select(GroupCampaign)
                .where(GroupCampaign.id == order.group_campaign_id)
                .execution_options(populate_existing=True)
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
                    paid_at=payment_time,
                )
                await self._publish_payment_notification(
                    order,
                    attempt,
                    "payment_refund_required",
                    "付款已收到，正在處理退款",
                    "此筆付款未能保留名額，系統已送出退款處理。",
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
                    paid_at=payment_time,
                )
                await self._publish_payment_notification(
                    order,
                    attempt,
                    "payment_refund_required",
                    "付款已收到，正在處理退款",
                    "商品庫存無法重新保留，系統已送出退款處理。",
                )
                return

        attempt.status = PaymentStatus.PAID
        attempt.paid_at = payment_time
        order.payment_status = PaymentStatus.PAID
        order.paid_at = payment_time
        enqueue_invoice_issue(
            self.session,
            order,
            trigger="payment_confirmed",
        )
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
            "付款成功通知",
            "訂單 {} 已付款成功。".format(order.order_number),
        )

    async def _assign_provider_trade_no(
        self,
        attempt: PaymentAttempt,
        payload: Mapping[str, str],
    ) -> None:
        provider_trade_no = payload.get("TradeNo") or payload.get("TradeID")
        if not provider_trade_no:
            raise PaymentApplicationError("付款回傳缺少金流交易編號")
        if (
            attempt.provider_trade_no
            and attempt.provider_trade_no != provider_trade_no
        ):
            raise PaymentApplicationError("付款嘗試的金流交易編號不一致")
        collision = await self.session.scalar(
            select(PaymentAttempt.id).where(
                PaymentAttempt.provider == attempt.provider,
                PaymentAttempt.provider_trade_no == provider_trade_no,
                PaymentAttempt.id != attempt.id,
            )
        )
        if collision is not None:
            raise PaymentApplicationError("金流交易編號已綁定其他付款嘗試")
        attempt.provider_trade_no = provider_trade_no

    async def _apply_provider_refund(
        self,
        attempt: PaymentAttempt,
        payload: Mapping[str, str],
    ) -> None:
        await self._assign_provider_trade_no(attempt, payload)
        provider_refund_id = payload.get("RayGateAssociatedOrderID", "")
        if not provider_refund_id:
            raise PaymentApplicationError("雷門退款結果缺少退款訂單編號")
        current = _provider_payment_time(payload) or datetime.now(timezone.utc)
        order = attempt.order
        if attempt.order_id is not None:
            order = await self.session.scalar(
                select(Order)
                .where(Order.id == attempt.order_id)
                .options(
                    selectinload(Order.fulfillment).selectinload(
                        OrderFulfillment.shipment
                    ),
                    selectinload(Order.invoice),
                    selectinload(Order.user),
                )
                .execution_options(populate_existing=True)
                .with_for_update()
            )
            if order is None:
                raise PaymentApplicationError("找不到退款訂單")
            await self._reload_locked_attempt_reservations(attempt)
            fulfillment = order.__dict__.get("fulfillment")
            shipment = (
                fulfillment.__dict__.get("shipment")
                if fulfillment is not None
                else None
            )
            if shipment is not None:
                locked_shipment = await self.session.scalar(
                    select(Shipment)
                    .where(Shipment.id == shipment.id)
                    .execution_options(populate_existing=True)
                    .with_for_update()
                )
                if locked_shipment is None:
                    raise PaymentApplicationError("找不到訂單物流資料")
        elif attempt.membership_charge_id is not None:
            await self.session.execute(
                select(MembershipCharge.id)
                .where(MembershipCharge.id == attempt.membership_charge_id)
                .with_for_update()
            )
        subject_filter = (
            Refund.order_id == attempt.order_id
            if attempt.order_id is not None
            else Refund.membership_charge_id == attempt.membership_charge_id
        )
        refund = await self.session.scalar(
            select(Refund)
            .where(Refund.payment_attempt_id == attempt.id)
            .order_by(Refund.created_at.desc())
            .with_for_update()
        )
        if refund is None:
            refund = await self.session.scalar(
                select(Refund)
                .where(
                    Refund.payment_attempt_id.is_(None),
                    Refund.status.in_(
                        {RefundStatus.PENDING, RefundStatus.FAILED}
                    ),
                    subject_filter,
                )
                .order_by(Refund.created_at.desc())
                .with_for_update()
            )
        refund_id = refund.id if refund is not None else None
        provider_refund_collision = await self.session.scalar(
            select(Refund.id).where(
                Refund.provider == attempt.provider,
                Refund.provider_refund_id == provider_refund_id,
                Refund.id != refund_id,
            )
        )
        if provider_refund_collision is not None:
            raise PaymentApplicationError("金流退款編號已綁定其他退款紀錄")
        if refund is None:
            requested_by_id = (
                attempt.order.user_id
                if attempt.order is not None
                else attempt.membership_charge.user_id
            )
            refund = Refund(
                order=attempt.order,
                membership_charge=attempt.membership_charge,
                payment_attempt=attempt,
                provider=attempt.provider,
                provider_refund_id=provider_refund_id,
                provider_response=dict(payload),
                amount=attempt.amount,
                status=RefundStatus.COMPLETED,
                reason="雷門查單確認交易已退款",
                requested_by_id=requested_by_id,
                completed_at=current,
            )
            self.session.add(refund)
            await self.session.flush()
        else:
            refund.payment_attempt = attempt
            refund.provider = attempt.provider
            refund.provider_refund_id = provider_refund_id
            refund.provider_response = dict(payload)
            refund.status = RefundStatus.COMPLETED
            refund.completed_at = current

        remaining_payment_status = (
            await remaining_subject_payment_status(self.session, attempt)
        )
        fulfillment_irreversible = (
            order is not None and order_fulfillment_is_irreversible(order)
        )
        if order is not None:
            await self._release_refunded_attempt_resources(
                attempt,
                current,
                restore_consumed=not fulfillment_irreversible,
            )
        attempt.status = PaymentStatus.REFUNDED
        attempt.provider_response = dict(payload)
        if order is not None:
            order.payment_status = (
                remaining_payment_status or PaymentStatus.REFUNDED
            )
            if remaining_payment_status is None and not fulfillment_irreversible:
                order.fulfillment_status = FulfillmentStatus.CANCELLED
                order.cancelled_at = order.cancelled_at or current
                order.cancellation_reason = (
                    order.cancellation_reason or "金流確認交易已退款"
                )
                if order.fulfillment is not None:
                    order.fulfillment.status = FulfillmentState.CANCELLED
                    shipment = order.fulfillment.__dict__.get("shipment")
                    if shipment is not None and shipment.status in {
                        ShipmentStatus.DRAFT,
                        ShipmentStatus.SELECTION_PENDING,
                        ShipmentStatus.READY_TO_CREATE,
                    }:
                        shipment.status = ShipmentStatus.CANCELLED
            if remaining_payment_status is None:
                enqueue_invoice_adjustment_after_refund(
                    self.session,
                    order,
                    refund,
                )
                await reverse_order_purchase_points(
                    self.session,
                    order,
                    refund.id,
                )
                if fulfillment_irreversible:
                    await self._publish_refunded_fulfillment_alert(order, refund)
        elif attempt.membership_charge is not None:
            charge = attempt.membership_charge
            if remaining_payment_status is None:
                charge.status = MembershipChargeStatus.REFUNDED
                charge.refunded_at = current
                if charge.membership.status in {
                    MembershipStatus.TRAINEE,
                    MembershipStatus.ACTIVE,
                }:
                    charge.membership.status = MembershipStatus.SUSPENDED
                    charge.membership.status_reason = "入社款項已退款，待人工確認"
            elif remaining_payment_status == PaymentStatus.PAID:
                charge.status = MembershipChargeStatus.PAID
            else:
                charge.status = MembershipChargeStatus.REFUND_PENDING

        owner = (
            attempt.order.user
            if attempt.order is not None
            else await self.session.get(User, attempt.membership_charge.user_id)
        )
        if owner is not None:
            service = NotificationService(
                SQLAlchemyNotificationRepository(self.session)
            )
            await service.publish(
                NotificationCommand(
                    user_id=owner.id,
                    event_type="refund_completed",
                    title="退款完成",
                    body=f"NT${refund.amount} 已由金流確認退款完成。",
                    data={
                        "order_id": attempt.order_id,
                        "membership_charge_id": attempt.membership_charge_id,
                    },
                    email=owner.email,
                    dedupe_key=f"refund:{refund.id}",
                )
            )

    async def _reload_locked_attempt_reservations(
        self,
        attempt: PaymentAttempt,
    ) -> None:
        reservations = list(
            await self.session.scalars(
                select(InventoryReservation)
                .where(InventoryReservation.payment_attempt_id == attempt.id)
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        )
        set_committed_value(attempt, "reservations", reservations)

    async def _publish_refunded_fulfillment_alert(
        self,
        order: Order,
        refund: Refund,
    ) -> None:
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
                    event_type="refund_fulfillment_intervention",
                    title="已退款訂單仍在履約中",
                    body=(
                        f"訂單 {order.order_number} 已由金流退款，"
                        "但物流已建立或商品已交付；請立即人工攔截或追蹤退回。"
                    ),
                    data={"order_id": order.id, "refund_id": refund.id},
                    email=admin.email,
                    dedupe_key=(
                        f"refund-fulfillment:{refund.id}:{admin.id}"
                    ),
                )
            )

    async def _release_refunded_attempt_resources(
        self,
        attempt: PaymentAttempt,
        current: datetime,
        *,
        restore_consumed: bool = True,
    ) -> None:
        await release_attempt_reservations(self.session, attempt)
        if not restore_consumed:
            return
        order = attempt.order
        if order is None:
            return
        group_release_quantity = 0
        for reservation in attempt.reservations:
            if reservation.status != ReservationStatus.CONSUMED:
                continue
            if (
                order.order_kind == OrderKind.GROUP
                and reservation.group_campaign_id == order.group_campaign_id
            ):
                group_release_quantity += reservation.quantity
            elif reservation.source_product_id is not None:
                product = await self.session.scalar(
                    select(Product)
                    .where(Product.id == reservation.source_product_id)
                    .with_for_update()
                )
                if product is not None:
                    product.stock_quantity += reservation.quantity
            elif reservation.source_meal_offering_id is not None:
                offering = await self.session.scalar(
                    select(MealEventOffering)
                    .where(
                        MealEventOffering.id
                        == reservation.source_meal_offering_id
                    )
                    .with_for_update()
                )
                if offering is not None:
                    offering.paid_quantity = max(
                        0,
                        offering.paid_quantity - reservation.quantity,
                    )
            reservation.status = ReservationStatus.RELEASED
            reservation.released_at = current
        if group_release_quantity and order.group_campaign_id is not None:
            campaign = await self.session.scalar(
                select(GroupCampaign)
                .where(GroupCampaign.id == order.group_campaign_id)
                .execution_options(populate_existing=True)
                .with_for_update()
            )
            if campaign is None:
                raise PaymentApplicationError("找不到團購資料")
            remove_paid_quantity(campaign, group_release_quantity, current)

    async def _apply_provider_refund_failure(
        self,
        attempt: PaymentAttempt,
        payload: Mapping[str, str],
    ) -> None:
        attempt.provider_response = dict(payload)
        subject_filter = (
            Refund.order_id == attempt.order_id
            if attempt.order_id is not None
            else Refund.membership_charge_id == attempt.membership_charge_id
        )
        refund = await self.session.scalar(
            select(Refund)
            .where(Refund.payment_attempt_id == attempt.id)
            .order_by(Refund.created_at.desc())
            .with_for_update()
        )
        if refund is None:
            refund = await self.session.scalar(
                select(Refund)
                .where(
                    Refund.payment_attempt_id.is_(None),
                    Refund.status.in_(
                        {RefundStatus.PENDING, RefundStatus.FAILED}
                    ),
                    subject_filter,
                )
                .order_by(Refund.created_at.desc())
                .with_for_update()
            )
        if refund is not None:
            refund.payment_attempt = attempt
            refund.provider = attempt.provider
            refund.provider_response = dict(payload)
            refund.status = RefundStatus.FAILED
        if refund is not None:
            service = NotificationService(
                SQLAlchemyNotificationRepository(self.session)
            )
            owner = (
                attempt.order.user
                if attempt.order is not None
                else await self.session.get(
                    User,
                    attempt.membership_charge.user_id,
                )
            )
            if owner is not None:
                await service.publish(
                    NotificationCommand(
                        user_id=owner.id,
                        event_type="refund_failed",
                        title="退款尚未完成",
                        body="金流回報退款失敗，工作人員將協助確認。",
                        data={"refund_id": refund.id},
                        email=owner.email,
                        dedupe_key=f"refund-failed:{refund.id}",
                    )
                )
            admins = list(
                await self.session.scalars(
                    select(User).where(User.user_role == UserRole.ADMIN)
                )
            )
            for admin in admins:
                await service.publish(
                    NotificationCommand(
                        user_id=admin.id,
                        event_type="refund_manual_review_required",
                        title="退款失敗，需要人工介入",
                        body=(
                            f"退款 {refund.id} 已由金流明確回報失敗；"
                            "請查核原付款與退款狀態後人工處理。"
                        ),
                        data={
                            "refund_id": refund.id,
                            "order_id": refund.order_id,
                            "membership_charge_id": (
                                refund.membership_charge_id
                            ),
                        },
                        email=admin.email,
                        dedupe_key=(
                            f"refund-manual-review:{refund.id}:{admin.id}"
                        ),
                    )
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
        paid_at: Optional[datetime] = None,
    ) -> None:
        current = datetime.now(timezone.utc)
        payment_time = paid_at or current
        attempt.status = PaymentStatus.LATE_PAID_REFUND_REQUIRED
        attempt.paid_at = payment_time
        order.payment_status = PaymentStatus.LATE_PAID_REFUND_REQUIRED
        order.paid_at = payment_time
        refund = Refund(
            order=order,
            payment_attempt=attempt,
            provider=attempt.provider,
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
                .execution_options(populate_existing=True)
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
    from zoneinfo import ZoneInfo

    for date_format in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            local = datetime.strptime(raw, date_format).replace(
                tzinfo=ZoneInfo("Asia/Taipei")
            )
            return local.astimezone(timezone.utc)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Taipei"))
    return parsed.astimezone(timezone.utc)
