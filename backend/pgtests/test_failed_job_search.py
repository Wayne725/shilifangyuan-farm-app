import pytest

from pgtests.test_transaction_concurrency import postgres_sessions
from tests.test_failed_job_search import exercise_failed_job_search


@pytest.mark.asyncio
async def test_failed_job_search_joins_json_order_references_on_postgres(postgres_sessions):
    async with postgres_sessions() as session:
        await exercise_failed_job_search(session)
