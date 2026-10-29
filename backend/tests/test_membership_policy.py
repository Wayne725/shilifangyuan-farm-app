from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import pytest

from app.membership_policy import (
    MembershipPolicyError,
    MembershipRetryPolicy,
    get_public_membership_quiz,
    grade_membership_quiz,
    membership_payment_eligible,
    membership_quiz_eligibility,
    quote_membership_shares,
)


# Independently transcribed from the PDF's red answers, not the implementation's key.
OFFICIAL_ANSWERS = ("C", "B", "B", "C", "C", "D", "B", "C", "C", "B")
QUIZ_VERSION = "2026-10-29.v1"


def answers_with_correct_count(correct_count=10):
    return [
        {"question_id": f"q{index:02d}", "option": option if index <= correct_count else "A"}
        for index, option in enumerate(OFFICIAL_ANSWERS, start=1)
    ]


def test_public_quiz_exposes_complete_versioned_questions_without_answer_key():
    quiz = get_public_membership_quiz()
    assert set(quiz) == {"version", "question_count", "points_per_question", "pass_score", "questions"}
    assert quiz["version"] == QUIZ_VERSION
    assert (quiz["question_count"], quiz["points_per_question"], quiz["pass_score"]) == (10, 10, 80)
    assert [question["id"] for question in quiz["questions"]] == [f"q{index:02d}" for index in range(1, 11)]
    assert len(quiz["questions"]) == 10
    for question in quiz["questions"]:
        assert set(question) == {"id", "question", "options"}
        assert question["question"]
        assert set(question["options"]) == {"A", "B", "C", "D"}
    assert quiz["questions"][0]["options"]["C"] == "合作社採取「一人一票」的民主管理方式"
    assert quiz["questions"][6]["question"] == "社員加入十里方圓合作社時，每股股金為多少？"
    assert quiz["questions"][6]["options"]["B"] == "100 元"


def test_mutating_public_quiz_cannot_change_future_questions_or_grading():
    baseline = get_public_membership_quiz()
    edited = get_public_membership_quiz()
    edited["questions"][0]["options"].clear()
    edited["questions"][0]["options"]["X"] = "偽造選項"
    edited["questions"][1]["question"] = "偽造題目"
    edited["questions"].pop()
    edited["pass_score"] = 0
    assert get_public_membership_quiz() == baseline
    assert asdict(grade_membership_quiz(QUIZ_VERSION, answers_with_correct_count())) == {"score": 100, "passed": True}
    answers = answers_with_correct_count()
    answers[0]["option"] = "X"
    with pytest.raises(MembershipPolicyError, match="選項"):
        grade_membership_quiz(QUIZ_VERSION, answers)


@pytest.mark.parametrize("correct_count, expected_score, expected_passed", [
    (10, 100, True), (8, 80, True), (7, 70, False), (0, 0, False),
])
def test_grading_uses_official_answers_and_eighty_point_pass_boundary(correct_count, expected_score, expected_passed):
    result = grade_membership_quiz(QUIZ_VERSION, answers_with_correct_count(correct_count))
    assert asdict(result) == {"score": expected_score, "passed": expected_passed}


def test_grading_matches_question_ids_when_answers_are_reordered():
    result = grade_membership_quiz(QUIZ_VERSION, list(reversed(answers_with_correct_count())))
    assert result.score == 100
    assert result.passed is True


@pytest.mark.parametrize("version", ["2026-09-17.v0", None])
def test_grading_rejects_wrong_or_missing_version(version):
    with pytest.raises(MembershipPolicyError, match="版本"):
        grade_membership_quiz(version, answers_with_correct_count())


@pytest.mark.parametrize("count", [0, 9, 11])
def test_grading_rejects_missing_and_extra_answers(count):
    answers = (answers_with_correct_count() * 2)[:count]
    with pytest.raises(MembershipPolicyError, match="10 題"):
        grade_membership_quiz(QUIZ_VERSION, answers)


def test_repeating_one_question_cannot_replace_an_unanswered_question():
    answers = answers_with_correct_count()
    answers[-1] = answers[-2].copy()
    with pytest.raises(MembershipPolicyError, match="一次"):
        grade_membership_quiz(QUIZ_VERSION, answers)


@pytest.mark.parametrize("question_id", ["q11", 1, []])
def test_grading_rejects_unknown_question_ids(question_id):
    answers = answers_with_correct_count()
    answers[0]["question_id"] = question_id
    with pytest.raises(MembershipPolicyError, match="編號"):
        grade_membership_quiz(QUIZ_VERSION, answers)


