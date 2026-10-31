from collections import Counter
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from sqlalchemy import event

from app import membership_admission as admission
from app.integrations.pii_crypto import VersionedPIICipher, pii_cipher_from_settings
from app.models import (
    Membership, MembershipAdmissionQuizAttempt, MembershipAdmissionReview,
    MembershipAdmissionRound, MembershipAdmissionSubmission, MembershipStatus, User,
)
from tests.test_membership_admission import admission_settings, PROFILE


@pytest.fixture
async def admission_list_context(database_session):
    session, settings = database_session, admission_settings()
    cipher = pii_cipher_from_settings(settings)
    rows = []
    users = [User(id=str(UUID(int=index + 1)), email=f"public-{index:02d}@example.com",
                  display_name="Straße" if index == 7 else f"公眾別名{index:02d}", password_hash="test") for index in range(40)]
    session.add_all(users)
    await session.flush()
    statuses = ["submitted", "needs_supplement", "approved", "rejected", "draft", "withdrawn"]
    for index, user in enumerate(users):
        row = MembershipAdmissionRound(id=str(UUID(int=1000 + index)), user_id=user.id, round_number=1,
            idempotency_key=str(UUID(int=2000 + index)), status=statuses[index % len(statuses)],
            requested_share_count=10, created_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=index % 3))
        row.profile_encrypted = cipher.encrypt_json({**PROFILE, "legal_name": f"私密姓名{index:02d}",
            "contact_email": f"private-{index:02d}@example.com"}, associated_data=admission.round_aad(row))
        row.discovery_other_encrypted = cipher.encrypt_text("隔離介紹", associated_data=f"{admission.round_aad(row)}:discovery")
        rows.append(row)
        if index % 11 == 0:
            session.add(Membership(user_id=user.id, status=MembershipStatus.ACTIVE, member_number=f"LIST-{index}"))
    session.add_all(rows)
    await session.commit()
    for index in range(0, len(rows), 10):
        row = rows[index]
        attempts = [MembershipAdmissionQuizAttempt(round_id=row.id, attempt_number=number,
            idempotency_key=str(UUID(int=10000 + index * 2 + number)), quiz_version=row.quiz_version,
            answers=[], results=[], score=90 if number == 2 else 50, passed=number == 2,
            submitted_at=datetime(2026, 1, number, tzinfo=timezone.utc)) for number in (2, 1)]
        session.add_all(attempts)
        await session.flush()
        submission = MembershipAdmissionSubmission(round_id=row.id, revision_number=1,
            profile_encrypted=row.profile_encrypted, facts={}, quiz_attempt_id=attempts[0].id)
        session.add(submission)
        await session.flush()
        session.add(MembershipAdmissionReview(round_id=row.id, submission_id=submission.id,
            reviewer_id=users[0].id, decision="request_supplement", reason=f"補件{index}"))
    await session.commit()
    return session, settings, rows


@pytest.mark.asyncio
@pytest.mark.parametrize("status,q,offset", [
    ("all", "", 0), ("pending", "  ", 5), ("approved", "", 0),
    ("all", "私密姓名", 5), ("pending", "私密姓名", 0),
    ("all", "公眾別名", 5), ("all", "STRASSE", 0),
    ("all", "private-07@EXAMPLE.COM", 0), ("all", "public-07@example.com", 0),
    ("all", "no-match", 0), ("all", "", 100),
])
async def test_admission_list_keeps_search_order_and_full_response_with_bounded_queries(admission_list_context, monkeypatch, status, q, offset):
    session, settings, rows = admission_list_context
    ordered = sorted(rows, key=lambda row: (-row.created_at.timestamp(), row.id))
    if status != "all":
        allowed = {"submitted", "needs_supplement"} if status == "pending" else {status}
        ordered = [row for row in ordered if row.status in allowed]
    candidate_count = len(ordered)
    public_matches = set()
    if q.strip():
        term = q.strip().casefold()
        matches = []
        for row in ordered:
            index = int(UUID(row.user_id)) - 1
            values = ["Straße" if index == 7 else f"公眾別名{index:02d}", f"public-{index:02d}@example.com",
                      f"私密姓名{index:02d}", f"private-{index:02d}@example.com"]
            if any(term in value.casefold() for value in values[:2]):
                public_matches.add(row.id)
            if any(term in value.casefold() for value in values):
                matches.append(row)
        ordered = matches
    expected = [(await admission.read_round(session, row, settings)).model_dump() for row in ordered[offset:offset + 25]]
    counts = Counter()

    def count_query(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            counts["select"] += 1

    original = VersionedPIICipher.decrypt_json

    def count_decrypt(self, *args, **kwargs):
        counts["decrypt"] += 1
        return original(self, *args, **kwargs)

    engine = session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", count_query)
    monkeypatch.setattr(VersionedPIICipher, "decrypt_json", count_decrypt)
    try:
        result = await admission.list_rounds(session, settings, status=status, q=q, limit=25, offset=offset)
    finally:
        event.remove(engine, "before_cursor_execute", count_query)
    assert [item.model_dump() for item in result.items] == expected
    assert (result.total, result.limit, result.offset) == (len(ordered), 25, offset)
    assert counts["select"] <= 8, dict(counts)
    if not q.strip():
        assert counts["decrypt"] == len(expected), dict(counts)
    else:
        expected_decrypts = candidate_count - len(public_matches) + sum(row.id in public_matches for row in ordered[offset:offset + 25])
        assert counts["decrypt"] == expected_decrypts, dict(counts)
