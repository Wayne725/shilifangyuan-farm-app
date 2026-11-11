from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from .admin_explanations import AdminExplanation


class AdmissionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AdmissionProfile(AdmissionInput):
    legal_name: str = Field(min_length=1, max_length=120)
    phone: str = Field(min_length=8, max_length=40)
    birth_date: date
    address: str = Field(min_length=1, max_length=500)
    emergency_contact: str = Field(min_length=1, max_length=240)
    contact_email: EmailStr
    gender: str | None = Field(default=None, max_length=40)
    place_of_origin: str | None = Field(default=None, max_length=120)
    occupation: str | None = Field(default=None, max_length=120)
    registered_address: str | None = Field(default=None, max_length=500)
    correspondence_address: str | None = Field(default=None, max_length=500)
    landline_phone: str | None = Field(default=None, max_length=40)
    line_id: str | None = Field(default=None, max_length=120)


class AdmissionRoundStart(AdmissionInput):
    idempotency_key: UUID
    expected_fee_version: int = Field(default=0, ge=0, strict=True)


class AdmissionVersionInput(AdmissionInput):
    expected_version: int = Field(ge=1, strict=True)


class AdmissionRoundUpdate(AdmissionVersionInput):
    profile: AdmissionProfile
    requested_share_count: int = Field(ge=5, le=100, strict=True)
    student_claimed: bool = Field(strict=True)
    discovery_channel: str = Field(min_length=1, max_length=100)
    discovery_other: str | None = Field(default=None, max_length=500)
    declaration_version: str = Field(min_length=1, max_length=40)
    privacy_version: str = Field(min_length=1, max_length=40)
    declaration_accepted: bool = Field(strict=True)
    privacy_accepted: bool = Field(strict=True)
    share_rules_understood: bool = Field(strict=True)
    payment_notice_understood: bool = Field(strict=True)

    @model_validator(mode="after")
    def require_explicit_consent(self):
        if not all((self.declaration_accepted, self.privacy_accepted,
                    self.share_rules_understood, self.payment_notice_understood)):
            raise ValueError("請本人確認志願書、個資使用、認股規則與後續繳費階段說明")
        return self


class AdmissionEducationConfirm(AdmissionVersionInput):
    material_version: str = Field(min_length=1, max_length=40)
    read_confirmed: bool = Field(strict=True)

    @field_validator("read_confirmed")
    @classmethod
    def require_confirmation(cls, value):
        if not value:
            raise ValueError("請完成教材閱讀後確認")
        return value


class AdmissionQuizAnswer(AdmissionInput):
    question_id: str = Field(min_length=1, max_length=40)
    option: str = Field(min_length=1, max_length=40)


class AdmissionQuizSubmit(AdmissionVersionInput):
    idempotency_key: UUID
    quiz_version: str = Field(min_length=1, max_length=40)
    answers: list[AdmissionQuizAnswer] = Field(min_length=10, max_length=10)


class AdmissionWithdraw(AdmissionVersionInput):
    reason: str | None = Field(default=None, max_length=2000)


class AdmissionReviewInput(AdmissionVersionInput):
    decision: Literal["approve", "request_supplement", "reject"]
    reason: AdminExplanation = Field(default="", max_length=2000)
    decision_reference: str | None = Field(default=None, max_length=200)
    student_verified: bool | None = Field(default=None, strict=True)


class AdmissionQuizFeedback(BaseModel):
    question_id: str
    selected_option: str
    correct_option: str
    correct: bool


class AdmissionQuizAttemptRead(BaseModel):
    id: str
    attempt_number: int
    quiz_version: str
    score: int
    passed: bool
    submitted_at: datetime
    settings_version: int
    results: list[AdmissionQuizFeedback]


class AdmissionEligibilityRead(BaseModel):
    allowed: bool
    remaining_attempts: int
    next_available_at: datetime | None
    reason: str | None
    settings_configured: bool


class AdmissionSubmissionRead(BaseModel):
    id: str
    revision_number: int
    submitted_at: datetime


class AdmissionReviewRead(BaseModel):
    id: str
    decision: str
    reason: str
    decision_reference: str | None
    student_verified: bool | None
    reviewed_at: datetime


class AdmissionRoundRead(BaseModel):
    id: str
    user_id: str
    round_number: int
    version: int
    flow_version: Literal["education-review-v1"]
    status: str
    profile: AdmissionProfile | None
    requested_share_count: int
    admission_fee_amount: int
    admission_fee_version: int
    share_unit_amount: int
    student_claimed: bool
    discovery_channel: str | None
    discovery_other: str | None
    declaration_version: str | None
    privacy_version: str | None
    consented_at: datetime | None
    material_version: str | None
    material_completed_at: datetime | None
    quiz_version: str
    quiz_eligibility: AdmissionEligibilityRead
    attempts: list[AdmissionQuizAttemptRead]
    submissions: list[AdmissionSubmissionRead]
    reviews: list[AdmissionReviewRead]
    submitted_at: datetime | None
    reviewed_at: datetime | None
    review_reason: str | None
    student_verified: bool | None
    decision_reference: str | None
    created_at: datetime
    updated_at: datetime
    can_edit: bool
    can_submit: bool
    can_withdraw: bool


class AdmissionHistoryRead(BaseModel):
    id: str
    round_number: int
    status: str
    created_at: datetime
    reviewed_at: datetime | None


class AdmissionMeRead(BaseModel):
    config: dict
    current_round: AdmissionRoundRead | None
    history: list[AdmissionHistoryRead]
    profile_defaults: AdmissionProfile | None
    has_legacy_application: bool = False
    can_start: bool
    start_blocked_reason: str | None


class AdmissionListRead(BaseModel):
    items: list[AdmissionRoundRead]
    total: int
    limit: int
    offset: int
