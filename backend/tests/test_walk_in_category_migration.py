from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, create_engine, text
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.models import User


def test_counter_category_backfill_preserves_financial_rows_and_guards_downgrade(tmp_path, monkeypatch):
    database = tmp_path / "counter-category.db"
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{database}"))
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    engine = create_engine(f"sqlite:///{database}")
    try:
        command.upgrade(config, "0050_meal_workday_policy")
        with engine.begin() as connection:
            connection.execute(User.__table__.insert().values(id="operator", email="operator@example.com", display_name="合成收款員", password_hash="test"))
            orders = Table("orders", MetaData(), autoload_with=connection)
            connection.execute(orders.insert().values(id="counter-sale", order_number="O2026110901", user_id=None,
                customer_kind="walk_in", customer_name_snapshot="現場客人", revision=1,
                order_kind="regular", sales_channel="regular", fulfillment_method="cooperative_pickup",
                membership_type_snapshot="nonmember", member_tax_exempt_snapshot=False, amount_total=105, tax_amount=5,
                contact_email="customer@example.com", invoice_buyer_type="personal", invoice_carrier_type="cloud",
                invoice_status="pending", payment_method="cash_on_pickup", payment_status="paid", fulfillment_status="picked_up",
                invoice_handling="on_payment", created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc)))
            connection.execute(text("INSERT INTO walk_in_sales (order_id, client_request_id, request_fingerprint, operator_id, created_at) VALUES ('counter-sale', 'request', 'fingerprint', 'operator', CURRENT_TIMESTAMP)"))
            before_order = connection.execute(text("SELECT * FROM orders")).mappings().one()
            before_sale = dict(connection.execute(text("SELECT * FROM walk_in_sales")).mappings().one())
        command.upgrade(config, "head")
        command.check(config)
        with engine.connect() as connection:
            assert dict(connection.execute(text("SELECT * FROM orders")).mappings().one()) == {**before_order, "meal_booking_type": "standard"}
            assert dict(connection.execute(text("SELECT * FROM walk_in_sales")).mappings().one()) == {**before_sale, "sale_category": None}
        command.downgrade(config, "0050_meal_workday_policy")
        with engine.connect() as connection:
            assert dict(connection.execute(text("SELECT * FROM walk_in_sales")).mappings().one()) == before_sale
        command.upgrade(config, "head")
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(text("UPDATE walk_in_sales SET sale_category='invalid'"))
        with engine.begin() as connection:
            connection.execute(text("UPDATE walk_in_sales SET sale_category='meal'"))
        with pytest.raises(RuntimeError, match="不可直接降版"):
            command.downgrade(config, "0050_meal_workday_policy")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT sale_category FROM walk_in_sales")) == "meal"
            assert connection.execute(text("SELECT * FROM orders")).mappings().one() == before_order
    finally:
        engine.dispose()
