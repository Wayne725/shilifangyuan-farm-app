from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from ..auth import require_admin
from ..database import get_session
from ..promotions import lock_promotions
from ..models import AdminAudit, Order, Promotion, User

promotions_router = APIRouter(prefix='/v1/admin/promotions', tags=['promotions'])
class PromotionInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    code: str | None = Field(default=None, max_length=32, pattern=r'^[A-Z0-9_-]+$')
    enabled: bool = False
    minimum_amount: int = Field(ge=1, le=1000000)
    discount_kind: Literal['fixed', 'percent']
    discount_value: int = Field(ge=1, le=1000000)
    starts_at: datetime
    ends_at: datetime
    max_uses: int | None = Field(default=None, ge=1, le=1000000)
    per_user_limit: int | None = Field(default=None, ge=1, le=1000000)
    @field_validator('name')
    @classmethod
    def nonblank(cls, v):
        if not v.strip(): raise ValueError('請填寫活動名稱')
        return v.strip()
    @model_validator(mode='after')
    def validate_rule(self):
        if not self.starts_at.tzinfo or not self.ends_at.tzinfo or self.ends_at <= self.starts_at:
            raise ValueError('請設定有效且包含時區的活動起迄時間')
        if self.discount_kind == 'percent' and self.discount_value > 99:
            raise ValueError('折扣百分比須介於 1 到 99')
        if self.discount_kind == 'fixed' and self.discount_value >= self.minimum_amount:
            raise ValueError('折抵金額須小於消費門檻')
        return self

def output(row, uses=0):
    return {key: getattr(row, key) for key in ('id','name','code','enabled','minimum_amount','discount_kind','discount_value','starts_at','ends_at','max_uses','per_user_limit')} | {'uses': uses}

@promotions_router.get('')
async def list_rules(_admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    result = []
    for row in await session.scalars(select(Promotion).order_by(Promotion.created_at.desc(), Promotion.id.desc())):
        uses = await session.scalar(select(func.count(Order.id)).where(Order.promotion_id == row.id))
        result.append(output(row, uses))
    return result

@promotions_router.post('', status_code=201)
async def create_rule(body: PromotionInput, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    await lock_promotions(session)
    row = Promotion(**body.model_dump(), created_by_id=admin.id)
    session.add(row)
    try: await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, '優惠碼已存在') from exc
    session.add(AdminAudit(actor_id=admin.id, action='promotion.created', aggregate_type='promotion', aggregate_id=row.id, data=body.model_dump(mode='json')))
    await session.commit()
    return output(row)

@promotions_router.put('/{rule_id}')
async def update_rule(rule_id: str, body: PromotionInput, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    await lock_promotions(session)
    row = await session.scalar(select(Promotion).where(Promotion.id == rule_id).with_for_update())
    if not row: raise HTTPException(404, '找不到促銷活動')
    before = output(row)
    for key, value in body.model_dump().items(): setattr(row, key, value)
    session.add(AdminAudit(actor_id=admin.id, action='promotion.updated', aggregate_type='promotion', aggregate_id=row.id, data={'previous': {**before, 'starts_at': before['starts_at'].isoformat(), 'ends_at': before['ends_at'].isoformat()}, 'current': body.model_dump(mode='json')}))
    try: await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, '優惠碼已存在') from exc
    return output(row)
