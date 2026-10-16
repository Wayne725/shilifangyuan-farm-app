from datetime import datetime, timezone
from fastapi import HTTPException
from sqlalchemy import func, select
from .domain import aware
from .models import Order, Promotion, SystemSetting

async def lock_promotions(session):
    # Keep the same rule lock order for checkouts and admin edits; caps include pending orders.
    await session.scalar(select(SystemSetting).where(SystemSetting.key == "promotion_operations").with_for_update())
    await session.scalars(select(Promotion).order_by(Promotion.id).with_for_update())

async def price_promotion(session, total, code, user_id, now):
    query = select(Promotion).where(Promotion.enabled.is_(True)).order_by(Promotion.id)
    rules = list(await session.scalars(query))
    code = code.strip().upper()
    candidates = []
    for rule in rules:
        if (rule.code != code if code else rule.code is not None):
            continue
        if not aware(rule.starts_at) <= now < aware(rule.ends_at) or total < rule.minimum_amount:
            continue
        used = await session.scalar(select(func.count(Order.id)).where(Order.promotion_id == rule.id))
        if rule.max_uses and used >= rule.max_uses:
            continue
        if user_id and rule.per_user_limit:
            own = await session.scalar(select(func.count(Order.id)).where(Order.promotion_id == rule.id, Order.user_id == user_id))
            if own >= rule.per_user_limit:
                continue
        discount = min(total - 1, rule.discount_value if rule.discount_kind == 'fixed' else total * rule.discount_value // 100)
        if discount > 0:
            candidates.append((discount, rule))
    if not candidates:
        if code:
            raise HTTPException(status_code=409, detail="優惠碼不適用、已到期或已達使用上限")
        return None, 0
    discount, rule = max(candidates, key=lambda pair: (pair[0], pair[1].id))
    return {'id': rule.id, 'name': rule.name, 'code': rule.code, 'discount': discount, 'original_amount': total}, discount
