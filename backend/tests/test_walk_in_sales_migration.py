from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, create_engine, inspect, text

from app.config import Settings
from app.models import Membership, MembershipStatus, User


def test_walk_in_migration_preserves_account_rows_and_refuses_unsafe_downgrade(tmp_path, monkeypatch):
    database = tmp_path / "walk-in-migration.db"
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{database}"))
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    engine = create_engine(f"sqlite:///{database}")
    try:
        command.upgrade(config, "0044_invoice_timing")
        with engine.begin() as connection:
            connection.execute(User.__table__.insert().values(id="operator", email="operator@example.com", display_name="原使用者", password_hash="test"))
            orders = Table("orders", MetaData(), autoload_with=connection)
            connection.execute(orders.insert().values(
                id="old", order_number="OLD-ORDER", user_id="operator", revision=1,
                order_kind="regular", sales_channel="regular", fulfillment_method="cooperative_pickup",
                membership_type_snapshot="nonmember", member_tax_exempt_snapshot=False,
                amount_total=105, tax_amount=5, contact_email="operator@example.com",
                invoice_buyer_type="personal", invoice_carrier_type="cloud", invoice_status="not_eligible",
                payment_method="online", payment_status="pending", fulfillment_status="pending_confirmation",
                invoice_handling="on_payment", created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc)))
            before = dict(connection.execute(text("SELECT * FROM orders WHERE id='old'")).mappings().one())
        command.upgrade(config, "0045_walk_in_sales")
        with engine.connect() as connection:
            after = dict(connection.execute(text("SELECT * FROM orders WHERE id='old'")).mappings().one())
            assert {name: after[name] for name in before} == before
            assert after["customer_kind"] == "account"
        command.downgrade(config, "0044_invoice_timing")
        assert "walk_in_sales" not in inspect(engine).get_table_names()
        command.upgrade(config, "0045_walk_in_sales")
        with engine.begin() as connection:
            connection.execute(text("UPDATE orders SET user_id=NULL, customer_kind='walk_in', payment_method='cash_on_pickup' WHERE id='old'"))
            connection.execute(text("INSERT INTO walk_in_sales (order_id, client_request_id, request_fingerprint, operator_id, created_at) VALUES ('old', 'request', 'fingerprint', 'operator', CURRENT_TIMESTAMP)"))
        with pytest.raises(RuntimeError, match="已有現場交易"):
            command.downgrade(config, "0044_invoice_timing")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT COUNT(*) FROM walk_in_sales")) == 1
            assert connection.scalar(text("SELECT customer_kind FROM orders WHERE id='old'")) == "walk_in"
    finally:
        engine.dispose()


def test_counter_pricing_migration_preserves_older_orders_and_protects_verified_sales(tmp_path, monkeypatch):
    database = tmp_path / "counter-pricing-migration.db"
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{database}"))
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    engine = create_engine(f"sqlite:///{database}")
    previous = "0054_catalog_visibility"
    current = "0055_counter_member_pricing"
    try:
        command.upgrade(config, previous)
        with engine.begin() as connection:
            connection.execute(User.__table__.insert().values(id="member", email="member@example.com", display_name="原社員", password_hash="test"))
            connection.execute(Membership.__table__.insert().values(id="membership", user_id="member", status=MembershipStatus.ACTIVE))
            orders = Table("orders", MetaData(), autoload_with=connection)
            base_order = dict(
                order_kind="regular", sales_channel="regular", fulfillment_method="cooperative_pickup",
                membership_type_snapshot="nonmember", member_tax_exempt_snapshot=False,
                amount_total=105, tax_amount=5, contact_email="member@example.com",
                invoice_buyer_type="personal", invoice_carrier_type="cloud", invoice_status="not_eligible",
                payment_status="paid", fulfillment_status="picked_up", invoice_handling="external",
                created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
            )
            connection.execute(orders.insert().values(
                **base_order, id="online", order_number="OLD-ONLINE", user_id="member",
                customer_kind="account", payment_method="online",
            ))
            connection.execute(orders.insert().values(
                **base_order, id="counter", order_number="OLD-COUNTER", user_id=None,
                customer_kind="walk_in", payment_method="cash_on_pickup",
            ))
            before = connection.execute(text("SELECT * FROM orders ORDER BY id")).mappings().all()
        command.upgrade(config, current)
        with engine.connect() as connection:
            upgraded = connection.execute(text("SELECT * FROM orders ORDER BY id")).mappings().all()
            assert [dict(row) for row in upgraded] == [{**row, "verified_counter_membership_id": None} for row in before]
        command.downgrade(config, previous)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT * FROM orders ORDER BY id")).mappings().all() == before
            assert "walk_in_request_guards" not in inspect(connection).get_table_names()
        command.upgrade(config, current)
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO walk_in_request_guards (request_id, retired) VALUES ('retired', true)"))
        with pytest.raises(RuntimeError, match="已有作廢的現場操作編號"):
            command.downgrade(config, previous)
        with engine.begin() as connection:
            connection.execute(text(
                "UPDATE orders SET verified_counter_membership_id='membership', membership_type_snapshot='member', "
                "member_tax_exempt_snapshot=true WHERE id='counter'"
            ))
        with pytest.raises(RuntimeError, match="已有社員現場交易"):
            command.downgrade(config, previous)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == current
            assert connection.scalar(text("SELECT verified_counter_membership_id FROM orders WHERE id='counter'")) == "membership"
    finally:
        engine.dispose()
