from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from inspect import isawaitable
import secrets
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Order, OrderNumberCounter


async def allocate_order_number(
    session: AsyncSession,
    prefix: Literal["M", "O"],
    now: datetime | None = None,
) -> str:
    current = now or datetime.now(timezone.utc)
    if prefix not in {"M", "O"} or current.tzinfo is None:
        raise ValueError("訂單編號需要有效類別及含時區的時間")
    business_date = current.astimezone(ZoneInfo("Asia/Taipei")).date()
    insert = {"postgresql": pg_insert, "sqlite": sqlite_insert}[
        session.get_bind().dialect.name
    ]
    # The counter and order commit together; an aborted order does not consume a number.
    statement = insert(OrderNumberCounter).values(
        prefix=prefix, business_date=business_date, last_value=1,
    ).on_conflict_do_update(
        index_elements=["prefix", "business_date"],
        set_={"last_value": OrderNumberCounter.last_value + 1},
    ).returning(OrderNumberCounter.last_value)
    sequence = await session.scalar(statement)
    return f"{prefix}{business_date:%Y%m%d}{sequence:02d}"


def make_order_number() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%y%m%d%H%M%S")
    return f"SLF{timestamp}{secrets.token_hex(2).upper()}"


def _is_unique_collision(error: IntegrityError, dialect: str, constraint: str, columns: str) -> bool:
    if dialect == "postgresql":
        cause = getattr(error.orig, "__cause__", None)
        return (getattr(error.orig, "sqlstate", None) == "23505"
                and getattr(cause, "constraint_name", None) == constraint)
    if dialect == "sqlite":
        return str(error.orig) == f"UNIQUE constraint failed: {columns}"
    return False


def _is_order_number_collision(error: IntegrityError, dialect: str) -> bool:
    return _is_unique_collision(error, dialect, "ix_orders_order_number", "orders.order_number")


def is_order_submission_collision(error: IntegrityError, dialect: str) -> bool:
    return _is_unique_collision(error, dialect, "uq_orders_user_client_request", "orders.user_id, orders.client_request_id")


async def insert_order_with_unique_number(
    session: AsyncSession, order: Order, number_factory: Callable[[], str | Awaitable[str]]
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
            number = number_factory()
            order.order_number = await number if isawaitable(number) else number
