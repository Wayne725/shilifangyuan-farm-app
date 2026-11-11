from typing import Literal

from sqlalchemy import case, or_, true

from .models import Order, SalesChannel, WalkInSale


OrderCategory = Literal["all", "general", "meal"]
FinancialOrderCategory = Literal["all", "general", "meal", "walk_in_unclassified"]

FINANCIAL_ORDER_CATEGORY_LABELS = {
    "general": "一般訂單",
    "meal": "便當訂單",
    "walk_in_meal": "現場便當",
    "walk_in_product": "現場一般商品",
    "walk_in_unclassified": "現場未分類",
}
FINANCIAL_ORDER_CATEGORY_GROUPS = {
    "general": ("general", "walk_in_product"),
    "meal": ("meal", "walk_in_meal"),
    "walk_in_unclassified": ("walk_in_unclassified",),
}


def order_category_condition(category: OrderCategory):
    if category == "general":
        return Order.sales_channel.in_([SalesChannel.REGULAR, SalesChannel.GROUP])
    if category == "meal":
        return Order.sales_channel == SalesChannel.MEAL_PREORDER
    return true()


def financial_order_category():
    """Project report categories on queries that outer join WalkInSale by order ID."""
    return case(
        (WalkInSale.sale_category == "meal", "walk_in_meal"),
        (WalkInSale.sale_category == "product", "walk_in_product"),
        (or_(WalkInSale.order_id.is_not(None), Order.customer_kind == "walk_in"), "walk_in_unclassified"),
        (Order.sales_channel == SalesChannel.MEAL_PREORDER, "meal"),
        else_="general",
    )


def financial_order_category_condition(category: FinancialOrderCategory):
    if category == "all":
        return true()
    return financial_order_category().in_(FINANCIAL_ORDER_CATEGORY_GROUPS[category])
