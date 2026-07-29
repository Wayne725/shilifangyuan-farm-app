from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Index,
    Integer,
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
    NONMEMBER = "nonmember"


class TaxType(str, enum.Enum):
    TAXABLE = "taxable"
    TAX_EXEMPT = "tax_exempt"


class ProductCategory(str, enum.Enum):
    SEASONAL_PRODUCE = "當季蔬果"
    RICE_AND_GRAINS = "米・雜糧"
    EGGS = "蛋品"
    PROCESSED = "加工品"


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


class InvoiceStatus(str, enum.Enum):
    NOT_ELIGIBLE = "not_eligible"
    PENDING = "pending"
    ISSUED = "issued"
    FAILED = "failed"


class InvoiceCarrierType(str, enum.Enum):
    ECPAY = "ecpay"
    MOBILE_BARCODE = "mobile_barcode"


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


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(String(255))
    user_role: Mapped[UserRole] = mapped_column(
        enum_type(UserRole, "user_role"), default=UserRole.CUSTOMER
    )
    membership_type: Mapped[MembershipType] = mapped_column(
        enum_type(MembershipType, "membership_type"),
        default=MembershipType.NONMEMBER,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
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
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


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


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("amount_total >= 0", name="amount_total_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    order_kind: Mapped[OrderKind] = mapped_column(
        enum_type(OrderKind, "order_kind"), index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    group_campaign_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("group_campaigns.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    membership_type_snapshot: Mapped[MembershipType] = mapped_column(
        enum_type(MembershipType, "order_membership_type")
    )
    amount_total: Mapped[int] = mapped_column(Integer)
    contact_email: Mapped[str] = mapped_column(String(320))
    invoice_carrier_type: Mapped[InvoiceCarrierType] = mapped_column(
        enum_type(InvoiceCarrierType, "invoice_carrier_type"),
        default=InvoiceCarrierType.ECPAY,
    )
    invoice_carrier_value: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )
    fulfillment_status: Mapped[FulfillmentStatus] = mapped_column(
        enum_type(FulfillmentStatus, "fulfillment_status"),
        default=FulfillmentStatus.PENDING_CONFIRMATION,
        index=True,
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
    product_name: Mapped[str] = mapped_column(String(120))
    unit_label: Mapped[str] = mapped_column(String(40))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[int] = mapped_column(Integer)
    subtotal: Mapped[int] = mapped_column(Integer)
    tax_type: Mapped[TaxType] = mapped_column(enum_type(TaxType, "order_tax_type"))

    order: Mapped[Order] = relationship(back_populates="items")


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
        CheckConstraint("amount >= 0", name="amount_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), index=True
    )
    merchant_trade_no: Mapped[str] = mapped_column(
        String(20), unique=True, index=True
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

    order: Mapped[Order] = relationship(back_populates="payment_attempts")
    reservations: Mapped[List[InventoryReservation]] = relationship(
        back_populates="payment_attempt"
    )


class Refund(Base):
    __tablename__ = "refunds"
    __table_args__ = (
        CheckConstraint("amount >= 0", name="amount_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), index=True
    )
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

    order: Mapped[Order] = relationship(back_populates="refunds")
    requested_by: Mapped[User] = relationship()


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), unique=True, index=True
    )
    relate_number: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    invoice_number: Mapped[Optional[str]] = mapped_column(
        String(24), nullable=True, unique=True
    )
    invoice_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    random_number: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    status: Mapped[InvoiceStatus] = mapped_column(
        enum_type(InvoiceStatus, "invoice_record_status"),
        default=InvoiceStatus.PENDING,
        index=True,
    )
    provider_response: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    issued_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    order: Mapped[Order] = relationship(back_populates="invoice")


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
