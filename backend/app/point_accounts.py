from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import PointAccount


async def point_account(session: AsyncSession, user_id: str) -> PointAccount:
    """Return the account under the caller's transaction, including its ledger write."""
    insert = postgres_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
    statement = insert(PointAccount).values(user_id=user_id)
    # A no-op update keeps the per-account row lock until the caller commits.
    statement = statement.on_conflict_do_update(
        index_elements=[PointAccount.user_id], set_={"user_id": statement.excluded.user_id},
    ).returning(PointAccount)
    account = await session.scalar(statement.execution_options(populate_existing=True))
    assert account is not None
    return account
