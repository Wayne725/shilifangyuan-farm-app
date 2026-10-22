import base64
import json
from datetime import datetime, timezone
import pytest
from sqlalchemy import select
from app.models import LineOAuthState, MemberRosterEntry, Membership, User
from app.routers.membership import membership_router
from app.routers.line_auth import line_auth_router
from app.routers.auth import auth_router
from tests.support import api_test_context, auth_headers
from tests.test_line_login import start_flow, callback, profile, settings


@pytest.mark.asyncio
@pytest.mark.parametrize("contact_only", [True, False])
async def test_line_member_requires_matching_roster_and_otp_before_qualification(database_session, settings, profile, contact_only):
    configured = settings.model_copy(update={'environment': 'development', 'pii_encryption_keys_json': json.dumps({'v1': base64.b64encode(b'p'*32).decode()})})
    from app.models import UserRole
    admin = User(email='intake-admin@example.com', display_name='管理員', password_hash='test', user_role=UserRole.ADMIN)
    database_session.add(admin)
    await database_session.commit()
    async with api_test_context(database_session, [line_auth_router, auth_router, membership_router], settings=configured) as client:
        roster = await client.post('/v1/admin/member-roster', headers=auth_headers(admin), json={'member_number': 'INTAKE-1', 'legal_name': '社員甲', 'email': 'member@example.com', 'phone': '0912345678'})
        assert roster.status_code == 201, roster.text
        claim = {'member_number': 'INTAKE-1', 'legal_name': '社員甲', 'email': 'member@example.com'}
        if contact_only:
            claim = {'phone': '+886 912-345-678', 'email': 'member@example.com'}
        invalid_claim = {**claim, 'phone': '0987654321'} if contact_only else {**claim, 'legal_name': '錯誤'}
        invalid = await client.post('/v1/auth/line/start', json={'intent': 'member', 'data_consent_version': 'line-data-v1', 'member_claim': invalid_claim})
        assert invalid.status_code == 409
        state, _ = await start_flow(client, intent='member', member_claim=claim)
        record = await database_session.scalar(select(LineOAuthState))
        assert 'member@example.com' not in record.member_claim_encrypted
        result = await callback(client, state)
        assert result.status_code == 200, result.text
        data = result.json()
        assert data['session']['user']['membership_type'] == 'nonmember'
        assert '/verify-email?' in data['return_to'] and 'intent=existing_member' in data['return_to']
        owner = await database_session.scalar(select(User).where(User.email == claim['email']))
        assert owner.display_name == profile[0]['display_name']
        assert owner.pending_member_claim
        assert await database_session.scalar(select(Membership).where(Membership.user_id == owner.id)) is None
        await database_session.refresh(record)
        assert record.member_claim_encrypted is None
        resent = await client.post('/v1/auth/resend-verification', json={'email': claim['email']})
        assert resent.status_code == 202 and resent.json()['development_token']
        verified = await client.post('/v1/auth/verify-email', json={'email': claim['email'], 'token': resent.json()['development_token']})
        assert verified.status_code == 200, verified.text
        member = await database_session.scalar(select(Membership).where(Membership.user_id == owner.id))
        assert member.member_number == 'INTAKE-1' and member.status.value == 'active'
        assert (await callback(client, state)).status_code == 400
