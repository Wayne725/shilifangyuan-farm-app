from __future__ import annotations

from typing import Optional

from fastapi import HTTPException

from .config import Settings
from .controlled_product_payments import meals_test_product_enabled, meals_test_product_pairs
from .models import FulfillmentMethod, SalesChannel


MEALS_ONLY_MESSAGE = "目前僅開放便當訂購；其他新交易暫未開放"
PICKUP_ONLY_MESSAGE = "目前開放便當與商品自取；配送及其他新交易暫未開放"
DEMO_MEAL_EVENT_IDS = frozenset({
    "meal-event-preorder-demo",
    "meal-event-dinner-demo",
    "meal-event-pickup-demo",
})


class SalesScopeError(ValueError):
    pass


def ensure_sales_scope_allows(
    settings: Settings,
    *,
    sales_channel: Optional[SalesChannel] = None,
    fulfillment_method: Optional[FulfillmentMethod] = None,
    meal_event_id: Optional[str] = None,
) -> None:
    meal_order = (
        sales_channel == SalesChannel.MEAL_PREORDER
        and fulfillment_method == FulfillmentMethod.EVENT_PICKUP
        and bool(meal_event_id)
        and meal_event_id not in DEMO_MEAL_EVENT_IDS
    )
    if settings.sales_scope == "pickup":
        catalog_pickup = (
            sales_channel in {SalesChannel.REGULAR, SalesChannel.GROUP}
            and fulfillment_method == FulfillmentMethod.COOPERATIVE_PICKUP
            and not meal_event_id
        )
        if not (meal_order or catalog_pickup):
            raise SalesScopeError(PICKUP_ONLY_MESSAGE)
    elif settings.sales_scope == "meals_only" and not meal_order:
        raise SalesScopeError(MEALS_ONLY_MESSAGE)


def sales_capabilities(settings: Settings) -> dict[str, str | bool | list[str]]:
    return {
        "sales_scope": settings.sales_scope,
        "regular_orders_enabled": settings.sales_scope in {"all", "pickup"},
        "group_orders_enabled": settings.sales_scope in {"all", "pickup"},
        "shipping_enabled": settings.sales_scope == "all",
        "limited_product_ids": list(meals_test_product_pairs(settings)) if meals_test_product_enabled(settings) else [],
    }


def require_sales_scope_allows(
    settings: Settings,
    *,
    sales_channel: Optional[SalesChannel] = None,
    fulfillment_method: Optional[FulfillmentMethod] = None,
    meal_event_id: Optional[str] = None,
) -> None:
    try:
        ensure_sales_scope_allows(
            settings,
            sales_channel=sales_channel,
            fulfillment_method=fulfillment_method,
            meal_event_id=meal_event_id,
        )
    except SalesScopeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
