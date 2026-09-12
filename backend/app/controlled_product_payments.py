from __future__ import annotations

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .models import FulfillmentMethod, Order, OrderKind, Product, SalesChannel


MEALS_TEST_PRODUCT_AMOUNT = 10


def meals_test_product_enabled(settings: Settings) -> bool:
    return (
        settings.environment.strip().lower() == "production"
        and settings.sales_scope == "meals_only"
        and bool(settings.meals_test_product_id.strip())
        and bool(settings.meals_test_product_sku.strip())
        and settings.payment_provider == "raygate"
        and not settings.raygate_payment_stage
        and settings.raygate_payment_contract_verified
        and settings.invoice_provider == "fanyu"
        and not settings.fanyu_invoice_stage
        and settings.fanyu_invoice_signature_verified
    )


async def is_meals_test_product_order(
    session: AsyncSession,
    settings: Settings,
    order: Order,
    *,
    amount: int | None = None,
) -> bool:
    if not meals_test_product_enabled(settings):
        return False
    if (
        order.order_kind != OrderKind.REGULAR
        or order.sales_channel != SalesChannel.REGULAR
        or order.group_campaign_id is not None
        or order.meal_event_id is not None
        or order.fulfillment_method != FulfillmentMethod.COOPERATIVE_PICKUP
        or order.fulfillment is None
        or order.fulfillment.method != FulfillmentMethod.COOPERATIVE_PICKUP
        or len(order.items) != 1
        or order.amount_total != MEALS_TEST_PRODUCT_AMOUNT
        or (amount is not None and amount != MEALS_TEST_PRODUCT_AMOUNT)
    ):
        return False
    item = order.items[0]
    if (
        item.source_product_id != settings.meals_test_product_id.strip()
        or item.source_bundle_id is not None
        or item.source_meal_offering_id is not None
        or item.quantity != 1
        or item.unit_price != MEALS_TEST_PRODUCT_AMOUNT
        or item.subtotal != MEALS_TEST_PRODUCT_AMOUNT
    ):
        return False
    # Existing reservations may already own the last unit; stock is checked on reservation.
    return bool(await session.scalar(select(exists().where(
        Product.id == item.source_product_id,
        Product.sku == settings.meals_test_product_sku.strip(),
        Product.is_active.is_(True),
        Product.can_ship.is_(False),
        Product.member_price == MEALS_TEST_PRODUCT_AMOUNT,
        Product.nonmember_price == MEALS_TEST_PRODUCT_AMOUNT,
    ))))
