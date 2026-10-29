from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


MEMBERSHIP_QUIZ_VERSION = "2026-10-29.v1"
MEMBERSHIP_QUIZ_PASS_SCORE = 80
MEMBERSHIP_QUIZ_POINTS_PER_QUESTION = 10
MEMBERSHIP_ADMISSION_FEE = 500
MEMBERSHIP_SHARE_UNIT_AMOUNT = 100


class MembershipPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class MembershipQuizResult:
    score: int
    passed: bool


@dataclass(frozen=True)
class MembershipRetryPolicy:
    retry_limit: int
    retry_interval_minutes: int

    def __post_init__(self) -> None:
        if type(self.retry_limit) is not int or self.retry_limit < 0:
            raise MembershipPolicyError("額外重考次數必須是非負整數")
        if type(self.retry_interval_minutes) is not int or self.retry_interval_minutes < 0:
            raise MembershipPolicyError("重考間隔分鐘數必須是非負整數")


@dataclass(frozen=True)
class MembershipQuizEligibility:
    allowed: bool
    remaining_attempts: int
    next_available_at: datetime | None


@dataclass(frozen=True)
class MembershipShareQuote:
    share_count: int
    admission_fee_amount: int
    share_unit_amount: int
    share_capital_amount: int
    amount_total: int


@dataclass(frozen=True)
class _QuizQuestion:
    id: str
    question: str
    options: tuple[tuple[str, str], ...]
    correct_option: str


# 2026-10-29使用者提供的10題，答案已與入社說明PDF紅字核對；變更須更新版本。
_QUIZ_QUESTIONS = (
    _QuizQuestion(
        id="q01",
        question="合作社與一般公司的主要差異之一是什麼？",
        options=(
            ("A", "合作社只能由政府成立"),
            ("B", "合作社以資本多寡決定社員的投票權"),
            ("C", "合作社採取「一人一票」的民主管理方式"),
            ("D", "合作社不能進行任何商品交易"),
        ),
        correct_option="C",
    ),
    _QuizQuestion(
        id="q02",
        question="根據合作社七大原則，下列何者屬於合作社的基本原則？",
        options=(
            ("A", "股東利益最大化"),
            ("B", "社員的民主管理"),
            ("C", "資本額越高、投票權越多"),
            ("D", "追求企業獲利最大化"),
        ),
        correct_option="B",
    ),
    _QuizQuestion(
        id="q03",
        question="下列哪一項最符合「社員經濟參與」這項合作社原則？",
        options=(
            ("A", "由少數社員決定合作社的所有事務"),
            ("B", "社員透過認股出資，並依合作社規定參與合作社經濟活動"),
            ("C", "由外部投資人提供全部資金"),
            ("D", "由政府決定合作社的經營方向"),
        ),
        correct_option="B",
    ),
    _QuizQuestion(
        id="q04",
        question="十里方圓合作社希望透過「在地連結」達成什麼主要目標？",
        options=(
            ("A", "發展大型跨國企業"),
            ("B", "以資本投資為主要發展方式"),
            ("C", "連結在地生產者與社員的永續生活需求"),
            ("D", "取代所有傳統市場"),
        ),
        correct_option="C",
    ),
    _QuizQuestion(
        id="q05",
        question="社員推薦「好物／好體驗」時，合作社主要以什麼作為推薦準則？",
        options=(
            ("A", "商品價格最低"),
            ("B", "商品知名度最高"),
            ("C", "聯合國永續發展目標（SDGs）"),
            ("D", "廠商規模最大"),
        ),
        correct_option="C",
    ),
    _QuizQuestion(
        id="q06",
        question="下列何者「不是」十里方圓合作社社員的主要角色？",
        options=(
            ("A", "出資者"),
            ("B", "參與者"),
            ("C", "資源利用者"),
            ("D", "投資獲利者"),
        ),
        correct_option="D",
    ),
    _QuizQuestion(
        id="q07",
        question="社員加入十里方圓合作社時，每股股金為多少？",
        options=(
            ("A", "50 元"),
            ("B", "100 元"),
            ("C", "500 元"),
            ("D", "1,000 元"),
        ),
        correct_option="B",
    ),
    _QuizQuestion(
        id="q08",
        question="下列何者屬於十里方圓合作社社員應履行的義務？",
        options=(
            ("A", "必須購買合作社推薦的所有商品"),
            ("B", "必須每年參加所有合作社活動"),
            ("C", "與合作社忠實交易"),
            ("D", "必須擔任理事或監事"),
        ),
        correct_option="C",
    ),
    _QuizQuestion(
        id="q09",
        question="十里方圓合作社第一個合作社取物站於何時正式設立？",
        options=(
            ("A", "2022 年 3 月 3 日"),
            ("B", "2023 年 3 月 18 日"),
            ("C", "2023 年 10 月 3 日"),
            ("D", "2026 年 1 月"),
        ),
        correct_option="C",
    ),
    _QuizQuestion(
        id="q10",
        question="如果社員發現一項符合永續理念的在地農產品，希望推薦給合作社，最適合的做法是？",
        options=(
            ("A", "自行向其他社員販售"),
            ("B", "透過「社員推薦好物」系統提出推薦"),
            ("C", "直接要求合作社大量採購"),
            ("D", "先在社群媒體公開販售，再通知合作社"),
        ),
        correct_option="B",
    ),
)


