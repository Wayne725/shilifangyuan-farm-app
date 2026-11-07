from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, create_engine, inspect, text

from app.config import Settings
from app.models import User


@pytest.mark.parametrize("new_policy", ["pickup_day", "on_payment"])
def test_invoice_timing_migration_preserves_old_contract_and_refuses_lossy_downgrade(tmp_path, monkeypatch, new_policy):
    database = tmp_path / "invoice-timing.db"
    settings = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{database}")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    engine = create_engine(f"sqlite:///{database}")
    try:
        command.upgrade(config, "0043_daily_order_numbers")
        with engine.begin() as connection:
            connection.execute(User.__table__.insert().values(id="buyer", email="buyer@example.test", display_name="合成買家", password_hash="test"))
            old_orders = Table("orders", MetaData(), autoload_with=connection)
            for payment_method in ("cash_on_pickup", "online"):
                connection.execute(old_orders.insert().values(
                    id=payment_method, order_number=f"OLD-{payment_method}", user_id="buyer", revision=1,
                    order_kind="regular", sales_channel="regular", fulfillment_method="cooperative_pickup",
                    membership_type_snapshot="nonmember", member_tax_exempt_snapshot=False,
                    amount_total=120, tax_amount=0, contact_email="buyer@example.test",
                    invoice_buyer_type="personal", invoice_carrier_type="cloud",
                    invoice_status="external" if payment_method == "cash_on_pickup" else "issued",
                    payment_method=payment_method, payment_status="paid", fulfillment_status="pending_confirmation",
                    created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
                ))
            old = {row["id"]: dict(row) for row in connection.execute(text("SELECT * FROM orders")).mappings()}
        command.upgrade(config, "0044_invoice_timing")
        with engine.connect() as connection:
            rows = {row["id"]: dict(row) for row in connection.execute(text("SELECT * FROM orders")).mappings()}
            for order_id, row in rows.items():
                assert row.pop("invoice_handling") == ("external" if order_id == "cash_on_pickup" else "on_payment")
                assert row.pop("invoice_issue_after") is None
                assert row == old[order_id]
        command.downgrade(config, "0043_daily_order_numbers")
        assert "invoice_handling" not in {column["name"] for column in inspect(engine).get_columns("orders")}
        command.upgrade(config, "0044_invoice_timing")
        with engine.begin() as connection:
            connection.execute(text("UPDATE orders SET invoice_handling=:policy WHERE id='cash_on_pickup'"), {"policy": new_policy})
        with pytest.raises(RuntimeError, match="已有新開票安排"):
            command.downgrade(config, "0043_daily_order_numbers")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT invoice_handling FROM orders WHERE id='cash_on_pickup'")) == new_policy
            assert connection.scalar(text("SELECT invoice_status FROM orders WHERE id='cash_on_pickup'")) == "external"
    finally:
        engine.dispose()
