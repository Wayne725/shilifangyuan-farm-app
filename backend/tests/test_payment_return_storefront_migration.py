from alembic import command
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from tests.test_group_review_migration import MigrationDatabase, seed_payment, seed_user


def test_storefront_migration_preserves_existing_payment_and_refund_rows(tmp_path, monkeypatch):
    previous = "0055_counter_member_pricing"
    current = "0056_payment_return_storefront"
    url = f"sqlite+aiosqlite:///{tmp_path / 'payment-return.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    database = MigrationDatabase(url)
    try:
        command.upgrade(database.config, previous)
        seed_user(database)
        seed_payment(database, "legacy", refund_purpose="order_cancellation")
        before_payment = database.rows("payment_attempts")
        before_refund = database.rows("refunds")

        command.upgrade(database.config, current)
        assert database.rows("payment_attempts") == [{**before_payment[0], "browser_return_storefront": None}]
        assert database.rows("refunds") == before_refund
        with pytest.raises(IntegrityError):
            database.run(lambda connection: connection.execute(text(
                "UPDATE payment_attempts SET browser_return_storefront = 'attacker.example'"
            )))
        command.downgrade(database.config, previous)
        assert database.rows("payment_attempts") == before_payment
        command.upgrade(database.config, current)

        database.run(lambda connection: connection.execute(text(
            "UPDATE payment_attempts SET browser_return_storefront = 'legacy'"
        )))
        with pytest.raises(RuntimeError, match="已有舊網址付款回傳紀錄"):
            command.downgrade(database.config, previous)
        assert database.rows("payment_attempts") == [{**before_payment[0], "browser_return_storefront": "legacy"}]
        assert database.rows("refunds") == before_refund
        command.upgrade(database.config, "head")
        command.check(database.config)
    finally:
        get_settings.cache_clear()
