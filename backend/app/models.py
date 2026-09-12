from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


def enum_type(enum_class: type[enum.Enum], name: str) -> SqlEnum:
    return SqlEnum(
        enum_class,
        name=name,
        native_enum=False,
        values_callable=lambda values: [item.value for item in values],
        validate_strings=True,
    )


class UserRole(str, enum.Enum):
    CUSTOMER = "customer"
    ADMIN = "admin"


class MembershipType(str, enum.Enum):
    MEMBER = "member"
    TRAINEE = "trainee"
    NONMEMBER = "nonmember"


class MembershipApplicationStatus(str, enum.Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    NEEDS_SUPPLEMENT = "needs_supplement"
    APPROVED = "approved"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class MembershipStatus(str, enum.Enum):
    PENDING_PAYMENT = "pending_payment"
    TRAINEE = "trainee"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    RESIGNED = "resigned"
    TERMINATED = "terminated"


class MembershipDocumentType(str, enum.Enum):
    ID_FRONT = "id_front"
    ID_BACK = "id_back"
    SECONDARY = "secondary"


class MembershipDocumentStatus(str, enum.Enum):
    PENDING_UPLOAD = "pending_upload"
    CONFIRMED = "confirmed"
    DELETED = "deleted"


class MembershipChargeKind(str, enum.Enum):
    ADMISSION_FEE = "admission_fee"
    SHARE_CAPITAL = "share_capital"


class MembershipChargeStatus(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    REFUND_PENDING = "refund_pending"
    REFUNDED = "refunded"
    WAIVED = "waived"


class TaxType(str, enum.Enum):
    TAXABLE = "taxable"
    TAX_EXEMPT = "tax_exempt"


class ProductCategory(str, enum.Enum):
    SEASONAL_PRODUCE = "當季蔬果"
    RICE_AND_GRAINS = "米・雜糧"
    EGGS = "蛋品"
    PROCESSED = "加工品"
    DRINKS = "飲品"
    DAILY_GOODS = "生活用品"


class TargetType(str, enum.Enum):
    PRODUCT = "product"
    BUNDLE = "bundle"


class ProposalStatus(str, enum.Enum):
    PENDING_REVIEW = "pending_review"
    VOTING = "voting"
    ENDED_UNMET = "ended_unmet"
    CONVERSION_PENDING = "conversion_pending"
    CONVERTED = "converted"
    REJECTED = "rejected"
    EXPIRED_UNHANDLED = "expired_unhandled"


class GroupDecisionStatus(str, enum.Enum):
    RECRUITING = "recruiting"
    PENDING_CONFIRMATION = "pending_confirmation"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    FAILED_UNMET = "failed_unmet"
    EXPIRED_UNCONFIRMED = "expired_unconfirmed"
    CANCELLED = "cancelled"


class GroupIntakeStatus(str, enum.Enum):
    OPEN = "open"
    PAUSED = "paused"
    SETTLING = "settling"
    FULL = "full"
    CLOSED = "closed"


class OrderKind(str, enum.Enum):
    REGULAR = "regular"
    GROUP = "group"


class SalesChannel(str, enum.Enum):
    REGULAR = "regular"
    GROUP = "group"
    MEAL_PREORDER = "meal_preorder"


class FulfillmentMethod(str, enum.Enum):
    COOPERATIVE_PICKUP = "cooperative_pickup"
    EVENT_PICKUP = "event_pickup"
    ECPAY_LOGISTICS = "ecpay_logistics"


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    LATE_PAID_REFUND_REQUIRED = "late_paid_refund_required"
    REFUND_PENDING = "refund_pending"
    REFUNDED = "refunded"
    FAILED = "failed"
    EXPIRED = "expired"


class FulfillmentStatus(str, enum.Enum):
    PENDING_CONFIRMATION = "pending_confirmation"
    PREPARING = "preparing"
    READY_FOR_PICKUP = "ready_for_pickup"
    PICKED_UP = "picked_up"
    CANCELLED = "cancelled"


class FulfillmentState(str, enum.Enum):
    PENDING_CONFIRMATION = "pending_confirmation"
    PREPARING = "preparing"
    READY_FOR_PICKUP = "ready_for_pickup"
    PICKED_UP = "picked_up"
    AWAITING_SHIPMENT = "awaiting_shipment"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    NO_SHOW = "no_show"
    CANCELLED = "cancelled"


class ShippingTemperature(str, enum.Enum):
    AMBIENT = "ambient"
    CHILLED = "chilled"
    FROZEN = "frozen"


class ShippingChannel(str, enum.Enum):
    HOME_DELIVERY = "home_delivery"
    SEVEN_ELEVEN = "seven_eleven"
    FAMILY_MART = "family_mart"
    HILIFE = "hilife"


class ShipmentStatus(str, enum.Enum):
    DRAFT = "draft"
    SELECTION_PENDING = "selection_pending"
    READY_TO_CREATE = "ready_to_create"
    CREATED = "created"
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"
    EXCEPTION = "exception"
    CANCELLED = "cancelled"


class ActivityStatus(str, enum.Enum):
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    PUBLISHED = "published"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class ActivityRegistrationStatus(str, enum.Enum):
    REGISTERED = "registered"
    WAITLISTED = "waitlisted"
    CANCELLED = "cancelled"
    ATTENDED = "attended"
    NO_SHOW = "no_show"


class MemberProposalStatus(str, enum.Enum):
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    DISCUSSION = "discussion"
    VOTING = "voting"
    PASSED = "passed"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    CLOSED = "closed"


class MemberVoteChoice(str, enum.Enum):
    YES = "yes"
    NO = "no"
    ABSTAIN = "abstain"


class MemberProposalType(str, enum.Enum):
    RESOLUTION = "resolution"
    MULTIPLE_CHOICE = "multiple_choice"


class MeetingType(str, enum.Enum):
    GENERAL_ASSEMBLY = "general_assembly"
    AFFAIRS = "affairs"


class PointSourceType(str, enum.Enum):
    PURCHASE = "purchase"
    REFUND = "refund"
    WISH_LAUNCHED = "wish_launched"
    ACTIVITY = "activity"
    VOTE = "vote"
    ADMIN_ADJUSTMENT = "admin_adjustment"


class WishStatus(str, enum.Enum):
    SUBMITTED = "submitted"
    GATHERING = "gathering"
    SOURCING = "sourcing"
    LAUNCHED = "launched"
    DECLINED = "declined"


class MealEventStatus(str, enum.Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ORDERING_CLOSED = "ordering_closed"
    PICKUP_OPEN = "pickup_open"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class InvoiceStatus(str, enum.Enum):
    NOT_ELIGIBLE = "not_eligible"
    PENDING = "pending"
    ISSUED = "issued"
    FAILED = "failed"
    VOID_PENDING = "void_pending"
    VOIDED = "voided"


class InvoiceBuyerType(str, enum.Enum):
    PERSONAL = "personal"
    COMPANY = "company"


class InvoiceCarrierType(str, enum.Enum):
    ECPAY = "ecpay"
    CLOUD = "cloud"
    MOBILE_BARCODE = "mobile_barcode"


class InvoiceAllowanceStatus(str, enum.Enum):
    PENDING = "pending"
    ISSUED = "issued"
    FAILED = "failed"
    VOIDED = "voided"


class ReservationStatus(str, enum.Enum):
    ACTIVE = "active"
    CONSUMED = "consumed"
    RELEASED = "released"
    EXPIRED = "expired"


class RefundStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class OutboxStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class SupplierAccreditationStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    customer_number: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True, unique=True, index=True
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(String(255))
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    pending_member_claim: Mapped[bool] = mapped_column(Boolean, default=False)
    user_role: Mapped[UserRole] = mapped_column(
        enum_type(UserRole, "user_role"), default=UserRole.CUSTOMER
    )
    membership_type: Mapped[MembershipType] = mapped_column(
        enum_type(MembershipType, "membership_type"),
        default=MembershipType.NONMEMBER,
        comment="Legacy compatibility cache; authorization must use memberships.status.",
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    email_verified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    proposed_votes: Mapped[List["VoteProposal"]] = relationship(
        back_populates="proposer", foreign_keys="VoteProposal.proposer_id"
    )
    votes: Mapped[List["Vote"]] = relationship(back_populates="user")
    orders: Mapped[List["Order"]] = relationship(back_populates="user")
    notifications: Mapped[List["Notification"]] = relationship(
        back_populates="user"
    )
    email_verification_tokens: Mapped[List["EmailVerificationToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    password_reset_tokens: Mapped[List["PasswordResetToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    refresh_sessions: Mapped[List["RefreshSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    member_profile: Mapped[Optional["MemberProfile"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    membership_application: Mapped[Optional["MembershipApplication"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
        foreign_keys="MembershipApplication.user_id",
    )
    membership: Mapped[Optional["Membership"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    directory_entry: Mapped[Optional["MemberDirectoryEntry"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )


class EmailVerificationToken(Base):
    __tablename__ = "email_verification_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    user: Mapped[User] = relationship(back_populates="email_verification_tokens")


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    user: Mapped[User] = relationship(back_populates="password_reset_tokens")


class RefreshSession(Base):
    __tablename__ = "refresh_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    replaced_by_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("refresh_sessions.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    user: Mapped[User] = relationship(back_populates="refresh_sessions")


class MemberRosterEntry(Base):
    __tablename__ = "member_roster_entries"
    __table_args__ = (
        CheckConstraint(
            "share_capital_amount >= 0",
            name="share_capital_nonnegative",
        ),
        CheckConstraint("share_count >= 0", name="share_count_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    member_number: Mapped[str] = mapped_column(
        String(32), unique=True, index=True
    )
    legal_name_encrypted: Mapped[str] = mapped_column(Text)
    email_encrypted: Mapped[str] = mapped_column(Text)
    phone_encrypted: Mapped[str] = mapped_column(Text)
    encryption_key_version: Mapped[str] = mapped_column(String(32), default="v1")
    share_certificate_number: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )
    share_capital_amount: Mapped[int] = mapped_column(Integer, default=0)
    share_count: Mapped[int] = mapped_column(Integer, default=0)
    share_subscribed_on: Mapped[Optional[date]] = mapped_column(
        Date, nullable=True
    )
    share_paid_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    claimed_user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
        index=True,
    )
    claimed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    pending_claim_user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
        index=True,
    )
    pending_claim_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    claimed_user: Mapped[Optional[User]] = relationship(
        foreign_keys=[claimed_user_id]
    )
    pending_claim_user: Mapped[Optional[User]] = relationship(
        foreign_keys=[pending_claim_user_id]
    )


class MemberProfile(Base):
    __tablename__ = "member_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    legal_name_encrypted: Mapped[str] = mapped_column(Text)
    phone_encrypted: Mapped[str] = mapped_column(Text)
    birth_date_encrypted: Mapped[str] = mapped_column(Text)
    address_encrypted: Mapped[str] = mapped_column(Text)
    emergency_contact_encrypted: Mapped[str] = mapped_column(Text)
    identity_number_encrypted: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    gender_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    place_of_origin_encrypted: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    occupation_encrypted: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    registered_address_encrypted: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    correspondence_address_encrypted: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    landline_phone_encrypted: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    line_id_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    encryption_key_version: Mapped[str] = mapped_column(String(32), default="v1")
    consent_version: Mapped[str] = mapped_column(String(40))
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    user: Mapped[User] = relationship(back_populates="member_profile")


class MembershipApplication(Base):
    __tablename__ = "membership_applications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    status: Mapped[MembershipApplicationStatus] = mapped_column(
        enum_type(
            MembershipApplicationStatus,
            "membership_application_status",
        ),
        default=MembershipApplicationStatus.DRAFT,
        index=True,
    )
    submitted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reviewed_by_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    review_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    user: Mapped[User] = relationship(
        back_populates="membership_application", foreign_keys=[user_id]
    )
    reviewer: Mapped[Optional[User]] = relationship(foreign_keys=[reviewed_by_id])
    documents: Mapped[List["MembershipDocument"]] = relationship(
        back_populates="application", cascade="all, delete-orphan"
    )
    membership: Mapped[Optional["Membership"]] = relationship(
        back_populates="application", uselist=False
    )
    charges: Mapped[List["MembershipCharge"]] = relationship(
        back_populates="application"
    )


class MembershipDocument(Base):
    __tablename__ = "membership_documents"
    __table_args__ = (
        UniqueConstraint("application_id", "document_type"),
        CheckConstraint("size_bytes > 0", name="size_positive"),
        CheckConstraint("size_bytes <= 8388608", name="size_at_most_8mb"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(
        ForeignKey("membership_applications.id", ondelete="CASCADE"), index=True
    )
    document_type: Mapped[MembershipDocumentType] = mapped_column(
        enum_type(MembershipDocumentType, "membership_document_type")
    )
    status: Mapped[MembershipDocumentStatus] = mapped_column(
        enum_type(MembershipDocumentStatus, "membership_document_status"),
        default=MembershipDocumentStatus.PENDING_UPLOAD,
        index=True,
    )
    object_key: Mapped[str] = mapped_column(String(512), unique=True)
    content_type: Mapped[str] = mapped_column(String(80))
    size_bytes: Mapped[int] = mapped_column(Integer)
    checksum_sha256: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    application: Mapped[MembershipApplication] = relationship(
        back_populates="documents"
    )


class Membership(Base):
    __tablename__ = "memberships"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    application_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("membership_applications.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )
    member_number: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True, unique=True, index=True
    )
    trainee_number: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True, unique=True, index=True
    )
    status: Mapped[MembershipStatus] = mapped_column(
        enum_type(MembershipStatus, "membership_status"),
        default=MembershipStatus.PENDING_PAYMENT,
        index=True,
    )
    activated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    suspended_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    share_certificate_number: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )
    share_capital_amount: Mapped[int] = mapped_column(Integer, default=0)
    share_count: Mapped[int] = mapped_column(Integer, default=0)
    share_subscribed_on: Mapped[Optional[date]] = mapped_column(
        Date, nullable=True
    )
    share_paid_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    user: Mapped[User] = relationship(back_populates="membership")
    application: Mapped[Optional[MembershipApplication]] = relationship(
        back_populates="membership"
    )
    charges: Mapped[List["MembershipCharge"]] = relationship(
        back_populates="membership"
    )


class MembershipFeeSchedule(Base):
    __tablename__ = "membership_fee_schedules"
    __table_args__ = (
        UniqueConstraint("charge_kind", "effective_from"),
        CheckConstraint("amount >= 0", name="amount_nonnegative"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="effective_range_valid",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    charge_kind: Mapped[MembershipChargeKind] = mapped_column(
        enum_type(MembershipChargeKind, "membership_fee_kind"), index=True
    )
    amount: Mapped[int] = mapped_column(Integer)
    effective_from: Mapped[date] = mapped_column(Date, index=True)
    effective_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    charges: Mapped[List["MembershipCharge"]] = relationship(
        back_populates="fee_schedule"
    )


class MembershipCharge(Base):
    __tablename__ = "membership_charges"
    __table_args__ = (
        UniqueConstraint("application_id", "charge_kind"),
        CheckConstraint("amount >= 0", name="amount_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    application_id: Mapped[str] = mapped_column(
        ForeignKey("membership_applications.id", ondelete="RESTRICT"), index=True
    )
    membership_id: Mapped[str] = mapped_column(
        ForeignKey("memberships.id", ondelete="RESTRICT"), index=True
    )
    fee_schedule_id: Mapped[str] = mapped_column(
        ForeignKey("membership_fee_schedules.id", ondelete="RESTRICT"), index=True
    )
    charge_kind: Mapped[MembershipChargeKind] = mapped_column(
        enum_type(MembershipChargeKind, "membership_charge_kind"), index=True
    )
    amount: Mapped[int] = mapped_column(Integer)
    status: Mapped[MembershipChargeStatus] = mapped_column(
        enum_type(MembershipChargeStatus, "membership_charge_status"),
        default=MembershipChargeStatus.PENDING,
        index=True,
    )
    receipt_number: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True, unique=True
    )
    paid_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    refunded_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    user: Mapped[User] = relationship()
    application: Mapped[MembershipApplication] = relationship(
        back_populates="charges"
    )
    membership: Mapped[Membership] = relationship(back_populates="charges")
    fee_schedule: Mapped[MembershipFeeSchedule] = relationship(
        back_populates="charges"
    )
    payment_attempts: Mapped[List["PaymentAttempt"]] = relationship(
        back_populates="membership_charge"
    )
    refunds: Mapped[List["Refund"]] = relationship(
        back_populates="membership_charge"
    )


class MemberDirectoryEntry(Base):
    __tablename__ = "member_directory_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    nickname: Mapped[str] = mapped_column(String(80))
    avatar_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    expertise: Mapped[str] = mapped_column(String(240), default="")
    bio: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    user: Mapped[User] = relationship(back_populates="directory_entry")


class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    supplier_number: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True, unique=True, index=True
    )
    business_name: Mapped[str] = mapped_column(String(160), index=True)
    tax_id: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True, unique=True, index=True
    )
    responsible_person_encrypted: Mapped[str] = mapped_column(Text)
    contact_person_encrypted: Mapped[str] = mapped_column(Text)
    phone_encrypted: Mapped[str] = mapped_column(Text)
    email_encrypted: Mapped[str] = mapped_column(Text)
    line_id_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    settlement_terms: Mapped[str] = mapped_column(Text, default="")
    bank_account_encrypted: Mapped[str] = mapped_column(Text)
    encryption_key_version: Mapped[str] = mapped_column(String(32), default="v1")
    accredited_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    products: Mapped[List["Product"]] = relationship(back_populates="supplier")
    accreditations: Mapped[List["SupplierAccreditation"]] = relationship(
        back_populates="supplier",
        cascade="all, delete-orphan",
        order_by="SupplierAccreditation.reviewed_on",
    )
    documents: Mapped[List["SupplierDocument"]] = relationship(
        back_populates="supplier", cascade="all, delete-orphan"
    )


class SupplierAccreditation(Base):
    __tablename__ = "supplier_accreditations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    supplier_id: Mapped[str] = mapped_column(
        ForeignKey("suppliers.id", ondelete="CASCADE"), index=True
    )
    reviewed_on: Mapped[date] = mapped_column(Date, index=True)
    reviewer_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    process_notes: Mapped[str] = mapped_column(Text)
    status: Mapped[SupplierAccreditationStatus] = mapped_column(
        enum_type(
            SupplierAccreditationStatus,
            "supplier_accreditation_status",
        ),
        default=SupplierAccreditationStatus.PENDING,
        index=True,
    )
    result_notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    supplier: Mapped[Supplier] = relationship(back_populates="accreditations")
    reviewer: Mapped[User] = relationship()
    documents: Mapped[List["SupplierDocument"]] = relationship(
        back_populates="accreditation"
    )


class SupplierDocument(Base):
    __tablename__ = "supplier_documents"
    __table_args__ = (
        CheckConstraint("size_bytes > 0", name="size_positive"),
        CheckConstraint("size_bytes <= 8388608", name="size_at_most_8mb"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    supplier_id: Mapped[str] = mapped_column(
        ForeignKey("suppliers.id", ondelete="CASCADE"), index=True
    )
    accreditation_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("supplier_accreditations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    label: Mapped[str] = mapped_column(String(160))
    object_key: Mapped[str] = mapped_column(String(512), unique=True)
    content_type: Mapped[str] = mapped_column(String(80))
    size_bytes: Mapped[int] = mapped_column(Integer)
    checksum_sha256: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    supplier: Mapped[Supplier] = relationship(back_populates="documents")
    accreditation: Mapped[Optional[SupplierAccreditation]] = relationship(
        back_populates="documents"
    )


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("member_price >= 0", name="member_price_nonnegative"),
        CheckConstraint("nonmember_price >= 0", name="nonmember_price_nonnegative"),
        CheckConstraint(
            "member_price <= nonmember_price",
            name="member_price_not_above_nonmember_price",
        ),
        CheckConstraint("stock_quantity >= 0", name="stock_quantity_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    product_number: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True, unique=True, index=True
    )
    sku: Mapped[Optional[str]] = mapped_column(
        String(80), nullable=True, unique=True, index=True
    )
    supplier_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(80), index=True)
    unit: Mapped[str] = mapped_column(String(40))
    image_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    member_price: Mapped[int] = mapped_column(Integer)
    nonmember_price: Mapped[int] = mapped_column(Integer)
    stock_quantity: Mapped[int] = mapped_column(Integer, default=0)
    tax_type: Mapped[TaxType] = mapped_column(
        enum_type(TaxType, "tax_type"), default=TaxType.TAXABLE
    )
    can_ship: Mapped[bool] = mapped_column(Boolean, default=False)
    shipping_temperature: Mapped[Optional[ShippingTemperature]] = mapped_column(
        enum_type(ShippingTemperature, "product_shipping_temperature"),
        nullable=True,
    )
    allowed_shipping_channels: Mapped[List[str]] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    supplier: Mapped[Optional[Supplier]] = relationship(back_populates="products")

    @property
    def supplier_name(self) -> Optional[str]:
        supplier = self.__dict__.get("supplier")
        return supplier.business_name if supplier is not None else None


class GroupBundle(Base):
    __tablename__ = "group_bundles"
    __table_args__ = (
        CheckConstraint("member_price >= 0", name="member_price_nonnegative"),
        CheckConstraint("nonmember_price >= 0", name="nonmember_price_nonnegative"),
        CheckConstraint(
            "member_price <= nonmember_price",
            name="member_price_not_above_nonmember_price",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    image_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    member_price: Mapped[int] = mapped_column(Integer)
    nonmember_price: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    items: Mapped[List["GroupBundleItem"]] = relationship(
        back_populates="bundle",
        cascade="all, delete-orphan",
        order_by="GroupBundleItem.position",
    )


class GroupBundleItem(Base):
    __tablename__ = "group_bundle_items"
    __table_args__ = (
        UniqueConstraint("bundle_id", "product_id"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    bundle_id: Mapped[str] = mapped_column(
        ForeignKey("group_bundles.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), index=True
    )
    quantity: Mapped[int] = mapped_column(Integer)
    position: Mapped[int] = mapped_column(Integer, default=0)

    bundle: Mapped[GroupBundle] = relationship(back_populates="items")
    product: Mapped[Product] = relationship(lazy="joined")


class VoteProposal(Base):
    __tablename__ = "vote_proposals"
    __table_args__ = (
        CheckConstraint("threshold > 0", name="threshold_positive"),
        Index(
            "ix_vote_proposals_open_target",
            "target_type",
            "target_id",
            "status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    proposer_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    target_type: Mapped[TargetType] = mapped_column(
        enum_type(TargetType, "proposal_target_type")
    )
    target_id: Mapped[str] = mapped_column(String(36), index=True)
    target_name_snapshot: Mapped[str] = mapped_column(String(120))
    status: Mapped[ProposalStatus] = mapped_column(
        enum_type(ProposalStatus, "proposal_status"),
        default=ProposalStatus.PENDING_REVIEW,
        index=True,
    )
    threshold: Mapped[int] = mapped_column(Integer, default=10)
    deadline: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    conversion_deadline: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    frozen_vote_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    review_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reviewed_by_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    proposer: Mapped[User] = relationship(
        back_populates="proposed_votes", foreign_keys=[proposer_id]
    )
    reviewer: Mapped[Optional[User]] = relationship(foreign_keys=[reviewed_by_id])
    votes: Mapped[List["Vote"]] = relationship(
        back_populates="proposal", cascade="all, delete-orphan"
    )
    campaign: Mapped[Optional["GroupCampaign"]] = relationship(
        back_populates="source_proposal", uselist=False
    )


class Vote(Base):
    __tablename__ = "votes"
    __table_args__ = (
        UniqueConstraint("proposal_id", "user_id"),
        CheckConstraint("estimated_quantity > 0", name="estimated_quantity_positive"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    proposal_id: Mapped[str] = mapped_column(
        ForeignKey("vote_proposals.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    estimated_quantity: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    proposal: Mapped[VoteProposal] = relationship(back_populates="votes")
    user: Mapped[User] = relationship(back_populates="votes")


class GroupCampaign(Base):
    __tablename__ = "group_campaigns"
    __table_args__ = (
        CheckConstraint("member_price >= 0", name="member_price_nonnegative"),
        CheckConstraint("nonmember_price >= 0", name="nonmember_price_nonnegative"),
        CheckConstraint(
            "member_price <= nonmember_price",
            name="member_price_not_above_nonmember_price",
        ),
        CheckConstraint("min_paid_quantity > 0", name="min_paid_quantity_positive"),
        CheckConstraint("supply_cap > 0", name="supply_cap_positive"),
        CheckConstraint("per_user_cap > 0", name="per_user_cap_positive"),
        CheckConstraint(
            "min_paid_quantity <= supply_cap", name="minimum_within_supply_cap"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_proposal_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("vote_proposals.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )
    target_type: Mapped[TargetType] = mapped_column(
        enum_type(TargetType, "campaign_target_type")
    )
    target_id: Mapped[str] = mapped_column(String(36), index=True)
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    image_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    member_price: Mapped[int] = mapped_column(Integer)
    nonmember_price: Mapped[int] = mapped_column(Integer)
    min_paid_quantity: Mapped[int] = mapped_column(Integer)
    supply_cap: Mapped[int] = mapped_column(Integer)
    per_user_cap: Mapped[int] = mapped_column(Integer, default=5)
    paid_quantity: Mapped[int] = mapped_column(Integer, default=0)
    reserved_quantity: Mapped[int] = mapped_column(Integer, default=0)
    deadline: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )
    estimated_pickup_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True)
    )
    estimated_pickup_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    final_pickup_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decision_status: Mapped[GroupDecisionStatus] = mapped_column(
        enum_type(GroupDecisionStatus, "group_decision_status"),
        default=GroupDecisionStatus.RECRUITING,
        index=True,
    )
    intake_status: Mapped[GroupIntakeStatus] = mapped_column(
        enum_type(GroupIntakeStatus, "group_intake_status"),
        default=GroupIntakeStatus.OPEN,
        index=True,
    )
    core_locked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    confirmation_deadline: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    threshold_version: Mapped[int] = mapped_column(Integer, default=0)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejected_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    can_ship: Mapped[bool] = mapped_column(Boolean, default=False)
    shipping_temperature: Mapped[Optional[ShippingTemperature]] = mapped_column(
        enum_type(ShippingTemperature, "campaign_shipping_temperature"),
        nullable=True,
    )
    allowed_shipping_channels: Mapped[List[str]] = mapped_column(JSON, default=list)
    created_by_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    source_proposal: Mapped[Optional[VoteProposal]] = relationship(
        back_populates="campaign"
    )
    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])
    orders: Mapped[List["Order"]] = relationship(back_populates="group_campaign")


class Activity(Base):
    __tablename__ = "activities"
    __table_args__ = (
        CheckConstraint("capacity > 0", name="capacity_positive"),
        CheckConstraint("ends_at > starts_at", name="time_range_valid"),
        CheckConstraint(
            "registration_deadline <= starts_at",
            name="registration_before_start",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    created_by_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    image_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    location: Mapped[str] = mapped_column(String(240))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    registration_deadline: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )
    capacity: Mapped[int] = mapped_column(Integer)
    waitlist_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[ActivityStatus] = mapped_column(
        enum_type(ActivityStatus, "activity_status"),
        default=ActivityStatus.DRAFT,
        index=True,
    )
    reviewed_by_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    review_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])
    reviewed_by: Mapped[Optional[User]] = relationship(
        foreign_keys=[reviewed_by_id]
    )
    registrations: Mapped[List["ActivityRegistration"]] = relationship(
        back_populates="activity", cascade="all, delete-orphan"
    )


class ActivityRegistration(Base):
    __tablename__ = "activity_registrations"
    __table_args__ = (
        UniqueConstraint("activity_id", "user_id"),
        CheckConstraint("queue_position > 0", name="queue_position_positive"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    activity_id: Mapped[str] = mapped_column(
        ForeignKey("activities.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[ActivityRegistrationStatus] = mapped_column(
        enum_type(
            ActivityRegistrationStatus,
            "activity_registration_status",
        ),
        default=ActivityRegistrationStatus.REGISTERED,
        index=True,
    )
    queue_position: Mapped[int] = mapped_column(Integer)
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    checked_in_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    activity: Mapped[Activity] = relationship(back_populates="registrations")
    user: Mapped[User] = relationship()


class MemberProposal(Base):
    __tablename__ = "member_proposals"
    __table_args__ = (
        CheckConstraint("minimum_voters > 0", name="minimum_voters_positive"),
        CheckConstraint(
            "discussion_ends_at IS NULL OR voting_ends_at IS NULL "
            "OR voting_ends_at > discussion_ends_at",
            name="proposal_timeline_valid",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    created_by_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text)
    proposal_type: Mapped[MemberProposalType] = mapped_column(
        enum_type(MemberProposalType, "member_proposal_type"),
        default=MemberProposalType.RESOLUTION,
        index=True,
    )
    status: Mapped[MemberProposalStatus] = mapped_column(
        enum_type(MemberProposalStatus, "member_proposal_status"),
        default=MemberProposalStatus.DRAFT,
        index=True,
    )
    minimum_voters: Mapped[int] = mapped_column(Integer, default=10)
    discussion_ends_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    voting_ends_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    reviewed_by_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    review_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])
    reviewed_by: Mapped[Optional[User]] = relationship(
        foreign_keys=[reviewed_by_id]
    )
    comments: Mapped[List["MemberProposalComment"]] = relationship(
        back_populates="proposal", cascade="all, delete-orphan"
    )
    votes: Mapped[List["MemberProposalVote"]] = relationship(
        back_populates="proposal", cascade="all, delete-orphan"
    )
    options: Mapped[List["ProposalOption"]] = relationship(
        back_populates="proposal", cascade="all, delete-orphan"
    )


class ProposalOption(Base):
    __tablename__ = "proposal_options"
    __table_args__ = (UniqueConstraint("proposal_id", "position"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    proposal_id: Mapped[str] = mapped_column(
        ForeignKey("member_proposals.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(160))
    position: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    proposal: Mapped[MemberProposal] = relationship(back_populates="options")


class MemberProposalComment(Base):
    __tablename__ = "member_proposal_comments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    proposal_id: Mapped[str] = mapped_column(
        ForeignKey("member_proposals.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    body: Mapped[str] = mapped_column(Text)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    proposal: Mapped[MemberProposal] = relationship(back_populates="comments")
    user: Mapped[User] = relationship()


class MemberProposalVote(Base):
    __tablename__ = "member_proposal_votes"
    __table_args__ = (UniqueConstraint("proposal_id", "user_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    proposal_id: Mapped[str] = mapped_column(
        ForeignKey("member_proposals.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    choice: Mapped[Optional[MemberVoteChoice]] = mapped_column(
        enum_type(MemberVoteChoice, "member_vote_choice"), nullable=True, index=True
    )
    option_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("proposal_options.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    proposal: Mapped[MemberProposal] = relationship(back_populates="votes")
    user: Mapped[User] = relationship()
    option: Mapped[Optional[ProposalOption]] = relationship()


class Meal(Base):
    __tablename__ = "meals"
    __table_args__ = (CheckConstraint("price >= 0", name="price_nonnegative"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    image_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    price: Mapped[int] = mapped_column(Integer)
    tax_type: Mapped[TaxType] = mapped_column(
        enum_type(TaxType, "meal_tax_type"), default=TaxType.TAXABLE
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    offerings: Mapped[List["MealEventOffering"]] = relationship(
        back_populates="meal"
    )
    option_groups: Mapped[List["MealOptionGroup"]] = relationship(
        back_populates="meal",
        cascade="all, delete-orphan",
        order_by="MealOptionGroup.position",
    )


class MealOptionGroup(Base):
    __tablename__ = "meal_option_groups"
    __table_args__ = (
        UniqueConstraint("meal_id", "name"),
        CheckConstraint("min_selections >= 0", name="min_selections_nonnegative"),
        CheckConstraint("max_selections >= 1", name="max_selections_positive"),
        CheckConstraint(
            "max_selections >= min_selections",
            name="selection_range_valid",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    meal_id: Mapped[str] = mapped_column(
        ForeignKey("meals.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    min_selections: Mapped[int] = mapped_column(Integer, default=0)
    max_selections: Mapped[int] = mapped_column(Integer, default=1)
    position: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    meal: Mapped[Meal] = relationship(back_populates="option_groups")
    options: Mapped[List["MealOption"]] = relationship(
        back_populates="group",
        cascade="all, delete-orphan",
        order_by="MealOption.position",
    )


class MealOption(Base):
    __tablename__ = "meal_options"
    __table_args__ = (
        UniqueConstraint("group_id", "name"),
        CheckConstraint("price_delta >= 0", name="price_delta_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(
        ForeignKey("meal_option_groups.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    price_delta: Mapped[int] = mapped_column(Integer, default=0)
    position: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    group: Mapped[MealOptionGroup] = relationship(back_populates="options")


class MealEvent(Base):
    __tablename__ = "meal_events"
    __table_args__ = (
        CheckConstraint(
            "ordering_ends_at > ordering_starts_at",
            name="ordering_range_valid",
        ),
        CheckConstraint(
            "pickup_ends_at > pickup_starts_at",
            name="pickup_range_valid",
        ),
        CheckConstraint(
            "pickup_starts_at >= ordering_ends_at",
            name="pickup_after_ordering",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(160))
    location: Mapped[str] = mapped_column(String(240))
    ordering_starts_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )
    ordering_ends_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )
    pickup_starts_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )
    pickup_ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[MealEventStatus] = mapped_column(
        enum_type(MealEventStatus, "meal_event_status"),
        default=MealEventStatus.DRAFT,
        index=True,
    )
    created_by_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancellation_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    created_by: Mapped[User] = relationship()
    offerings: Mapped[List["MealEventOffering"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )
    orders: Mapped[List["Order"]] = relationship(back_populates="meal_event")


class MealEventOffering(Base):
    __tablename__ = "meal_event_offerings"
    __table_args__ = (
        UniqueConstraint("meal_event_id", "meal_id"),
        CheckConstraint("price >= 0", name="price_nonnegative"),
        CheckConstraint("capacity > 0", name="capacity_positive"),
        CheckConstraint("reserved_quantity >= 0", name="reserved_nonnegative"),
        CheckConstraint("paid_quantity >= 0", name="paid_nonnegative"),
        CheckConstraint(
            "reserved_quantity + paid_quantity <= capacity",
            name="allocation_within_capacity",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    meal_event_id: Mapped[str] = mapped_column(
        ForeignKey("meal_events.id", ondelete="CASCADE"), index=True
    )
    meal_id: Mapped[str] = mapped_column(
        ForeignKey("meals.id", ondelete="RESTRICT"), index=True
    )
    price: Mapped[int] = mapped_column(Integer)
    capacity: Mapped[int] = mapped_column(Integer)
    reserved_quantity: Mapped[int] = mapped_column(Integer, default=0)
    paid_quantity: Mapped[int] = mapped_column(Integer, default=0)
    position: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    event: Mapped[MealEvent] = relationship(back_populates="offerings")
    meal: Mapped[Meal] = relationship(back_populates="offerings")


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("amount_total >= 0", name="amount_total_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    order_kind: Mapped[OrderKind] = mapped_column(
        enum_type(OrderKind, "order_kind"),
        index=True,
        comment="Legacy compatibility field; new flows use sales_channel.",
    )
    sales_channel: Mapped[SalesChannel] = mapped_column(
        enum_type(SalesChannel, "sales_channel"),
        default=SalesChannel.REGULAR,
        index=True,
    )
    fulfillment_method: Mapped[FulfillmentMethod] = mapped_column(
        enum_type(FulfillmentMethod, "fulfillment_method"),
        default=FulfillmentMethod.COOPERATIVE_PICKUP,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    group_campaign_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("group_campaigns.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    meal_event_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("meal_events.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    membership_type_snapshot: Mapped[MembershipType] = mapped_column(
        enum_type(MembershipType, "order_membership_type")
    )
    amount_total: Mapped[int] = mapped_column(Integer)
    tax_amount: Mapped[int] = mapped_column(Integer, default=0)
    contact_email: Mapped[str] = mapped_column(String(320))
    invoice_buyer_type: Mapped[InvoiceBuyerType] = mapped_column(
        enum_type(InvoiceBuyerType, "invoice_buyer_type"),
        default=InvoiceBuyerType.PERSONAL,
    )
    invoice_buyer_tax_id: Mapped[Optional[str]] = mapped_column(
        String(10), nullable=True
    )
    invoice_buyer_name: Mapped[Optional[str]] = mapped_column(
        String(60), nullable=True
    )
    invoice_buyer_email: Mapped[Optional[str]] = mapped_column(
        String(320), nullable=True
    )
    invoice_carrier_type: Mapped[InvoiceCarrierType] = mapped_column(
        enum_type(InvoiceCarrierType, "invoice_carrier_type"),
        default=InvoiceCarrierType.CLOUD,
    )
    invoice_carrier_value: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )
    invoice_provider_context: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON, nullable=True
    )
    fulfillment_status: Mapped[FulfillmentStatus] = mapped_column(
        enum_type(FulfillmentStatus, "fulfillment_status"),
        default=FulfillmentStatus.PENDING_CONFIRMATION,
        index=True,
        comment="Legacy compatibility cache; OrderFulfillment.status is authoritative.",
    )
    payment_status: Mapped[PaymentStatus] = mapped_column(
        enum_type(PaymentStatus, "payment_status"),
        default=PaymentStatus.PENDING,
        index=True,
    )
    invoice_status: Mapped[InvoiceStatus] = mapped_column(
        enum_type(InvoiceStatus, "invoice_status"),
        default=InvoiceStatus.NOT_ELIGIBLE,
        index=True,
    )
    paid_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancellation_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    user: Mapped[User] = relationship(back_populates="orders")
    group_campaign: Mapped[Optional[GroupCampaign]] = relationship(
        back_populates="orders"
    )
    meal_event: Mapped[Optional[MealEvent]] = relationship(back_populates="orders")
    items: Mapped[List["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    payment_attempts: Mapped[List["PaymentAttempt"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    reservations: Mapped[List["InventoryReservation"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    refunds: Mapped[List["Refund"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    invoice: Mapped[Optional["Invoice"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", uselist=False
    )
    fulfillment: Mapped[Optional["OrderFulfillment"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", uselist=False
    )


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price >= 0", name="unit_price_nonnegative"),
        CheckConstraint("subtotal >= 0", name="subtotal_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), index=True
    )
    source_product_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )
    source_bundle_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("group_bundles.id", ondelete="SET NULL"), nullable=True
    )
    source_meal_offering_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("meal_event_offerings.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    product_name: Mapped[str] = mapped_column(String(120))
    unit_label: Mapped[str] = mapped_column(String(40))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[int] = mapped_column(Integer)
    subtotal: Mapped[int] = mapped_column(Integer)
    tax_type: Mapped[TaxType] = mapped_column(enum_type(TaxType, "order_tax_type"))

    order: Mapped[Order] = relationship(back_populates="items")
    selected_options: Mapped[List["OrderItemOption"]] = relationship(
        back_populates="order_item",
        cascade="all, delete-orphan",
        order_by="OrderItemOption.position",
    )


class OrderItemOption(Base):
    __tablename__ = "order_item_options"
    __table_args__ = (
        CheckConstraint("price_delta >= 0", name="price_delta_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_item_id: Mapped[str] = mapped_column(
        ForeignKey("order_items.id", ondelete="CASCADE"), index=True
    )
    source_meal_option_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("meal_options.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    group_name: Mapped[str] = mapped_column(String(120))
    option_name: Mapped[str] = mapped_column(String(120))
    price_delta: Mapped[int] = mapped_column(Integer, default=0)
    position: Mapped[int] = mapped_column(Integer, default=0)

    order_item: Mapped[OrderItem] = relationship(back_populates="selected_options")


class OrderFulfillment(Base):
    __tablename__ = "order_fulfillments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), unique=True, index=True
    )
    pickup_location_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("pickup_locations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    method: Mapped[FulfillmentMethod] = mapped_column(
        enum_type(FulfillmentMethod, "order_fulfillment_method"), index=True
    )
    status: Mapped[FulfillmentState] = mapped_column(
        enum_type(FulfillmentState, "order_fulfillment_state"),
        default=FulfillmentState.PENDING_CONFIRMATION,
        index=True,
    )
    pickup_location: Mapped[Optional[str]] = mapped_column(
        String(240), nullable=True
    )
    pickup_starts_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    pickup_ends_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    pickup_code: Mapped[Optional[str]] = mapped_column(
        String(6), nullable=True, unique=True, index=True
    )
    pickup_qr_token_hash: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, unique=True
    )
    recipient_name_encrypted: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    recipient_phone_encrypted: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    shipping_address_encrypted: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    encryption_key_version: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True
    )
    fulfilled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    order: Mapped[Order] = relationship(back_populates="fulfillment")
    pickup_location_record: Mapped[Optional["PickupLocation"]] = relationship(
        back_populates="fulfillments"
    )
    shipment: Mapped[Optional["Shipment"]] = relationship(
        back_populates="fulfillment", cascade="all, delete-orphan", uselist=False
    )


class PickupLocation(Base):
    __tablename__ = "pickup_locations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    address: Mapped[str] = mapped_column(String(500), default="")
    instructions: Mapped[str] = mapped_column(Text, default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    fulfillments: Mapped[List[OrderFulfillment]] = relationship(
        back_populates="pickup_location_record"
    )


class Shipment(Base):
    __tablename__ = "shipments"
    __table_args__ = (
        CheckConstraint("shipping_fee >= 0", name="shipping_fee_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_fulfillment_id: Mapped[str] = mapped_column(
        ForeignKey("order_fulfillments.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    channel: Mapped[ShippingChannel] = mapped_column(
        enum_type(ShippingChannel, "shipment_channel"), index=True
    )
    temperature: Mapped[ShippingTemperature] = mapped_column(
        enum_type(ShippingTemperature, "shipment_temperature"), index=True
    )
    status: Mapped[ShipmentStatus] = mapped_column(
        enum_type(ShipmentStatus, "shipment_status"),
        default=ShipmentStatus.DRAFT,
        index=True,
    )
    shipping_fee: Mapped[int] = mapped_column(Integer, default=0)
    ecpay_logistics_id: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True, unique=True, index=True
    )
    ecpay_booking_note: Mapped[Optional[str]] = mapped_column(
        String(80), nullable=True
    )
    tracking_number: Mapped[Optional[str]] = mapped_column(
        String(80), nullable=True, index=True
    )
    # Lets the unauthenticated store-selection page resolve one shipment; the
    # browser navigation to ECPay cannot carry an Authorization header.
    selection_token_hash: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )
    selection_token_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    provider_payload: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    fulfillment: Mapped[OrderFulfillment] = relationship(back_populates="shipment")


class ShippingRate(Base):
    __tablename__ = "shipping_rates"
    __table_args__ = (
        UniqueConstraint("channel", "temperature", "effective_from"),
        CheckConstraint("fee >= 0", name="fee_nonnegative"),
        CheckConstraint("free_shipping_threshold >= 0", name="threshold_nonnegative"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="effective_range_valid",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    channel: Mapped[ShippingChannel] = mapped_column(
        enum_type(ShippingChannel, "shipping_rate_channel"), index=True
    )
    temperature: Mapped[ShippingTemperature] = mapped_column(
        enum_type(ShippingTemperature, "shipping_rate_temperature"), index=True
    )
    fee: Mapped[int] = mapped_column(Integer)
    free_shipping_threshold: Mapped[int] = mapped_column(Integer, default=1500)
    effective_from: Mapped[date] = mapped_column(Date, index=True)
    effective_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class InventoryReservation(Base):
    __tablename__ = "inventory_reservations"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), index=True
    )
    group_campaign_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("group_campaigns.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    source_product_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=True, index=True
    )
    source_meal_offering_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("meal_event_offerings.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    payment_attempt_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("payment_attempts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    quantity: Mapped[int] = mapped_column(Integer)
    status: Mapped[ReservationStatus] = mapped_column(
        enum_type(ReservationStatus, "reservation_status"),
        default=ReservationStatus.ACTIVE,
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )
    released_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    order: Mapped[Order] = relationship(back_populates="reservations")
    payment_attempt: Mapped[Optional["PaymentAttempt"]] = relationship(
        back_populates="reservations"
    )


class PaymentAttempt(Base):
    __tablename__ = "payment_attempts"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "provider_trade_no",
            name="uq_payment_attempts_provider_trade_no",
        ),
        CheckConstraint("amount >= 0", name="amount_nonnegative"),
        CheckConstraint(
            "(order_id IS NOT NULL AND membership_charge_id IS NULL) OR "
            "(order_id IS NULL AND membership_charge_id IS NOT NULL)",
            name="exactly_one_payment_subject",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=True, index=True
    )
    membership_charge_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("membership_charges.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    provider: Mapped[str] = mapped_column(
        String(20), default="ecpay", index=True
    )
    merchant_trade_no: Mapped[str] = mapped_column(
        String(50), unique=True, index=True
    )
    provider_trade_no: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, index=True
    )
    amount: Mapped[int] = mapped_column(Integer)
    status: Mapped[PaymentStatus] = mapped_column(
        enum_type(PaymentStatus, "payment_attempt_status"),
        default=PaymentStatus.PENDING,
        index=True,
    )
    checkout_payload: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    provider_response: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    next_reconcile_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )
    paid_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    order: Mapped[Optional[Order]] = relationship(back_populates="payment_attempts")
    membership_charge: Mapped[Optional[MembershipCharge]] = relationship(
        back_populates="payment_attempts"
    )
    reservations: Mapped[List[InventoryReservation]] = relationship(
        back_populates="payment_attempt"
    )


class Refund(Base):
    __tablename__ = "refunds"
    __table_args__ = (
        UniqueConstraint(
            "payment_attempt_id",
            name="uq_refunds_payment_attempt_id",
        ),
        UniqueConstraint(
            "provider",
            "provider_refund_id",
            name="uq_refunds_provider_refund_id",
        ),
        CheckConstraint("amount >= 0", name="amount_nonnegative"),
        CheckConstraint(
            "(order_id IS NOT NULL AND membership_charge_id IS NULL) OR "
            "(order_id IS NULL AND membership_charge_id IS NOT NULL)",
            name="exactly_one_refund_subject",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=True, index=True
    )
    membership_charge_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("membership_charges.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    payment_attempt_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("payment_attempts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    provider: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True, index=True
    )
    provider_refund_id: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, index=True
    )
    provider_response: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    amount: Mapped[int] = mapped_column(Integer)
    status: Mapped[RefundStatus] = mapped_column(
        enum_type(RefundStatus, "refund_status"),
        default=RefundStatus.PENDING,
        index=True,
    )
    reason: Mapped[str] = mapped_column(Text)
    requested_by_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    order: Mapped[Optional[Order]] = relationship(back_populates="refunds")
    membership_charge: Mapped[Optional[MembershipCharge]] = relationship(
        back_populates="refunds"
    )
    payment_attempt: Mapped[Optional[PaymentAttempt]] = relationship()
    requested_by: Mapped[User] = relationship()


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), unique=True, index=True
    )
    relate_number: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(40), default="ecpay", index=True)
    payment_attempt_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("payment_attempts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    invoice_number: Mapped[Optional[str]] = mapped_column(
        String(24), nullable=True, unique=True
    )
    invoice_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    random_number: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    provider_status: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True, index=True
    )
    buyer_type: Mapped[InvoiceBuyerType] = mapped_column(
        enum_type(InvoiceBuyerType, "invoice_record_buyer_type"),
        default=InvoiceBuyerType.PERSONAL,
    )
    buyer_tax_id: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    buyer_name: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    buyer_email: Mapped[Optional[str]] = mapped_column(String(320), nullable=True)
    carrier_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    carrier_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    sales_amount: Mapped[int] = mapped_column(Integer, default=0)
    tax_amount: Mapped[int] = mapped_column(Integer, default=0)
    total_amount: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[InvoiceStatus] = mapped_column(
        enum_type(InvoiceStatus, "invoice_record_status"),
        default=InvoiceStatus.PENDING,
        index=True,
    )
    provider_request: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    provider_response: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    provider_context: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    issued_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    voided_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    void_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    void_source: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    order: Mapped[Order] = relationship(back_populates="invoice")
    payment_attempt: Mapped[Optional[PaymentAttempt]] = relationship()
    items: Mapped[List["InvoiceItem"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )
    allowances: Mapped[List["InvoiceAllowance"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )


class InvoiceItem(Base):
    __tablename__ = "invoice_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price >= 0", name="unit_price_nonnegative"),
        CheckConstraint("amount >= 0", name="amount_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    invoice_id: Mapped[str] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    item_name: Mapped[str] = mapped_column(String(500))
    quantity: Mapped[int] = mapped_column(Integer)
    unit: Mapped[str] = mapped_column(String(6), default="件")
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    tax_type: Mapped[str] = mapped_column(String(1))
    sequence_number: Mapped[int] = mapped_column(Integer)

    invoice: Mapped[Invoice] = relationship(back_populates="items")


class InvoiceAllowance(Base):
    __tablename__ = "invoice_allowances"
    __table_args__ = (
        CheckConstraint("sales_amount >= 0", name="sales_amount_nonnegative"),
        CheckConstraint("tax_amount >= 0", name="tax_amount_nonnegative"),
        CheckConstraint("total_amount >= 0", name="total_amount_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    invoice_id: Mapped[str] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    sales_return_number: Mapped[str] = mapped_column(
        String(50), unique=True, index=True
    )
    allowance_number: Mapped[Optional[str]] = mapped_column(
        String(16), nullable=True, unique=True
    )
    allowance_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[InvoiceAllowanceStatus] = mapped_column(
        enum_type(InvoiceAllowanceStatus, "invoice_allowance_status"),
        default=InvoiceAllowanceStatus.PENDING,
        index=True,
    )
    sales_amount: Mapped[int] = mapped_column(Integer)
    tax_amount: Mapped[int] = mapped_column(Integer, default=0)
    total_amount: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)
    provider_response: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    issued_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    voided_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    invoice: Mapped[Invoice] = relationship(back_populates="allowances")
    items: Mapped[List["InvoiceAllowanceItem"]] = relationship(
        back_populates="allowance", cascade="all, delete-orphan"
    )


class InvoiceAllowanceItem(Base):
    __tablename__ = "invoice_allowance_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price >= 0", name="unit_price_nonnegative"),
        CheckConstraint("amount >= 0", name="amount_nonnegative"),
        CheckConstraint("tax_amount >= 0", name="tax_amount_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    allowance_id: Mapped[str] = mapped_column(
        ForeignKey("invoice_allowances.id", ondelete="CASCADE"), index=True
    )
    invoice_item_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("invoice_items.id", ondelete="SET NULL"), nullable=True
    )
    item_name: Mapped[str] = mapped_column(String(500))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    tax_amount: Mapped[int] = mapped_column(Integer, default=0)
    tax_type: Mapped[str] = mapped_column(String(1))
    sequence_number: Mapped[int] = mapped_column(Integer)

    allowance: Mapped[InvoiceAllowance] = relationship(back_populates="items")


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text)
    data: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    read_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    user: Mapped[User] = relationship(back_populates="notifications")


class OutboxEvent(Base):
    __tablename__ = "outbox_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    aggregate_type: Mapped[str] = mapped_column(String(80))
    aggregate_id: Mapped[str] = mapped_column(String(36), index=True)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[OutboxStatus] = mapped_column(
        enum_type(OutboxStatus, "outbox_status"),
        default=OutboxStatus.PENDING,
        index=True,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    processed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class ExternalEvent(Base):
    __tablename__ = "external_events"
    __table_args__ = (
        UniqueConstraint("provider", "external_event_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    external_event_key: Mapped[str] = mapped_column(String(160))
    event_type: Mapped[str] = mapped_column(String(80))
    payload: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    processed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    processed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class AdminAudit(Base):
    __tablename__ = "admin_audits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    action: Mapped[str] = mapped_column(String(80), index=True)
    aggregate_type: Mapped[str] = mapped_column(String(80))
    aggregate_id: Mapped[str] = mapped_column(String(36), index=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    data: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    actor: Mapped[User] = relationship()


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_by_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class FiscalYear(Base):
    __tablename__ = "fiscal_years"
    __table_args__ = (
        CheckConstraint("ends_on >= starts_on", name="fiscal_year_dates_valid"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    label: Mapped[str] = mapped_column(String(80), unique=True)
    starts_on: Mapped[date] = mapped_column(Date, index=True)
    ends_on: Mapped[date] = mapped_column(Date, index=True)
    reserve_percentage: Mapped[int] = mapped_column(Integer, default=50)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SurplusLedger(Base):
    __tablename__ = "surplus_ledgers"
    __table_args__ = (
        UniqueConstraint("fiscal_year_id"),
        CheckConstraint("total_revenue >= 0", name="surplus_revenue_nonnegative"),
        CheckConstraint("total_cost >= 0", name="surplus_cost_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    fiscal_year_id: Mapped[str] = mapped_column(
        ForeignKey("fiscal_years.id", ondelete="RESTRICT"), index=True
    )
    total_revenue: Mapped[int] = mapped_column(Integer)
    total_cost: Mapped[int] = mapped_column(Integer)
    total_surplus: Mapped[int] = mapped_column(Integer)
    reserve_amount: Mapped[int] = mapped_column(Integer)
    distributable_surplus: Mapped[int] = mapped_column(Integer)
    contribution_basis: Mapped[str] = mapped_column(String(20), default="paid_orders")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SurplusDistribution(Base):
    __tablename__ = "surplus_distributions"
    __table_args__ = (UniqueConstraint("fiscal_year_id", "member_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    fiscal_year_id: Mapped[str] = mapped_column(
        ForeignKey("fiscal_years.id", ondelete="RESTRICT"), index=True
    )
    member_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    contribution_amount: Mapped[int] = mapped_column(Integer)
    contribution_basis_points: Mapped[int] = mapped_column(Integer)
    distribution_amount: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EducationLecture(Base):
    __tablename__ = "education_lectures"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EducationQuestion(Base):
    __tablename__ = "education_questions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    lecture_id: Mapped[str] = mapped_column(
        ForeignKey("education_lectures.id", ondelete="CASCADE"), index=True
    )
    prompt: Mapped[str] = mapped_column(Text)
    options: Mapped[List[str]] = mapped_column(JSON)
    correct_option: Mapped[int] = mapped_column(Integer)
    explanation: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class EducationAttempt(Base):
    __tablename__ = "education_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    question_ids: Mapped[List[str]] = mapped_column(JSON)
    answers: Mapped[Dict[str, int]] = mapped_column(JSON, default=dict)
    score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    passed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PointAccount(Base):
    __tablename__ = "point_accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PointTransaction(Base):
    __tablename__ = "point_transactions"
    __table_args__ = (
        CheckConstraint("amount != 0", name="point_amount_nonzero"),
        UniqueConstraint("account_id", "source_type", "reference_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("point_accounts.id", ondelete="RESTRICT"), index=True
    )
    amount: Mapped[int] = mapped_column(Integer)
    source_type: Mapped[PointSourceType] = mapped_column(
        enum_type(PointSourceType, "point_source_type"), index=True
    )
    reference_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    note: Mapped[str] = mapped_column(String(240), default="")
    created_by_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Meeting(Base):
    __tablename__ = "meetings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    meeting_type: Mapped[MeetingType] = mapped_column(
        enum_type(MeetingType, "meeting_type"), index=True
    )
    title: Mapped[str] = mapped_column(String(160))
    agenda: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    location: Mapped[str] = mapped_column(String(240), default="")
    created_by_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MeetingAttendance(Base):
    __tablename__ = "meeting_attendances"
    __table_args__ = (UniqueConstraint("meeting_id", "member_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    member_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    attended: Mapped[bool] = mapped_column(Boolean, default=True)
    checked_in_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class MeetingResolution(Base):
    __tablename__ = "meeting_resolutions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    member_proposal_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("member_proposals.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(160))
    resolution_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Wish(Base):
    __tablename__ = "wishes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    proposer_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text)
    expected_price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reference_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    status: Mapped[WishStatus] = mapped_column(enum_type(WishStatus, "wish_status"), default=WishStatus.SUBMITTED, index=True)
    launched_product_id: Mapped[Optional[str]] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    launched_campaign_id: Mapped[Optional[str]] = mapped_column(ForeignKey("group_campaigns.id", ondelete="SET NULL"), nullable=True)
    admin_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class WishSupport(Base):
    __tablename__ = "wish_supports"
    __table_args__ = (UniqueConstraint("wish_id", "user_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    wish_id: Mapped[str] = mapped_column(ForeignKey("wishes.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BadgeDefinition(Base):
    __tablename__ = "badge_definitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    key: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text)
    rule: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)


class MemberBadge(Base):
    __tablename__ = "member_badges"
    __table_args__ = (UniqueConstraint("user_id", "badge_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    badge_id: Mapped[str] = mapped_column(ForeignKey("badge_definitions.id", ondelete="CASCADE"), index=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
