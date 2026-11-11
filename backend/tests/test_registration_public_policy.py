import pytest
from sqlalchemy import select

from app.models import SystemSetting, User, UserRole
from app.platform_settings import PLATFORM_KEY
from app.routers.auth import auth_router
from app.routers.platform import platform_router
from tests.support import api_test_context, auth_headers, make_test_settings


def information(**changes):
    return dict(published=True, legal_name='隔離合作社', tax_id='15989995', address='隔離營業地址',
                phone='0223456789', email='support@example.com', policy_version='test-v1',
                privacy='合成個資告知', terms='合成服務條款', returns='合成退款政策', **changes)


@pytest.mark.parametrize('consent,version', [(False, None), (True, 'old-version'), (False, 'test-v1')])
async def test_published_policy_is_enforced_before_creating_account(database_session, consent, version):
    database_session.add(SystemSetting(key=PLATFORM_KEY, value={'public_information': information()}))
    await database_session.commit()
    async with api_test_context(database_session, [auth_router], settings=make_test_settings()) as client:
        response = await client.post('/v1/auth/register', json={'email': 'new@example.com', 'display_name': '隔離新買家',
            'password': 'synthetic-password-123', 'data_consent': consent, 'policy_version': version})
    assert response.status_code == 422
    assert await database_session.scalar(select(User.id).where(User.email == 'new@example.com')) is None


async def test_email_registration_persists_accepted_policy(database_session):
    database_session.add(SystemSetting(key=PLATFORM_KEY, value={'public_information': information()}))
    await database_session.commit()
    async with api_test_context(database_session, [auth_router], settings=make_test_settings()) as client:
        response = await client.post('/v1/auth/register', json={'email': 'new@example.com', 'display_name': '隔離新買家',
            'password': 'synthetic-password-123', 'data_consent': True, 'policy_version': 'test-v1'})
    assert response.status_code == 201, response.text
    user = await database_session.scalar(select(User).where(User.email == 'new@example.com'))
    assert user.data_consent_version == 'test-v1' and user.data_consented_at is not None


async def test_unpublished_draft_is_private_and_legacy_settings_updates_preserve_it(database_session):
    admin = User(email='admin@example.com', display_name='隔離管理員', password_hash='unused', user_role=UserRole.ADMIN)
    database_session.add(admin)
    draft = information(); draft['published'] = False
    database_session.add(SystemSetting(key=PLATFORM_KEY, value={'public_information': draft}))
    await database_session.commit()
    async with api_test_context(database_session, [platform_router], settings=make_test_settings()) as client:
        public = await client.get('/v1/platform')
        assert public.json()['public_information'] is None
        assert '隔離營業地址' not in public.text
        assert (await client.get('/v1/admin/platform')).status_code == 401
        response = await client.put('/v1/admin/platform', headers=auth_headers(admin), json={'expected_version': 0, 'orders_open': True})
        assert response.status_code == 200, response.text
        assert response.json()['public_information'] == draft


async def test_policy_content_changes_require_new_version(database_session):
    admin = User(email='admin@example.com', display_name='隔離管理員', password_hash='unused', user_role=UserRole.ADMIN)
    database_session.add(admin)
    database_session.add(SystemSetting(key=PLATFORM_KEY, value={'public_information': information()}))
    await database_session.commit()
    async with api_test_context(database_session, [platform_router], settings=make_test_settings()) as client:
        changed = information(); changed['privacy'] = '另一版告知'
        blocked = await client.put('/v1/admin/platform', headers=auth_headers(admin), json={'expected_version': 0, 'public_information': changed})
        assert blocked.status_code == 409
        changed['policy_version'] = 'test-v2'
        saved = await client.put('/v1/admin/platform', headers=auth_headers(admin), json={'expected_version': 0, 'public_information': changed})
        assert saved.status_code == 200, saved.text
        assert (await client.get('/v1/platform')).json()['public_information']['policy_version'] == 'test-v2'


@pytest.mark.parametrize('field,value', [('legal_name', ''), ('tax_id', '12345678'), ('phone', 'abc'), ('privacy', '')])
def test_incomplete_information_cannot_be_published(field, value):
    from pydantic import ValidationError
    from app.public_information import PublicInformation
    data = information(); data[field] = value
    with pytest.raises(ValidationError):
        PublicInformation.model_validate(data)


async def test_unpublish_edit_republish_cannot_rebind_old_policy_version(database_session):
    from app.models import PublicPolicyVersion
    admin = User(email='policy-admin@example.com', display_name='隔離管理員', password_hash='unused', user_role=UserRole.ADMIN)
    database_session.add(admin)
    await database_session.commit()
    async with api_test_context(database_session, [platform_router], settings=make_test_settings()) as client:
        async def save(version, value):
            return await client.put('/v1/admin/platform', headers=auth_headers(admin), json={'expected_version': version, 'public_information': value})
        original = information()
        assert (await save(0, original)).status_code == 200
        draft = dict(original, published=False, privacy='變更的草稿內容')
        assert (await save(1, draft)).status_code == 200
        blocked = await save(2, dict(draft, published=True))
        assert blocked.status_code == 409
        assert (await database_session.get(PublicPolicyVersion, 'test-v1')).content['privacy'] == original['privacy']
        v2 = dict(draft, published=True, policy_version='test-v2')
        assert (await save(2, v2)).status_code == 200
        assert (await save(3, dict(v2, policy_version='test-v1'))).status_code == 409
        assert (await client.get('/v1/platform')).json()['public_information']['policy_version'] == 'test-v2'
