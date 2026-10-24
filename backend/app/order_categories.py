from typing import Literal

from sqlalchemy import true

from .models import Order, SalesChannel


OrderCategory = Literal["all", "general", "meal"]


def order_category_condition(category: OrderCategory):
    if category == "general":
        return Order.sales_channel.in_([SalesChannel.REGULAR, SalesChannel.GROUP])
    if category == "meal":
        return Order.sales_channel == SalesChannel.MEAL_PREORDER
    return true()
