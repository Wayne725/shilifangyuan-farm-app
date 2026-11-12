from httpx import ASGITransport, AsyncClient

from app import main
from app.database import get_session
from app.models import Membership, MembershipStatus, User, UserRole
from tests.support import auth_headers, make_test_settings


async def test_admin_help_requires_current_admin_role_and_disables_caching(database_session, monkeypatch):
    settings = make_test_settings()
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    application = main.create_app()

    async def override_session():
        yield database_session

    application.dependency_overrides[get_session] = override_session
    admin = User(email="faq-admin@example.com", display_name="測試管理員", password_hash="test", user_role=UserRole.ADMIN)
    member = User(email="faq-member@example.com", display_name="測試社員", password_hash="test")
    member.membership = Membership(status=MembershipStatus.ACTIVE)
    database_session.add_all([admin, member])
    await database_session.commit()
    question = "管理員如何匯入金流、銀行與發票明細？"

    async with AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client:
        path = "/v1/admin/help"
        admin_headers = auth_headers(admin)
        guest = await client.get(path)
        authenticated_member = await client.get(path, headers=auth_headers(member))
        allowed = await client.get(path, headers=admin_headers)
        assert guest.status_code == 401
        assert authenticated_member.status_code == 403
        assert allowed.status_code == 200
        answers = {item["question"]: item["answer"] for item in allowed.json()}
        assert len(answers) == 29
        assert "廚房出單 → 印表機與提示音" in answers["出單機與廚房提示音如何使用？"]
        assert "會餐預訂成功後先送備餐單，付款完成才可領餐" in answers["出單機與廚房提示音如何使用？"]
        assert "會員管理 → 社員帳冊 → 社員股籍" in answers["如何匯出社員及股籍資料？"]
        assert "訂單營運 → 手動開票" in answers["手動開票紀錄在哪裡？"]
        assert question in answers
        assert "什麼是會餐？" not in answers

        admin.user_role = member.user_role
        await database_session.commit()
        revoked = await client.get(path, headers=admin_headers)
        assert revoked.status_code == 403
        for response in [guest, authenticated_member, allowed, revoked]:
            assert response.headers["Cache-Control"] == "no-store"
        assert all(question not in response.text for response in [guest, authenticated_member, revoked])
