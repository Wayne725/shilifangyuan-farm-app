"""Read-only authorization checks using isolated sessions, never live providers."""

import re

import pytest
from fastapi.routing import APIRoute

from app.auth import get_current_user, require_active_member, require_admin
from app.models import User, UserRole
from app.routers import ALL_ROUTERS
from tests.support import api_test_context, auth_headers, make_test_settings

pytestmark = pytest.mark.asyncio


def dependency_calls(dependant):
    yield dependant.call
    for child in dependant.dependencies:
        yield from dependency_calls(child)


def routes_requiring(dependency):
    cases = []
    for route in (route for router in ALL_ROUTERS for route in router.routes):
        if not isinstance(route, APIRoute):
            continue
        if dependency not in set(dependency_calls(route.dependant)):
            continue
        for method in sorted(route.methods):
            cases.append(pytest.param(method, route.path, id=f"{method} {route.path}"))
    return cases


def concrete_path(path):
    return re.sub(r"\{[^}]+\}", "00000000-0000-4000-8000-000000000099", path)


async def test_all_admin_paths_have_admin_dependency():
    for router in ALL_ROUTERS:
        for route in router.routes:
            if isinstance(route, APIRoute) and "/admin" in route.path:
                assert require_admin in set(dependency_calls(route.dependant)), route.path


@pytest.mark.parametrize("method,path", routes_requiring(get_current_user))
async def test_anonymous_cannot_access_authenticated_routes(database_session, method, path):
    async with api_test_context(
        database_session, ALL_ROUTERS, settings=make_test_settings()
    ) as client:
        response = await client.request(method, concrete_path(path), json={})
    assert response.status_code == 401, (method, path, response.text)


@pytest.mark.parametrize(
    "method,path", routes_requiring(require_admin) + routes_requiring(require_active_member)
)
async def test_customer_cannot_access_privileged_routes(database_session, method, path):
    user = User(
        email="audit-customer@example.test",
        display_name="Audit customer",
        password_hash="unused",
        user_role=UserRole.CUSTOMER,
    )
    database_session.add(user)
    await database_session.commit()
    async with api_test_context(
        database_session, ALL_ROUTERS, settings=make_test_settings()
    ) as client:
        response = await client.request(
            method, concrete_path(path), headers=auth_headers(user), json={}
        )
    assert response.status_code == 403, (method, path, response.text)