@pytest.mark.parametrize("option", ["E", "c", None, []])
def test_grading_rejects_answers_outside_the_question_options(option):
    answers = answers_with_correct_count()
    answers[0]["option"] = option
    with pytest.raises(MembershipPolicyError, match="選項"):
        grade_membership_quiz(QUIZ_VERSION, answers)


@pytest.mark.parametrize("bad_answer", [None, {}, {"question_id": "q01"}])
def test_grading_rejects_malformed_answer_entries(bad_answer):
    answers = answers_with_correct_count()
    answers[0] = bad_answer
    with pytest.raises(MembershipPolicyError):
        grade_membership_quiz(QUIZ_VERSION, answers)


@pytest.mark.parametrize("student, share_count, expected_capital, expected_total", [
    (True, 5, 500, 1000), (True, 7, 700, 1200), (True, 10, 1000, 1500),
    (False, 10, 1000, 1500), (False, 40, 4000, 4500), (False, 100, 10000, 10500),
])
def test_share_quotes_include_admission_fee_amount_and_capital_at_inclusive_limits(
    student, share_count, expected_capital, expected_total,
):
    quote = quote_membership_shares(share_count, approved_full_time_student=student)
    assert asdict(quote) == {
        "share_count": share_count,
        "admission_fee_amount": 500,
        "share_unit_amount": 100,
        "share_capital_amount": expected_capital,
        "amount_total": expected_total,
    }


@pytest.mark.parametrize("student, share_count", [
    (True, 4), (True, 11), (False, 9), (False, 101), (True, -1), (False, 0),
])
def test_share_limits_require_approved_student_status(student, share_count):
    with pytest.raises(MembershipPolicyError, match="介於"):
        quote_membership_shares(share_count, approved_full_time_student=student)


@pytest.mark.parametrize("share_count", [True, 10.0, "10", None])
def test_share_quotes_reject_boolean_and_non_integer_counts(share_count):
    with pytest.raises(MembershipPolicyError, match="整數"):
        quote_membership_shares(share_count, approved_full_time_student=False)


@pytest.mark.parametrize("student_status", [None, "全職在校學生"])
def test_student_eligibility_requires_explicit_backend_boolean(student_status):
    with pytest.raises(MembershipPolicyError, match="已核定"):
        quote_membership_shares(10, approved_full_time_student=student_status)


@pytest.mark.parametrize(
    "lecture_completed, education_passed, formally_approved, approval_notice_issued, expected",
    [
        (False, False, False, False, False),
        (False, True, True, True, False),
        (True, False, True, True, False),
        (True, True, False, True, False),
        (True, True, True, False, False),
        (True, True, False, False, False),
        (True, True, True, True, True),
    ],
)
def test_payment_requires_all_four_confirmed_prerequisites(
    lecture_completed, education_passed, formally_approved, approval_notice_issued, expected,
):
    assert membership_payment_eligible(
        lecture_completed=lecture_completed,
        education_passed=education_passed,
        formally_approved=formally_approved,
        approval_notice_issued=approval_notice_issued,
    ) is expected


@pytest.mark.parametrize("flag, value", [
    ("lecture_completed", None), ("education_passed", 1),
    ("formally_approved", "approved"), ("approval_notice_issued", 1),
])
def test_payment_prerequisites_reject_truthy_unverified_values(flag, value):
    prerequisites = dict.fromkeys(
        ["lecture_completed", "education_passed", "formally_approved", "approval_notice_issued"], True,
    )
    prerequisites[flag] = value
    with pytest.raises(MembershipPolicyError, match="後端確認"):
        membership_payment_eligible(**prerequisites)


LAST_ATTEMPT_AT = datetime(2026, 10, 26, 4, tzinfo=timezone.utc)


@pytest.mark.parametrize("retry_limit, interval", [
    (-1, 30), (True, 30), (1.0, 30), (1, -1), (1, False), (1, "30"),
])
def test_retry_policy_requires_nonnegative_integer_settings(retry_limit, interval):
    with pytest.raises(MembershipPolicyError, match="非負整數"):
        MembershipRetryPolicy(retry_limit=retry_limit, retry_interval_minutes=interval)


