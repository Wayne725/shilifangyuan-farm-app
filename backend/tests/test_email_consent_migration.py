from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text

from app.config import get_settings


def test_email_consent_migration_preserves_users_and_refuses_erasing_consent(tmp_path, monkeypatch):
    path = tmp_path / 'email-consent.db'
    monkeypatch.setenv('DATABASE_URL', f'sqlite+aiosqlite:///{path}')
    get_settings.cache_clear()
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / 'alembic.ini'))
    config.set_main_option('script_location', str(root / 'alembic'))
    engine = create_engine(f'sqlite:///{path}')
    try:
        command.upgrade(config, '0052_business_meal_orders')
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO users(id,email,display_name,password_hash,user_role,membership_type,is_active,token_version,created_at,updated_at,pending_member_claim) VALUES ('legacy-consent-user','legacy@example.com','歷史買家','unused','CUSTOMER','NONMEMBER',1,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,0)"))
            before = connection.execute(text('SELECT id,email,password_hash FROM users')).all()
        command.upgrade(config, 'head')
        command.check(config)
        with engine.connect() as connection:
            assert connection.execute(text('SELECT id,email,password_hash FROM users')).all() == before
            assert connection.execute(text('SELECT data_consent_version,data_consented_at FROM users')).one() == (None, None)
        command.downgrade(config, '0052_business_meal_orders')
        assert 'data_consent_version' not in {column['name'] for column in inspect(engine).get_columns('users')}
        command.upgrade(config, 'head')
        with engine.begin() as connection:
            connection.execute(text("UPDATE users SET data_consent_version='accepted-v1',data_consented_at=CURRENT_TIMESTAMP"))
        with pytest.raises(RuntimeError, match='已有註冊同意紀錄'):
            command.downgrade(config, '0052_business_meal_orders')
        with engine.connect() as connection:
            assert connection.execute(text('SELECT data_consent_version FROM users')).scalar_one() == 'accepted-v1'
    finally:
        engine.dispose()
        get_settings.cache_clear()
