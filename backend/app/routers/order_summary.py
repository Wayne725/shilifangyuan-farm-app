from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_admin
from ..database import get_session
from ..models import User
from ..order_summary import OrderSummaryFilters, read_order_summary


order_summary_router = APIRouter(prefix="/v1/admin", tags=["order-summary"])


@order_summary_router.get("/order-summary")
async def get_order_summary(
    filters: Annotated[OrderSummaryFilters, Query()],
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    return await read_order_summary(session, filters)