def get_public_membership_quiz() -> dict:
    return {
        "version": MEMBERSHIP_QUIZ_VERSION,
        "question_count": len(_QUIZ_QUESTIONS),
        "points_per_question": MEMBERSHIP_QUIZ_POINTS_PER_QUESTION,
        "pass_score": MEMBERSHIP_QUIZ_PASS_SCORE,
        "questions": [
            {"id": question.id, "question": question.question, "options": dict(question.options)}
            for question in _QUIZ_QUESTIONS
        ],
    }


def grade_membership_quiz(
    version: str,
    answers: Sequence[Mapping[str, str]],
) -> MembershipQuizResult:
    """A passing result fulfills education only; formal review is still required."""
    if version != MEMBERSHIP_QUIZ_VERSION:
        raise MembershipPolicyError("測驗版本已變更，請重新讀取題目")
    if not isinstance(answers, Sequence) or len(answers) != len(_QUIZ_QUESTIONS):
        raise MembershipPolicyError("必須完整回答 10 題")

    questions = {question.id: question for question in _QUIZ_QUESTIONS}
    answered_ids = set()
    score = 0
    for answer in answers:
        if not isinstance(answer, Mapping):
            raise MembershipPolicyError("答案格式不正確")
        question_id = answer.get("question_id")
        if not isinstance(question_id, str) or question_id not in questions:
            raise MembershipPolicyError("題目編號不正確")
        if question_id in answered_ids:
            raise MembershipPolicyError("每題只能回答一次")
        option = answer.get("option")
        question = questions[question_id]
        if not isinstance(option, str) or option not in dict(question.options):
            raise MembershipPolicyError("答案必須是題目提供的選項")
        answered_ids.add(question_id)
        if option == question.correct_option:
            score += MEMBERSHIP_QUIZ_POINTS_PER_QUESTION

    return MembershipQuizResult(score=score, passed=score >= MEMBERSHIP_QUIZ_PASS_SCORE)


def _utc_datetime(value: datetime | None, name: str) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        raise MembershipPolicyError(f"{name}必須是含時區的 UTC 時間")
    return value.astimezone(timezone.utc)


def get_membership_quiz_feedback(
    version: str, answers: Sequence[Mapping[str, str]],
) -> list[dict]:
    """Call only after accepting a submission; public questions omit this feedback."""
    grade_membership_quiz(version, answers)
    selected = {answer["question_id"]: answer["option"] for answer in answers}
    return [
        {
            "question_id": question.id,
            "selected_option": selected[question.id],
            "correct_option": question.correct_option,
            "correct": selected[question.id] == question.correct_option,
        }
        for question in _QUIZ_QUESTIONS
    ]


