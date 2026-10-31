from collections.abc import Callable
from datetime import datetime, timezone
import secrets

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Order


def make_order_number() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%y%m%d%H%M%S")
    return f"SLF{timestamp}{secrets.token_hex(2).upper()}"


def _is_order_number_collision(error: IntegrityError, dialect: str) -> bool:
    if dialect == "postgresql":
        cause = getattr(error.orig, "__cause__", None)
        return (getattr(error.orig, "sqlstate", None) == "23505"
                and getattr(cause, "constraint_name", None) == "ix_orders_order_number")
    if dialect == "sqlite":
        return str(error.orig) == "UNIQUE constraint failed: orders.order_number"
    return False


async def insert_order_with_unique_number(
    session: AsyncSession, order: Order, number_factory: Callable[[], str]
) -> None:
    if session.get_bind().dialect.name == "sqlite":
        connection = await session.connection()
        raw_connection = await connection.get_raw_connection()
        # Legacy SQLite transaction control does not BEGIN for SELECT or SAVEPOINT.
        if not raw_connection.driver_connection.in_transaction:
            await connection.exec_driver_sql("BEGIN")
    for attempt in range(3):
        try:
            # Add inside the savepoint: begin_nested flushes existing pending rows first.
            # Retrying this insert must retain the caller's product/campaign locks.
            async with session.begin_nested():
                session.add(order)
                await session.flush()
            return
        except IntegrityError as exc:
            if not _is_order_number_collision(exc, session.get_bind().dialect.name):
                raise
            if attempt == 2:
                raise HTTPException(status_code=409, detail="訂單編號暫時衝突，請重新送出訂單") from exc
            order.order_number = number_factory()
