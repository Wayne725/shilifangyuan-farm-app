from __future__ import annotations

import zlib
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession


async def next_identity_number(
    session: AsyncSession,
    column: Any,
    prefix: str,
    now: Optional[datetime] = None,
    *,
    reserved_columns: Sequence[Any] = (),
) -> str:
    current = now or datetime.now(timezone.utc)
    year_prefix = f"{prefix}-{current.year}-"
    connection = await session.connection()
    if connection.dialect.name == "postgresql":
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": zlib.crc32(year_prefix.encode("utf-8"))},
        )
    values = []
    for number_column in (column, *reserved_columns):
        values.extend(await session.scalars(
            select(number_column).where(number_column.like(f"{year_prefix}%"))
        ))
    sequences = [
        int(value.removeprefix(year_prefix))
        for value in values
        if value and value.removeprefix(year_prefix).isdigit()
    ]
    return f"{year_prefix}{max(sequences, default=0) + 1:04d}"
