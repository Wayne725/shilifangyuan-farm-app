from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.domain import aware
from app.models import AdminAudit, Activity, ActivityCharge, ActivityRegistration, Notification, Order, OutboxEvent, PaymentAttempt, Refund
from app.routers.community import community_router
from app.routers.groups import groups_router
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_activity_payments import make_activity, pending_attempt, registration
from tests.test_group_review_window import group_fixture


async def counts(session):
    return {model.__tablename__: await session.scalar(select(func.count()).select_from(model))
            for model in [ActivityCharge, ActivityRegistration, Notification, Order, OutboxEvent, PaymentAttempt, Refund]}


async def test_hidden_activity_keeps_existing_participant_and_finance(database_session):
    context = await make_activity(database_session)
    registered = await registration(context)
    previous = await counts(database_session)
    status = context.activity.status
    async with api_test_context(database_session, [community_router], settings=make_test_settings()) as client:
        body = {'is_public': False, 'expected_updated_at': aware(context.activity.updated_at).isoformat()}
        path = f'/v1/admin/activities/{context.activity.id}/visibility'
        assert (await client.patch(path, json=body)).status_code == 401
        assert (await client.patch(path, json=body, headers=auth_headers(context.second))).status_code == 403
        hidden = await client.patch(path, json=body, headers=auth_headers(context.admin))
        assert hidden.status_code == 200, hidden.text
        assert hidden.json()['is_public'] is False
        assert (await client.get('/v1/activities')).json() == []
        detail = f'/v1/activities/{context.activity.id}'
        assert (await client.get(detail)).status_code == 404
        assert (await client.get(detail, headers=auth_headers(context.second))).status_code == 404
        assert (await client.get(detail, headers=auth_headers(context.buyer))).status_code == 200
        assert (await client.get(detail, headers=auth_headers(context.admin))).status_code == 200
        assert (await client.get('/v1/activities', headers=auth_headers(context.admin))).json() == []
        assert (await client.get('/v1/admin/activities')).status_code == 401
        assert (await client.get('/v1/admin/activities', headers=auth_headers(context.second))).status_code == 403
        admin_rows = (await client.get('/v1/admin/activities', headers=auth_headers(context.admin))).json()
        assert len(admin_rows) == 1 and admin_rows[0]['id'] == context.activity.id and admin_rows[0]['is_public'] is False
        assert (await client.post(detail + '/register', headers=auth_headers(context.second))).status_code == 404
        replay = await client.post(detail + '/register', headers=auth_headers(context.buyer))
        assert replay.status_code == 200 and replay.json()['id'] == registered.id
        assert (await client.patch(path, json=body, headers=auth_headers(context.admin))).status_code == 409
        restored = await client.patch(path, headers=auth_headers(context.admin), json={
            'is_public': True, 'expected_updated_at': hidden.json()['updated_at']})
        assert restored.status_code == 200 and restored.json()['is_public'] is True
        assert len((await client.get('/v1/activities')).json()) == 1
    assert await counts(database_session) == previous
    assert context.activity.status == status
    assert await database_session.scalar(select(func.count(AdminAudit.id)).where(AdminAudit.action == 'activity.visibility_changed')) == 2


async def test_hidden_group_is_not_public_but_order_owner_can_read(database_session):
    admin, campaign, buyer, order, _, _ = await group_fixture(database_session, cutoff=False)
    from app.models import User
    other = User(email='visibility-other@example.com', display_name='無關買家', password_hash='unused')
    database_session.add(other)
    await database_session.commit()
    previous = await counts(database_session)
    financial = (order.payment_status, order.amount_total, campaign.paid_quantity, campaign.reserved_quantity, campaign.decision_status, campaign.intake_status)
    async with api_test_context(database_session, [groups_router], settings=make_test_settings()) as client:
        path = f'/v1/group-campaigns/{campaign.id}/admin/visibility'
        hidden = await client.patch(path, headers=auth_headers(admin), json={
            'is_public': False, 'expected_updated_at': aware(campaign.updated_at).isoformat()})
        assert hidden.status_code == 200, hidden.text
        assert (await client.get('/v1/group-campaigns')).json() == []
        assert (await client.get('/v1/group-campaigns?include_hidden=true')).status_code == 401
        assert (await client.get('/v1/group-campaigns?include_hidden=true', headers=auth_headers(buyer))).status_code == 403
        rows = await client.get('/v1/group-campaigns?include_hidden=true', headers=auth_headers(admin))
        assert rows.status_code == 200 and rows.json()[0]['id'] == campaign.id
        detail = f'/v1/group-campaigns/{campaign.id}'
        assert (await client.get(detail)).status_code == 404
        assert (await client.get(detail, headers=auth_headers(other))).status_code == 404
        assert (await client.get(detail, headers=auth_headers(buyer))).status_code == 200
        assert (await client.get(detail, headers=auth_headers(admin))).status_code == 200
        payload = {'quantity': 1, 'contact_email': other.email}
        assert (await client.post(detail + '/quote', headers=auth_headers(other), json=payload)).status_code == 404
        assert (await client.post(detail + '/join', headers=auth_headers(other), json=payload)).status_code == 404
        stale = await client.patch(path, headers=auth_headers(admin), json={
            'is_public': True, 'expected_updated_at': (aware(campaign.updated_at) - timedelta(days=1)).isoformat()})
        assert stale.status_code == 409
        edited = await client.patch(detail, headers=auth_headers(admin), json={'description': '仍保留管理編輯', 'is_public': True})
        assert edited.status_code == 200, edited.text
        assert edited.json()['is_public'] is False
        restored = await client.patch(path, headers=auth_headers(admin), json={
            'is_public': True, 'expected_updated_at': edited.json()['updated_at']})
        assert restored.status_code == 200
        assert len((await client.get('/v1/group-campaigns')).json()) == 1
    assert await counts(database_session) == previous
    assert (order.payment_status, order.amount_total, campaign.paid_quantity, campaign.reserved_quantity, campaign.decision_status, campaign.intake_status) == financial