@pytest.mark.parametrize("policy", [None, MembershipRetryPolicy(0, 0)])
def test_unconfigured_or_zero_retry_policy_allows_only_the_first_submission(policy):
    first = membership_quiz_eligibility(
        policy=policy, attempt_count=0, last_attempt_at=None, passed=False, now=LAST_ATTEMPT_AT,
    )
    assert asdict(first) == {"allowed": True, "remaining_attempts": 1, "next_available_at": None}
    repeat = membership_quiz_eligibility(
        policy=policy, attempt_count=1, last_attempt_at=LAST_ATTEMPT_AT,
        passed=False, now=LAST_ATTEMPT_AT + timedelta(days=30),
    )
    assert asdict(repeat) == {"allowed": False, "remaining_attempts": 0, "next_available_at": None}


@pytest.mark.parametrize("attempt_count, expected_remaining, expected_allowed", [
    (0, 3, True), (1, 2, True), (2, 1, True), (3, 0, False), (4, 0, False),
])
def test_retry_limit_counts_extra_submissions_and_zero_interval_allows_immediate_retry(
    attempt_count, expected_remaining, expected_allowed,
):
    result = membership_quiz_eligibility(
        policy=MembershipRetryPolicy(2, 0), attempt_count=attempt_count,
        last_attempt_at=LAST_ATTEMPT_AT if attempt_count else None,
        passed=False, now=LAST_ATTEMPT_AT,
    )
    assert asdict(result) == {
        "allowed": expected_allowed, "remaining_attempts": expected_remaining, "next_available_at": None,
    }


def test_retry_interval_blocks_until_the_exact_deadline_without_spending_an_attempt():
    deadline = datetime(2026, 10, 26, 4, 30, tzinfo=timezone.utc)
    waiting = membership_quiz_eligibility(
        policy=MembershipRetryPolicy(2, 30), attempt_count=1, last_attempt_at=LAST_ATTEMPT_AT,
        passed=False, now=deadline - timedelta(microseconds=1),
    )
    assert asdict(waiting) == {"allowed": False, "remaining_attempts": 2, "next_available_at": deadline}
    assert waiting.next_available_at.tzinfo is timezone.utc
    ready = membership_quiz_eligibility(
        policy=MembershipRetryPolicy(2, 30), attempt_count=1, last_attempt_at=LAST_ATTEMPT_AT,
        passed=False, now=deadline,
    )
    assert asdict(ready) == {"allowed": True, "remaining_attempts": 2, "next_available_at": None}


def test_passed_round_cannot_submit_again_even_with_retry_budget_and_elapsed_interval():
    result = membership_quiz_eligibility(
        policy=MembershipRetryPolicy(10, 30), attempt_count=1, last_attempt_at=LAST_ATTEMPT_AT,
        passed=True, now=LAST_ATTEMPT_AT + timedelta(hours=1),
    )
    assert asdict(result) == {"allowed": False, "remaining_attempts": 0, "next_available_at": None}


@pytest.mark.parametrize("attempt_count, last_attempt_at, passed", [
    (-1, LAST_ATTEMPT_AT, False),
    (True, LAST_ATTEMPT_AT, False),
    (1, None, False),
    (0, LAST_ATTEMPT_AT, False),
    (0, None, True),
    (1, LAST_ATTEMPT_AT, "passed"),
])
def test_retry_eligibility_rejects_inconsistent_or_unverified_round_records(attempt_count, last_attempt_at, passed):
    with pytest.raises(MembershipPolicyError):
        membership_quiz_eligibility(
            policy=MembershipRetryPolicy(1, 30), attempt_count=attempt_count,
            last_attempt_at=last_attempt_at, passed=passed, now=LAST_ATTEMPT_AT,
        )


@pytest.mark.parametrize("last_attempt_at, now", [
    (LAST_ATTEMPT_AT.replace(tzinfo=None), LAST_ATTEMPT_AT),
    (LAST_ATTEMPT_AT, LAST_ATTEMPT_AT.replace(tzinfo=None)),
    (LAST_ATTEMPT_AT.astimezone(timezone(timedelta(hours=8))), LAST_ATTEMPT_AT),
    (LAST_ATTEMPT_AT + timedelta(seconds=1), LAST_ATTEMPT_AT),
])
def test_retry_eligibility_requires_utc_timestamps_and_chronological_attempts(last_attempt_at, now):
    with pytest.raises(MembershipPolicyError):
        membership_quiz_eligibility(
            policy=MembershipRetryPolicy(1, 30), attempt_count=1,
            last_attempt_at=last_attempt_at, passed=False, now=now,
        )
