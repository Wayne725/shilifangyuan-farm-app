from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, create_engine, inspect, text

from app.config import Settings
from app.models import User


def test_daily_number_migration_preserves_old_order_and_rejects_used_counter_downgrade(tmp_path, monkeypatch):
    database = tmp_path / "order-numbers.db"
    settings = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{database}")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    engine = create_engine(f"sqlite:///{database}")
    try:
        command.upgrade(config, "0042_order_reference_printing")
        with engine.begin() as connection:
            connection.execute(User.__table__.insert().values(id="buyer", email="buyer@example.test", display_name="合成買家", password_hash="test"))
            old_orders = Table("orders", MetaData(), autoload_with=connection)
            connection.execute(old_orders.insert().values(
                id="old-order", order_number="M261106050836911ED279CD05", user_id="buyer", revision=1,
                order_kind="regular", sales_channel="regular", fulfillment_method="cooperative_pickup",
                membership_type_snapshot="nonmember", member_tax_exempt_snapshot=False,
                amount_total=120, tax_amount=0, contact_email="buyer@example.test",
                invoice_buyer_type="personal", invoice_carrier_type="cloud", invoice_status="not_eligible",
                payment_method="online", payment_status="pending", fulfillment_status="pending_confirmation",
                created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
            ))
            old = dict(connection.execute(text("SELECT * FROM orders WHERE id='old-order'")).mappings().one())
        command.upgrade(config, "0043_daily_order_numbers")
        with engine.connect() as connection:
            assert dict(connection.execute(text("SELECT * FROM orders WHERE id='old-order'")).mappings().one()) == old
            assert connection.scalar(text("SELECT COUNT(*) FROM order_number_counters")) == 0
        command.downgrade(config, "0042_order_reference_printing")
        assert "order_number_counters" not in inspect(engine).get_table_names()
        command.upgrade(config, "0043_daily_order_numbers")
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO order_number_counters VALUES ('M', :day, 1)"), {"day": date(2026, 11, 6)})
        with pytest.raises(RuntimeError, match="已有每日訂單編號"):
            command.downgrade(config, "0042_order_reference_printing")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT last_value FROM order_number_counters")) == 1
            assert dict(connection.execute(text("SELECT * FROM orders WHERE id='old-order'")).mappings().one()) == old
    finally:
        engine.dispose()
