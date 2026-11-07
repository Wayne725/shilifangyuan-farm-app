"""Compare raw omissions and ORM defaults in isolated PostgreSQL schemas."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import asyncpg
from alembic import command
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from app import models
from app.database import Base
from tests.test_metadata_default_parity import CASES, CIPHER, OMIT, ROOT, build_row, new_user, source_hashes, value_of
from tests.test_open_proposal_migration import proposal_migration_db


pytestmark = pytest.mark.skipif(not os.environ.get("TEST_POSTGRES_URL"), reason="Requires the isolated local PostgreSQL test database")
STATE = {"migration_target": "0049_merge_commerce_remediation", "cases": [], "owned_schemas": [], "owned_databases": []}


@pytest.fixture(scope="module", autouse=True)
def postgres_safety_and_evidence():
    url = make_url(os.environ["TEST_POSTGRES_URL"])
    assert url.drivername == "postgresql+asyncpg"
    assert url.host in {"localhost", "127.0.0.1"} and url.database == "shilifangyuan_test"
    assert url.port in {5432, 55441} and not url.query
    before = source_hashes()
    yield
    assert source_hashes() == before
    async def verify_cleanup():
        connection = await asyncpg.connect(url.set(drivername="postgresql").render_as_string(hide_password=False))
        try:
            for name in STATE["owned_schemas"]:
                assert await connection.fetchval("SELECT count(*) FROM pg_namespace WHERE nspname=$1", name) == 0
            for name in STATE["owned_databases"]:
                assert await connection.fetchval("SELECT count(*) FROM pg_database WHERE datname=$1", name) == 0
        finally:
            await connection.close()
    asyncio.run(verify_cleanup())
    STATE["owned_resources_removed"] = True
    STATE["source_hashes"] = before
    if os.environ.get("DEFAULT_PARITY_PG_EVIDENCE"):
        destination = Path(os.environ["DEFAULT_PARITY_PG_EVIDENCE"]).resolve()
        assert destination.is_relative_to((ROOT / "outputs").resolve())
        destination.write_text(json.dumps(STATE, ensure_ascii=False, indent=2) + "\n")


@pytest.fixture
async def default_postgres_sessions():
    url = make_url(os.environ["TEST_POSTGRES_URL"])
    schema = f"default_parity_{uuid4().hex}"
    STATE["owned_schemas"].append(schema)
    admin_engine = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    try:
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()


def observe_postgres(session, owner, row_id, column):
    model = getattr(models, owner)
    raw, native_type = session.execute(text(
        f'SELECT "{column}", pg_typeof("{column}")::text FROM "{model.__table__.name}" WHERE id=:id'
    ), {"id": row_id}).one()
    session.expire_all()
    return {"raw_value": raw, "postgres_type": native_type, "orm_value": value_of(getattr(session.get(model, row_id), column))}


def clone_with_raw_sql(session, owner, column, supplied=OMIT):
    model = getattr(models, owner)
    original = build_row(session, owner, column)
    clone_id = str(uuid4())
    replacements = {"id": clone_id}
    if owner == "Membership":
        replacements["user_id"] = new_user(session).id
    if owner == "MemberRosterEntry":
        replacements["member_number"] = "DEFAULT-" + uuid4().hex[:20]
        aad = f"member-roster:{clone_id}"
        replacements.update(
            legal_name_encrypted=CIPHER.encrypt_text("合成姓名", associated_data=aad),
            email_encrypted=CIPHER.encrypt_text(f"default-{clone_id}@example.test", associated_data=aad),
            phone_encrypted=CIPHER.encrypt_text("0900000000", associated_data=aad),
        )
    if owner == "Order":
        replacements.update(order_number="DF-" + uuid4().hex[:24], client_request_id=str(uuid4()))
    if owner == "MemberProposal":
        replacements["client_request_id"] = str(uuid4())
    if supplied is not OMIT:
        replacements[column] = supplied
    columns = [item.name for item in model.__table__.columns if item.name != column or supplied is not OMIT]
    names = ",".join(f'"{name}"' for name in columns)
    expressions = ",".join(":v_" + name if name in replacements else f'"{name}"' for name in columns)
    parameters = {"v_" + name: value for name, value in replacements.items()}
    parameters["original_id"] = original.id
    session.execute(text(f'INSERT INTO "{model.__table__.name}" ({names}) SELECT {expressions} FROM "{model.__table__.name}" WHERE id=:original_id'), parameters)
    return observe_postgres(session, owner, clone_id, column)


def verify_value(result, expected):
    assert result["raw_value"] == expected and result["orm_value"] == expected, result
    if isinstance(expected, bool):
        assert result["raw_value"] is expected and result["orm_value"] is expected, result
        assert result["postgres_type"] == "boolean", result
    elif isinstance(expected, int):
        assert result["postgres_type"] == "integer", result


def exercise_contract(session, path):
    cases = [*CASES, ("GroupCampaign", "legacy_confirmed_intake", False, True)]
    for owner, column, expected, explicit in cases:
        for mode, supplied, target in [("raw_omission", OMIT, expected), ("orm_omission", OMIT, expected), ("orm_explicit", explicit, explicit)]:
            if mode.startswith("raw"):
                result = clone_with_raw_sql(session, owner, column, supplied)
            else:
                row = build_row(session, owner, column, supplied)
                result = observe_postgres(session, owner, row.id, column)
            STATE["cases"].append({"schema": path, "model": owner, "column": column, "mode": mode, "expected": target, **result})
            verify_value(result, target)
    result = clone_with_raw_sql(session, "GroupCampaign", "legacy_confirmed_intake", True)
    STATE["cases"].append({"schema": path, "model": "GroupCampaign", "column": "legacy_confirmed_intake", "mode": "raw_true", "expected": True, **result})
    verify_value(result, True)


@pytest.mark.asyncio
async def test_metadata_raw_omissions_and_orm_controls(default_postgres_sessions):
    async with default_postgres_sessions() as session:
        await session.run_sync(lambda sync_session: exercise_contract(sync_session, "metadata"))
        await session.rollback()


@pytest.mark.parametrize("proposal_migration_db", ["postgresql"], indirect=True)
def test_alembic_merged_head_raw_omissions_and_orm_controls(proposal_migration_db):
    database = proposal_migration_db
    STATE["owned_databases"].append(make_url(database.url).database)
    command.upgrade(database.config, STATE["migration_target"])
    command.check(database.config)
    def verify(connection):
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == STATE["migration_target"]
        with Session(bind=connection, expire_on_commit=False) as session:
            exercise_contract(session, "alembic")
    database.run(verify)
