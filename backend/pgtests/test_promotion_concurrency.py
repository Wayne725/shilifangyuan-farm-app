import asyncio
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import func, select
from app.models import Meal, MealEvent, MealEventOffering, MealEventStatus, Order, Promotion, SystemSetting, User, UserRole
from app.routers import meals
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.support import api_test_context, auth_headers, make_test_settings, tomorrow_meal_timeline

@pytest.mark.asyncio
async def test_last_coupon_is_claimed_once_across_different_events(postgres_sessions, monkeypatch):
    now = datetime(2030,1,1,4,tzinfo=timezone.utc)
    class Fixed(datetime):
        @classmethod
        def now(cls, tz=None): return now.astimezone(tz) if tz else now.replace(tzinfo=None)
    monkeypatch.setattr(meals, 'datetime', Fixed)
    async with postgres_sessions() as session:
        admin=User(email='promo-admin@example.com', display_name='管理員', password_hash='x', user_role=UserRole.ADMIN)
        buyers=[User(email=f'promo-{n}@example.com',display_name=f'買家{n}',password_hash='x') for n in range(2)]
        meal=Meal(slug='promo',name='優惠便當',price=100)
        session.add_all([admin,meal,*buyers,SystemSetting(key='promotion_operations',value={})]);await session.flush()
        events=[]
        for n in range(2):
            event=MealEvent(title=f'場次{n}',location='隔離地點',created_by_id=admin.id,status=MealEventStatus.PUBLISHED,**tomorrow_meal_timeline(now),offerings=[MealEventOffering(meal_id=meal.id,price=100,capacity=10)])
            session.add(event);events.append(event)
        promo=Promotion(name='最後一張',code='LAST20',enabled=True,minimum_amount=100,discount_kind='fixed',discount_value=20,starts_at=now-timedelta(days=1),ends_at=now+timedelta(days=1),max_uses=1,created_by_id=admin.id)
        session.add(promo);await session.commit()
        inputs=[(event.id,event.offerings[0].id,buyer) for event,buyer in zip(events,buyers)]
    async def create(event_id,offering_id,buyer):
        async with postgres_sessions() as session:
            async with api_test_context(session,[meals.meals_router],settings=make_test_settings()) as client:
                return await client.post(f'/v1/meal-events/{event_id}/orders',headers=auth_headers(buyer),json={"contact_phone": "0912345678", 'contact_email':buyer.email,'coupon_code':'LAST20','items':[{'offering_id':offering_id,'quantity':1}]})
    results=await asyncio.wait_for(asyncio.gather(*(create(*values) for values in inputs)),timeout=15)
    assert sorted(response.status_code for response in results)==[201,409],[response.text for response in results]
    async with postgres_sessions() as session:
        assert await session.scalar(select(func.count(Order.id)).where(Order.promotion_id==promo.id))==1
        order=await session.scalar(select(Order))
        assert order.amount_total==80 and order.promotion_snapshot['discount']==20
