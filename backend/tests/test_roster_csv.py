import io
import csv

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.integrations.pii_crypto import pii_cipher_from_settings
from app.member_claims import roster_aad
from app.models import AdminAudit, MemberRosterEntry, Membership, MembershipCharge, PaymentAttempt, Refund, User, UserRole
from app.roster_csv import MAX_CSV_BYTES, parse_roster_csv
from app.routers.membership import membership_router
from tests.support import api_test_context, auth_headers
from tests.test_v2_routers import settings

HEADERS = ["社員編號", "姓名", "Email", "手機", "股金", "股數"]
ROW = [" m-001 ", "測試社員", "member@example.com", "0912-345-678", "1000", "1"]


def csv_bytes(rows, headers=HEADERS, encoding="utf-8-sig"):
    text = io.StringIO(newline="")
    writer = csv.writer(text)
    writer.writerow(headers)
    writer.writerows(rows)
    return text.getvalue().encode(encoding)


@pytest.mark.parametrize("encoding", ["utf-8-sig", "cp950"])
def test_chinese_headers_encodings_and_normalization(encoding):
    rows = parse_roster_csv(csv_bytes([ROW], encoding=encoding))
    entry = rows[0].entry
    assert not rows[0].errors
    assert entry.member_number == "M-001"
    assert entry.phone == "0912345678"
    assert entry.share_capital_amount == 1000


def test_quoted_names_newlines_optional_columns_and_international_phone():
    rows = parse_roster_csv(csv_bytes([
        ["M1", "陳,小明\n二", "a@example.com", "+886912345678"],
        ["M2", "第二位", "b@example.com", "０９１２３４５６７９"],
    ], headers=HEADERS[:4]))
    assert [row.row_number for row in rows] == [2, 4]
    assert rows[0].entry.legal_name == "陳,小明\n二"
    assert rows[0].entry.phone == "0912345678"
    assert rows[1].entry.share_count == 0


@pytest.mark.parametrize("field,value", [(2, "not-email"), (3, "912345678"), (3, "abc0912345678"), (4, "-1"), (5, "1.5"), (5, "2147483648"), (1, "   ")])
def test_invalid_data_reports_row_errors(field, value):
    row = ROW.copy()
    row[field] = value
    result = parse_roster_csv(csv_bytes([row]))
    assert result[0].errors
    assert result[0].entry is None


def test_both_normalized_duplicate_rows_are_rejected():
    rows = parse_roster_csv(csv_bytes([ROW, ["Ｍ－００１", "重複", "b@example.com", "0912345678", "", ""]]))
    assert all("社員編號在檔案中重複" in row.errors for row in rows)


@pytest.mark.parametrize("content,message", [
    (b"", "缺少欄位"),
    (csv_bytes([], headers=HEADERS), "沒有社員資料"),
    (csv_bytes([], headers=HEADERS + ["email"]), "重複的欄位"),
    (csv_bytes([], headers=HEADERS + ["未知欄位"]), "不支援欄位"),
    (csv_bytes([ROW] * 501), "500 筆"),
    (b"a" * (MAX_CSV_BYTES + 1), "2 MB"),
    (b"\x00", "有效的 CSV"),
    (b"\xff\xff", "編碼"),
    (csv_bytes([]) + b'"unclosed', "格式不正確"),
])
def test_invalid_file_is_rejected(content, message):
    with pytest.raises(ValueError, match=message):
        parse_roster_csv(content)


@pytest.fixture
async def context(database_session):
    admin = User(email="csv-admin@example.com", display_name="管理員", password_hash="test", user_role=UserRole.ADMIN)
    owner = User(email="csv-user@example.com", display_name="一般會員", password_hash="test")
    database_session.add_all([admin, owner])
    await database_session.commit()
    async with api_test_context(database_session, [membership_router], settings=settings()) as client:
        yield client, database_session, auth_headers(admin), auth_headers(owner)


@pytest.mark.asyncio
async def test_preview_is_admin_only_and_never_writes(context):
    client, session, admin, user = context
    path = "/v1/admin/member-roster/import/preview"
    content = csv_bytes([ROW])
    assert (await client.post(path, content=content)).status_code == 401
    assert (await client.post(path, content=content, headers=user)).status_code == 403
    response = await client.post(path, content=content, headers=admin)
    assert response.status_code == 200, response.text
    assert response.json()["valid_count"] == 1
    assert response.json()["rows"][0]["entry"]["phone"] == "0912345678"
    assert await session.scalar(select(func.count(MemberRosterEntry.id))) == 0
    assert await session.scalar(select(func.count(AdminAudit.id))) == 0
    assert (await client.post(path, content=b"a" * (MAX_CSV_BYTES + 1), headers=admin)).status_code == 413


