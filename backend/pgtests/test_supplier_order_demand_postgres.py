import os

import pytest

from pgtests.test_transaction_concurrency import postgres_sessions
from tests.test_supplier_order_demand import (
    test_confirmed_group_direct_items_review_unknown_and_order_refunds,
    test_regular_order_supplier_changes_amendment_and_missing_product,
)


pytestmark = pytest.mark.skipif(not os.environ.get("TEST_POSTGRES_URL"), reason="Requires disposable PostgreSQL")


@pytest.fixture
async def database_session(postgres_sessions):
    async with postgres_sessions() as session:
        yield session
