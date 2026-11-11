import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from tests.support import make_test_settings


@pytest.mark.parametrize('environment,expected', [('production', 404), ('development', 200)])
async def test_documentation_exposure_follows_environment(monkeypatch, environment, expected):
    import app.main as main

    settings = make_test_settings(environment=environment)
    monkeypatch.setattr(main, 'get_settings', lambda: settings)
    # Deployment secret validation is covered separately; no lifespan or worker starts.
    monkeypatch.setattr(Settings, 'validate_runtime_secrets', lambda self: None)
    application = main.create_app()
    async with AsyncClient(transport=ASGITransport(app=application), base_url='http://test') as client:
        for path in ['/docs', '/redoc', '/openapi.json']:
            assert (await client.get(path)).status_code == expected
