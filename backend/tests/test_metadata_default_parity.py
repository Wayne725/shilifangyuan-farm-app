"""Synthetic SQLite metadata/Alembic default contract; no app/network/provider work."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app import config


def isolated_settings(url='sqlite+aiosqlite:///:memory:'):
    return config.Settings(_env_file=None, environment='test', database_url=url,
        printer_enabled=False, printer_dry_run=True, printer_worker_enabled=False)


from app.database import Base
from app import models
from app.integrations.pii_crypto import VersionedPIICipher
from app.schemas import MealScheduleInput


NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)
OMIT = object()
CIPHER = VersionedPIICipher({'v1': b'S' * 32}, 'v1')
CASES = [
    ('MealEvent', 'booking_policy', 'legacy', 'workday'),
    ('MealScheduleTemplate', 'cutoff_days_before', 0, 2),
    ('MealScheduleTemplate', 'advance_days', 7, 14),
    ('MealScheduleTemplate', 'enabled', False, True),
    ('MealScheduleTemplate', 'auto_publish', False, True),
    ('MemberProposal', 'proposal_type', 'resolution', 'multiple_choice'),
    ('MemberRosterEntry', 'share_capital_amount', 0, 2500),
    ('MemberRosterEntry', 'share_count', 0, 5),
    ('MemberRosterEntry', 'is_active', True, False),
    ('Membership', 'share_capital_amount', 0, 5000),
    ('Membership', 'share_count', 0, 10),
    ('Order', 'tax_amount', 0, 5),
]
STATE = {'original_red_head': '4be705a3b5bdffed0088dc6025082319526222a9', 'scope': 'SQLite-only synthetic schema/default correctness; no app requests or performance claims',
    'migration_target': 'head', 'cases': [], 'stores': [], 'source_changed': False}


def value_of(value):
    return getattr(value, 'value', value)


def source_hashes():
    paths = [ROOT / 'backend/app/models.py', ROOT / 'backend/app/database.py', ROOT / 'backend/app/schemas.py',
        ROOT / 'backend/tests/test_sqlite_cash_default.py', *sorted((ROOT / 'backend/alembic/versions').glob('*.py'))]
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


@pytest.fixture(scope='module', autouse=True)
def safety_and_evidence():
    before = source_hashes()
    patch = pytest.MonkeyPatch()
    original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex
    def blocked(*args, **kwargs): raise AssertionError('Network/process/provider work is forbidden in the default candidate')
    def connect(connection, address):
        if connection.family in {socket.AF_INET, socket.AF_INET6}: return blocked()
        return original_connect(connection, address)
    def connect_ex(connection, address):
        if connection.family in {socket.AF_INET, socket.AF_INET6}: return blocked()
        return original_connect_ex(connection, address)
    patch.setattr(socket.socket, 'connect', connect); patch.setattr(socket.socket, 'connect_ex', connect_ex)
    patch.setattr(socket, 'create_connection', blocked)
    patch.setattr(subprocess, 'Popen', blocked); patch.setattr(subprocess, 'run', blocked)
    try:
        yield
    finally:
        patch.undo()
        STATE['sources_before'] = before; STATE['sources_after'] = source_hashes()
        STATE['source_changed'] = before != STATE['sources_after']
        STATE['candidate_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        STATE['owned_databases_removed'] = all(item.get('removed') is True for item in STATE['stores'])
        if os.environ.get('DEFAULT_PARITY_EVIDENCE'):
            destination = Path(os.environ['DEFAULT_PARITY_EVIDENCE']).resolve()
            assert destination.is_relative_to((ROOT / 'outputs').resolve())
            destination.write_text(json.dumps(STATE, ensure_ascii=False, indent=2) + '\n')
        assert not STATE['source_changed']
        assert STATE['owned_databases_removed']


@pytest.fixture(scope='module', params=['metadata', 'alembic'])
def store(request, safety_and_evidence):
    record = {'path': request.param, 'removed': False}
    STATE['stores'].append(record)
    with tempfile.TemporaryDirectory(prefix='farm-defaults-a-') as directory:
        database = Path(directory) / 'owned.sqlite'
        assert database.parent == Path(directory) and not database.is_symlink()
        engine = None
        try:
            if request.param == 'alembic':
                previous = config.get_settings
                config.get_settings = lambda: isolated_settings(f'sqlite+aiosqlite:///{database}')
                try:
                    migration = Config(str(ROOT / 'backend/alembic.ini'))
                    migration.set_main_option('script_location', str(ROOT / 'backend/alembic'))
                    command.upgrade(migration, STATE['migration_target'])
                finally:
                    config.get_settings = previous
            engine = create_engine(f'sqlite+pysqlite:///{database}')
            @event.listens_for(engine, 'connect')
            def enable_fk(connection, _record):
                cursor = connection.cursor()
                try: cursor.execute('PRAGMA foreign_keys=ON')
                finally: cursor.close()
            if request.param == 'metadata': Base.metadata.create_all(engine)
            with engine.connect() as connection:
                assert connection.scalar(text('PRAGMA foreign_keys')) == 1
                if request.param == 'alembic': assert connection.scalar(text('SELECT version_num FROM alembic_version')) == ScriptDirectory.from_config(migration).get_current_head()
                record['tables'] = len(inspect(connection).get_table_names())
                record['foreign_keys'] = 1
            yield SimpleNamespace(engine=engine, path=request.param)
        finally:
            if engine is not None: engine.dispose()
    record['removed'] = not database.exists()


def new_user(session):
    user = models.User(email=f'default-{uuid4().hex}@example.test', display_name='合成預設值使用者', password_hash='!unused')
    session.add(user); session.flush()
    return user


def build_row(session, owner_name, column, supplied=OMIT):
    owner = new_user(session)
    key = str(uuid4())
    if owner_name == 'MealEvent':
        row = models.MealEvent(id=key, title='合成場次', location='隔離', created_by_id=owner.id,
            ordering_starts_at=NOW, ordering_ends_at=NOW + timedelta(days=1),
            pickup_starts_at=NOW + timedelta(days=2), pickup_ends_at=NOW + timedelta(days=2, hours=2))
    elif owner_name == 'MealScheduleTemplate':
        meal = models.Meal(slug='default-' + uuid4().hex, name='合成餐點', price=100)
        session.add(meal); session.flush()
        row = models.MealScheduleTemplate(id=key, title='合成排程', location='隔離', meal_period='lunch',
            pickup_start_time='11:00', pickup_end_time='14:00', cutoff_time='13:00', weekdays=[0],
            offerings=[{'meal_id': meal.id, 'price': 100, 'capacity': 10, 'position': 0}], created_by_id=owner.id)
    elif owner_name == 'MemberProposal':
        row = models.MemberProposal(id=key, created_by_id=owner.id, title='合成提案', body='隔離預設值控制')
        if supplied == 'multiple_choice':
            row.options = [models.ProposalOption(label=label, position=index) for index, label in enumerate(['選項甲', '選項乙'])]
    elif owner_name == 'MemberRosterEntry':
        aad = f'member-roster:{key}'
        row = models.MemberRosterEntry(id=key, member_number='DEFAULT-' + uuid4().hex[:20],
            legal_name_encrypted=CIPHER.encrypt_text('合成姓名', associated_data=aad),
            email_encrypted=CIPHER.encrypt_text(f'default-{key}@example.test', associated_data=aad),
            phone_encrypted=CIPHER.encrypt_text('0900000000', associated_data=aad))
    elif owner_name == 'Membership':
        row = models.Membership(id=key, user_id=owner.id)
    elif owner_name == 'Order':
        row = models.Order(id=key, order_number='DF-' + uuid4().hex[:24], user_id=owner.id,
            order_kind='regular', membership_type_snapshot='nonmember', amount_total=100,
            contact_email='synthetic-order@example.test')
    elif owner_name == 'GroupCampaign':
        product = models.Product(slug='default-' + uuid4().hex, name='合成商品', category='隔離', unit='份',
            member_price=100, nonmember_price=100, stock_quantity=10)
        session.add(product); session.flush()
        row = models.GroupCampaign(id=key, target_type='product', target_id=product.id, title='合成共同購買',
            member_price=100, nonmember_price=100, min_paid_quantity=1, supply_cap=10, per_user_cap=5,
            deadline=NOW + timedelta(days=1), estimated_pickup_start=NOW + timedelta(days=2),
            estimated_pickup_end=NOW + timedelta(days=3), created_by_id=owner.id)
    else: raise AssertionError(owner_name)
    if supplied is not OMIT: setattr(row, column, supplied)
    session.add(row); session.flush()
    return row


def observe(session, model, row_id, column):
    table = model.__table__.name
    raw, sqlite_type = session.execute(text(f'SELECT "{column}",typeof("{column}") FROM "{table}" WHERE id=:id'), {'id': row_id}).one()
    session.expire_all()
    typed = value_of(getattr(session.get(model, row_id), column))
    return {'accepted': True, 'raw_value': raw, 'sqlite_type': sqlite_type, 'orm_value': typed}


def clone_with_raw_sql(store, owner_name, column, *, supplied=OMIT):
    model = getattr(models, owner_name)
    with Session(store.engine) as session:
        row = build_row(session, owner_name, column)
        original_id = row.id
        session.commit()
    clone_id = str(uuid4())
    replacements = {'id': clone_id}
    with Session(store.engine) as session:
        if owner_name == 'Membership': replacements['user_id'] = new_user(session).id
        if owner_name == 'MemberRosterEntry':
            replacements['member_number'] = 'DEFAULT-' + uuid4().hex[:20]
            aad = f'member-roster:{clone_id}'
            replacements.update(legal_name_encrypted=CIPHER.encrypt_text('合成姓名', associated_data=aad),
                email_encrypted=CIPHER.encrypt_text(f'default-{clone_id}@example.test', associated_data=aad),
                phone_encrypted=CIPHER.encrypt_text('0900000000', associated_data=aad))
        if owner_name == 'Order': replacements.update(order_number='DF-' + uuid4().hex[:24], client_request_id=str(uuid4()))
        if owner_name == 'MemberProposal': replacements['client_request_id'] = str(uuid4())
        session.commit()
    columns = [item.name for item in model.__table__.columns if item.name != column or supplied is not OMIT]
    if supplied is not OMIT: replacements[column] = int(supplied) if isinstance(supplied, bool) else supplied
    names = ','.join(f'"{name}"' for name in columns)
    expressions = ','.join(':v_' + name if name in replacements else f'"{name}"' for name in columns)
    parameters = {'v_' + name: value for name, value in replacements.items()}
    parameters['original_id'] = original_id
    with Session(store.engine) as session:
        try:
            session.execute(text(f'INSERT INTO "{model.__table__.name}" ({names}) SELECT {expressions} FROM "{model.__table__.name}" WHERE id=:original_id'), parameters)
            result = observe(session, model, clone_id, column)
        except IntegrityError as error:
            result = {'accepted': False, 'error_type': 'IntegrityError', 'constraint_error': str(error.orig)}
        finally:
            session.rollback()
    return result


def verify_value(result, expected):
    assert result['accepted'] is True, result
    assert result['orm_value'] == expected, result
    if isinstance(expected, bool):
        assert result['orm_value'] is expected and result['raw_value'] == int(expected) and result['sqlite_type'] == 'integer', result
    elif isinstance(expected, int): assert result['raw_value'] == expected and result['sqlite_type'] == 'integer', result
    else: assert result['raw_value'] == expected, result


@pytest.mark.parametrize('owner,column,expected,explicit', CASES, ids=[owner + '.' + column for owner, column, _, _ in CASES])
def test_each_raw_omission_preserves_existing_migration_default(store, owner, column, expected, explicit):
    result = clone_with_raw_sql(store, owner, column)
    STATE['cases'].append({'schema': store.path, 'mode': 'raw_omission', 'model': owner, 'column': column, 'expected': expected, **result})
    verify_value(result, expected)


@pytest.mark.parametrize('owner,column,expected,explicit', CASES, ids=[owner + '.' + column for owner, column, _, _ in CASES])
def test_orm_omission_and_explicit_value_remain_valid(store, owner, column, expected, explicit):
    for mode, supplied, target in [('orm_omission', OMIT, expected), ('orm_explicit', explicit, explicit)]:
        with Session(store.engine) as session:
            row = build_row(session, owner, column, supplied)
            result = observe(session, getattr(models, owner), row.id, column)
            STATE['cases'].append({'schema': store.path, 'mode': mode, 'model': owner, 'column': column, 'expected': target, **result})
            verify_value(result, target)
            session.rollback()


@pytest.mark.parametrize('mode', ['raw_omission', 'orm_omission', 'raw_true', 'orm_true'])
def test_legacy_confirmed_flag_is_separate_and_uses_native_boolean(store, mode):
    column = 'legacy_confirmed_intake'; expected = mode.endswith('_true')
    if mode.startswith('raw'):
        result = clone_with_raw_sql(store, 'GroupCampaign', column, supplied=True if expected else OMIT)
    else:
        with Session(store.engine) as session:
            row = build_row(session, 'GroupCampaign', column, True if expected else OMIT)
            result = observe(session, models.GroupCampaign, row.id, column)
            session.rollback()
    STATE['cases'].append({'schema': store.path, 'mode': mode, 'model': 'GroupCampaign', 'column': column, 'expected': expected, **result})
    verify_value(result, expected)


def test_api_cutoff_default_stays_one_while_db_contract_stays_zero():
    body = MealScheduleInput(title='合成排程', location='隔離', meal_period='lunch',
        offerings=[{'meal_id': str(uuid4()), 'price': 100, 'capacity': 10}])
    assert body.cutoff_days_before == 1
    assert next(expected for owner, column, expected, _ in CASES if column == 'cutoff_days_before') == 0