@pytest.mark.parametrize('body', [{'is_public': 'false', 'expected_updated_at': '2030-01-01T00:00:00Z'},
                                 {'is_public': False}, {'is_public': False, 'expected_updated_at': '2030-01-01T00:00:00'}])
async def test_visibility_update_requires_boolean_and_version(database_session, body):
    context = await make_activity(database_session)
    async with api_test_context(database_session, [community_router], settings=make_test_settings()) as client:
        response = await client.patch(f'/v1/admin/activities/{context.activity.id}/visibility',
            headers=auth_headers(context.admin), json=body)
    assert response.status_code == 422
    assert context.activity.is_public is True


async def test_hidden_activity_existing_payment_callback_still_completes(database_session):
    from uuid import uuid4
    from app.integrations.payment_service import SQLAlchemyPaymentCallbackRepository
    from app.models import ActivityRegistrationStatus, PaymentStatus
    from tests.test_order_payment_revisions import success_payload

    context = await make_activity(database_session)
    registered = await registration(context)
    attempt = await pending_attempt(context, registered.charge.id)
    async with api_test_context(database_session, [community_router], settings=make_test_settings()) as client:
        hidden = await client.patch(f'/v1/admin/activities/{context.activity.id}/visibility',
            headers=auth_headers(context.admin), json={'is_public': False, 'expected_updated_at': aware(context.activity.updated_at).isoformat()})
        assert hidden.status_code == 200
    result = await SQLAlchemyPaymentCallbackRepository(database_session).apply_raygate_payment_callback(uuid4().hex, success_payload(attempt))
    assert result == 'paid'
    charge = await database_session.get(ActivityCharge, registered.charge.id)
    current = await database_session.get(ActivityRegistration, registered.id)
    assert charge.payment_status == PaymentStatus.PAID
    assert current.status == ActivityRegistrationStatus.REGISTERED
    assert await database_session.scalar(select(func.count(Refund.id))) == 0


async def test_hidden_group_keeps_original_submission_replay(database_session):
    from uuid import uuid4
    from app.models import User, UserRole
    from tests.test_member_invoice_tax import ROUTERS, checkout_context, quote_and_create

    context = await checkout_context(database_session, 'group')
    context.payload['client_request_id'] = str(uuid4())
    admin = User(email='visibility-admin@example.com', display_name='隔離管理員', password_hash='unused', user_role=UserRole.ADMIN)
    database_session.add(admin)
    await database_session.commit()
    async with api_test_context(database_session, ROUTERS, settings=make_test_settings()) as client:
        _, created = await quote_and_create(client, context)
        detail = context.create_path.removesuffix('/join')
        campaign = (await client.get(detail)).json()
        hidden = await client.patch(detail + '/admin/visibility', headers=auth_headers(admin), json={
            'is_public': False, 'expected_updated_at': campaign['updated_at']})
        assert hidden.status_code == 200
        replay = await client.post(context.create_path, headers=auth_headers(context.user), json=context.payload)
        assert replay.status_code == 201 and replay.json()['id'] == created['id']
        fresh = {**context.payload, 'client_request_id': str(uuid4())}
        assert (await client.post(context.create_path, headers=auth_headers(context.user), json=fresh)).status_code == 404
    assert await database_session.scalar(select(func.count(Order.id))) == 1
