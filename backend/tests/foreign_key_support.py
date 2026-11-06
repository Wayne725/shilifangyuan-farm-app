from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.models import (
    GroupBundle, GroupBundleItem, GroupCampaign, Meal, MealEvent, MealEventOffering,
    Product, TargetType,
)


async def unrelated_catalog_sources(session, user_id):
    suffix = str(uuid4())
    now = datetime.now(timezone.utc)
    product = Product(slug=f"unrelated-product-{suffix}", name="其他合法商品", category="測試",
        unit="份", member_price=100, nonmember_price=100, stock_quantity=10)
    bundle = GroupBundle(name=f"其他合法組合-{suffix}", member_price=100, nonmember_price=100,
        items=[GroupBundleItem(product=product, quantity=1)])
    meal = Meal(slug=f"unrelated-meal-{suffix}", name="其他合法餐點", price=100)
    meal_event = MealEvent(title="其他合法場次", location="隔離取餐點", created_by_id=user_id,
        ordering_starts_at=now, ordering_ends_at=now + timedelta(hours=1),
        pickup_starts_at=now + timedelta(hours=2), pickup_ends_at=now + timedelta(hours=3))
    offering = MealEventOffering(event=meal_event, meal=meal, price=100, capacity=10)
    session.add_all([bundle, offering])
    await session.flush()
    campaign = GroupCampaign(title="其他合法共同購買", target_type=TargetType.PRODUCT,
        target_id=product.id, member_price=100, nonmember_price=100, min_paid_quantity=1,
        supply_cap=10, per_user_cap=5, deadline=now + timedelta(hours=1),
        estimated_pickup_start=now + timedelta(days=1), estimated_pickup_end=now + timedelta(days=2),
        created_by_id=user_id)
    session.add(campaign)
    await session.flush()
    return SimpleNamespace(product=product, bundle=bundle, campaign=campaign,
        meal_event=meal_event, offering=offering)