@pytest.mark.asyncio
async def test_atomic_import_encrypts_contacts_and_does_not_create_accounts_or_charges(context):
    client, session, admin, user = context
    preview = (await client.post("/v1/admin/member-roster/import/preview", content=csv_bytes([ROW, ["M-002", "第二位", "two@example.com", "0912345679", "", ""]]), headers=admin)).json()
    body = {"rows": [row["entry"] for row in preview["rows"]]}
    path = "/v1/admin/member-roster/import"
    assert (await client.post(path, json=body, headers=user)).status_code == 403
    response = await client.post(path, json=body, headers=admin)
    assert response.status_code == 201, response.text
    assert response.json() == {"imported_count": 2}
    entries = (await session.scalars(select(MemberRosterEntry).order_by(MemberRosterEntry.member_number))).all()
    cipher = pii_cipher_from_settings(settings())
    assert len(entries) == 2
    first = entries[0]
    assert "測試社員" not in first.legal_name_encrypted
    assert "member@example.com" not in first.email_encrypted
    assert cipher.decrypt_text(first.phone_encrypted, associated_data=roster_aad(first.id)) == "0912345678"
    assert first.claimed_user_id is None
    assert first.share_count == 1
    assert entries[1].share_capital_amount == 0
    for model in (Membership, MembershipCharge, PaymentAttempt, Refund):
        assert await session.scalar(select(func.count(model.id))) == 0
    assert await session.scalar(select(func.count(User.id))) == 2
    audit = (await session.scalars(select(AdminAudit))).one()
    assert audit.action == "member_roster.imported"
    assert audit.data == {"imported_count": 2, "member_numbers": ["M-001", "M-002"]}
    assert "member@example.com" not in str(audit.data)
    repeat = await client.post(path, json=body, headers=admin)
    assert repeat.status_code == 409
    assert await session.scalar(select(func.count(MemberRosterEntry.id))) == 2


@pytest.mark.asyncio
async def test_existing_member_and_preview_to_commit_conflict_never_partially_import(context):
    client, session, admin, _user = context
    preview = (await client.post("/v1/admin/member-roster/import/preview", content=csv_bytes([ROW]), headers=admin)).json()
    admin_id = await session.scalar(select(User.id).where(User.email == "csv-admin@example.com"))
    session.add(Membership(user_id=admin_id, member_number="M-001"))
    await session.commit()
    conflict = (await client.post("/v1/admin/member-roster/import/preview", content=csv_bytes([ROW]), headers=admin)).json()
    assert conflict["error_count"] == 1
    assert conflict["rows"][0]["entry"] is None
    rows = [preview["rows"][0]["entry"], {**preview["rows"][0]["entry"], "member_number": "M-NEW"}]
    response = await client.post("/v1/admin/member-roster/import", json={"rows": rows}, headers=admin)
    assert response.status_code == 409
    assert await session.scalar(select(func.count(MemberRosterEntry.id))) == 0


@pytest.mark.asyncio
async def test_commit_revalidates_and_rolls_back_integrity_conflicts(context, monkeypatch):
    client, session, admin, _user = context
    row = parse_roster_csv(csv_bytes([ROW]))[0].entry.model_dump(mode="json")
    path = "/v1/admin/member-roster/import"
    assert (await client.post(path, json={"rows": [row, {**row, "member_number": "ｍ－００１"}]}, headers=admin)).status_code == 422
    for changes in ({"share_count": True}, {"share_capital_amount": "1000"}, {"phone": "912345678"}, {"share_certificate_number": "EXTRA"}):
        assert (await client.post(path, json={"rows": [{**row, **changes}]}, headers=admin)).status_code == 422
    real_commit = session.commit

    async def conflicting_commit():
        await session.flush()
        raise IntegrityError("insert", {}, Exception("duplicate"))

    monkeypatch.setattr(session, "commit", conflicting_commit)
    response = await client.post(path, json={"rows": [row]}, headers=admin)
    assert response.status_code == 409
    monkeypatch.setattr(session, "commit", real_commit)
    assert await session.scalar(select(func.count(MemberRosterEntry.id))) == 0
    assert await session.scalar(select(func.count(AdminAudit.id))) == 0
