from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

from .models import (
    FulfillmentStatus,
    GroupDecisionStatus,
    GroupIntakeStatus,
    InvoiceCarrierType,
    InvoiceStatus,
    MembershipType,
    OrderKind,
    PaymentStatus,
    ProductCategory,
    ProposalStatus,
    TargetType,
    TaxType,
    UserRole,
)


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)


class Message(ApiModel):
    message: str


class DemoResetRequest(BaseModel):
    confirmation: str = Field(min_length=4, max_length=128)


class UserRead(ApiModel):
    id: str
    email: EmailStr
    display_name: str
    user_role: UserRole
    membership_type: MembershipType


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(ApiModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserRead


class ProductRead(ApiModel):
    id: str
    slug: str
    name: str
    description: str
    category: ProductCategory
    unit: str
    image_url: Optional[str]
    member_price: int
    nonmember_price: int
    stock_quantity: int
    tax_type: TaxType
    is_active: bool


class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    category: ProductCategory
    unit: str = Field(min_length=1, max_length=40)
    image_url: Optional[str] = None
    member_price: int = Field(ge=0)
    nonmember_price: int = Field(ge=0)
    stock_quantity: int = Field(default=0, ge=0)
    tax_type: TaxType = TaxType.TAXABLE
    is_active: bool = True

    @model_validator(mode="after")
    def validate_price_order(self) -> "ProductCreate":
        if self.member_price > self.nonmember_price:
            raise ValueError("社員價不可高於非社員價")
        return self


class ProductUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = None
    category: Optional[ProductCategory] = None
    unit: Optional[str] = Field(default=None, min_length=1, max_length=40)
    image_url: Optional[str] = None
    member_price: Optional[int] = Field(default=None, ge=0)
    nonmember_price: Optional[int] = Field(default=None, ge=0)
    stock_quantity: Optional[int] = Field(default=None, ge=0)
    tax_type: Optional[TaxType] = None
    is_active: Optional[bool] = None

    @model_validator(mode="before")
    @classmethod
    def reject_null_for_required_columns(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        nullable_fields = {"image_url"}
        null_fields = sorted(
            field
            for field, value in data.items()
            if field not in nullable_fields and value is None
        )
        if null_fields:
            raise ValueError(
                f"{'、'.join(null_fields)} 不可為 null"
            )
        return data

    @model_validator(mode="after")
    def validate_price_order(self) -> "ProductUpdate":
        if (
            self.member_price is not None
            and self.nonmember_price is not None
            and self.member_price > self.nonmember_price
        ):
            raise ValueError("社員價不可高於非社員價")
        return self


class BundleItemInput(BaseModel):
    product_id: str
    quantity: int = Field(ge=1, le=999)


class BundleItemRead(ApiModel):
    product_id: str
    product_name: str
    quantity: int


class BundleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    image_url: Optional[str] = None
    member_price: int = Field(ge=0)
    nonmember_price: int = Field(ge=0)
    is_active: bool = True
    items: List[BundleItemInput] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_price_order(self) -> "BundleCreate":
        if self.member_price > self.nonmember_price:
            raise ValueError("社員價不可高於非社員價")
        return self


class BundleRead(ApiModel):
    id: str
    name: str
    description: str
    image_url: Optional[str]
    member_price: int
    nonmember_price: int
    is_active: bool
    items: List[BundleItemRead]


class ProposalCreate(BaseModel):
    target_type: TargetType
    target_id: str


class ProposalReview(BaseModel):
    threshold: int = Field(default=10, ge=1, le=10000)
    deadline: Optional[datetime] = None


class ProposalReject(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class VoteUpsert(BaseModel):
    estimated_quantity: int = Field(ge=1, le=999)


class ProposalRead(ApiModel):
    id: str
    proposer_id: str
    target_type: TargetType
    target_id: str
    target_name: str
    status: ProposalStatus
    threshold: int
    deadline: Optional[datetime]
    conversion_deadline: Optional[datetime]
    vote_count: int
    estimated_quantity: int
    my_vote_quantity: Optional[int]
    review_reason: Optional[str]
    created_at: datetime


class CampaignCreate(BaseModel):
    source_proposal_id: Optional[str] = None
    target_type: TargetType
    target_id: str
    title: str = Field(min_length=1, max_length=120)
    description: str = ""
    image_url: Optional[str] = None
    member_price: int = Field(ge=0)
    nonmember_price: int = Field(ge=0)
    min_paid_quantity: int = Field(ge=1)
    supply_cap: int = Field(ge=1)
    per_user_cap: int = Field(default=5, ge=1)
    deadline: datetime
    estimated_pickup_start: datetime
    estimated_pickup_end: datetime

    @field_validator(
        "deadline", "estimated_pickup_start", "estimated_pickup_end"
    )
    @classmethod
    def ensure_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("時間必須包含時區")
        return value

    @model_validator(mode="after")
    def validate_price_order(self) -> "CampaignCreate":
        if self.member_price > self.nonmember_price:
            raise ValueError("社員價不可高於非社員價")
        return self


class CampaignDecision(BaseModel):
    final_pickup_at: datetime

    @field_validator("final_pickup_at")
    @classmethod
    def ensure_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("時間必須包含時區")
        return value


class CampaignReject(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class CampaignRead(ApiModel):
    id: str
    source_proposal_id: Optional[str]
    target_type: TargetType
    target_id: str
    title: str
    description: str
    image_url: Optional[str]
    member_price: int
    nonmember_price: int
    min_paid_quantity: int
    supply_cap: int
    per_user_cap: int
    paid_quantity: int
    reserved_quantity: int
    available_quantity: int
    deadline: datetime
    estimated_pickup_start: datetime
    estimated_pickup_end: datetime
    final_pickup_at: Optional[datetime]
    decision_status: GroupDecisionStatus
    intake_status: GroupIntakeStatus
    confirmation_deadline: Optional[datetime]
    confirmed_at: Optional[datetime]
    created_at: datetime


class OrderLineInput(BaseModel):
    product_id: str
    quantity: int = Field(ge=1, le=999)


class OrderQuoteRequest(BaseModel):
    items: List[OrderLineInput] = Field(min_length=1, max_length=100)


class OrderQuoteLine(ApiModel):
    product_id: str
    product_name: str
    unit_label: str
    quantity: int
    unit_price: int
    subtotal: int
    tax_type: TaxType


class OrderQuoteRead(ApiModel):
    membership_type: MembershipType
    amount_total: int
    items: List[OrderQuoteLine]


class OrderCreate(BaseModel):
    items: List[OrderLineInput] = Field(min_length=1, max_length=100)
    contact_email: EmailStr
    invoice_carrier_type: InvoiceCarrierType = InvoiceCarrierType.ECPAY
    invoice_carrier_value: Optional[str] = Field(default=None, max_length=64)


class GroupJoinRequest(BaseModel):
    quantity: int = Field(ge=1, le=999)
    contact_email: EmailStr
    invoice_carrier_type: InvoiceCarrierType = InvoiceCarrierType.ECPAY
    invoice_carrier_value: Optional[str] = Field(default=None, max_length=64)


class OrderItemRead(ApiModel):
    id: str
    product_name: str
    unit_label: str
    quantity: int
    unit_price: int
    subtotal: int
    tax_type: TaxType


class OrderRead(ApiModel):
    id: str
    order_number: str
    order_kind: OrderKind
    group_campaign_id: Optional[str]
    membership_type_snapshot: MembershipType
    amount_total: int
    contact_email: EmailStr
    invoice_carrier_type: InvoiceCarrierType
    fulfillment_status: FulfillmentStatus
    payment_status: PaymentStatus
    invoice_status: InvoiceStatus
    paid_at: Optional[datetime]
    cancelled_at: Optional[datetime]
    created_at: datetime
    available_actions: List[str]
    items: List[OrderItemRead]


class CancelRequest(BaseModel):
    reason: str = Field(default="買家取消", min_length=1, max_length=1000)


class RefundRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class FulfillmentUpdate(BaseModel):
    status: FulfillmentStatus


class NotificationRead(ApiModel):
    id: str
    event_type: str
    title: str
    body: str
    data: Dict[str, Any]
    read_at: Optional[datetime]
    created_at: datetime