def membership_quiz_eligibility(
    *,
    policy: MembershipRetryPolicy | None,
    attempt_count: int,
    last_attempt_at: datetime | None,
    passed: bool,
    now: datetime,
) -> MembershipQuizEligibility:
    """Use backend records from the current round; no pass carries over between rounds.

    retry_limit counts additional attempts beyond the first. remaining_attempts includes
    the first submission when unused. None policy allows only that first submission.
    next_available_at is UTC and present only while waiting for the retry interval.
    """
    if policy is not None and not isinstance(policy, MembershipRetryPolicy):
        raise MembershipPolicyError("重考設定必須由管理員確認")
    if type(attempt_count) is not int or attempt_count < 0:
        raise MembershipPolicyError("本輪已交卷次數必須是非負整數")
    if type(passed) is not bool:
        raise MembershipPolicyError("本輪合格狀態必須由後端確認")
    now = _utc_datetime(now, "目前時間")
    retry_limit = policy.retry_limit if policy else 0
    remaining_attempts = max(0, 1 + retry_limit - attempt_count)
    if attempt_count == 0:
        if last_attempt_at is not None or passed:
            raise MembershipPolicyError("本輪作答紀錄不一致")
        return MembershipQuizEligibility(True, remaining_attempts, None)

    last_attempt_at = _utc_datetime(last_attempt_at, "最後交卷時間")
    if last_attempt_at > now:
        raise MembershipPolicyError("最後交卷時間不得晚於目前時間")
    if passed or remaining_attempts == 0:
        return MembershipQuizEligibility(False, 0, None)

    retry_interval_minutes = policy.retry_interval_minutes if policy else 0
    next_available_at = last_attempt_at + timedelta(minutes=retry_interval_minutes)
    if now < next_available_at:
        return MembershipQuizEligibility(False, remaining_attempts, next_available_at)
    return MembershipQuizEligibility(True, remaining_attempts, None)


def quote_membership_shares(
    share_count: int,
    *,
    approved_full_time_student: bool,
) -> MembershipShareQuote:
    """Student eligibility must be verified by an administrator, not inferred from an occupation."""
    if type(approved_full_time_student) is not bool:
        raise MembershipPolicyError("必須提供已核定的全職在校學生資格")
    if type(share_count) is not int:
        raise MembershipPolicyError("認股股數必須是整數")
    minimum, maximum = (5, 10) if approved_full_time_student else (10, 100)
    if not minimum <= share_count <= maximum:
        raise MembershipPolicyError(f"認股股數須介於 {minimum} 至 {maximum} 股")

    share_capital_amount = share_count * MEMBERSHIP_SHARE_UNIT_AMOUNT
    return MembershipShareQuote(
        share_count=share_count,
        admission_fee_amount=MEMBERSHIP_ADMISSION_FEE,
        share_unit_amount=MEMBERSHIP_SHARE_UNIT_AMOUNT,
        share_capital_amount=share_capital_amount,
        amount_total=MEMBERSHIP_ADMISSION_FEE + share_capital_amount,
    )


def membership_payment_eligible(
    *,
    lecture_completed: bool,
    education_passed: bool,
    formally_approved: bool,
    approval_notice_issued: bool,
) -> bool:
    """Evaluate backend-verified prerequisites; callers still enforce payment authorization.

    An issued notice is a formally published, readable approval notice, not an email
    gateway acknowledgement. These flags must belong to the same application round.
    """
    prerequisites = (lecture_completed, education_passed, formally_approved, approval_notice_issued)
    if any(type(value) is not bool for value in prerequisites):
        raise MembershipPolicyError("付款前置條件必須由後端確認")
    return all(prerequisites)
