from __future__ import annotations

from datetime import date, datetime
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
    ActivityRegistrationStatus,
    ActivityStatus,
    FulfillmentStatus,
    FulfillmentMethod,
    FulfillmentState,
    GroupDecisionStatus,
    GroupIntakeStatus,
    InvoiceCarrierType,
    InvoiceStatus,
    MealEventStatus,
    MemberProposalStatus,
    MemberVoteChoice,
    MembershipApplicationStatus,
    MembershipChargeKind,
    MembershipChargeStatus,
    MembershipDocumentStatus,
    MembershipDocumentType,
    MembershipStatus,
    MembershipType,
    OrderKind,
    PaymentStatus,
    ProductCategory,
    ProposalStatus,
    SalesChannel,
    ShipmentStatus,
    ShippingChannel,
    ShippingTemperature,
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
    email_verified_at: Optional[datetime] = None
    membership_status: Optional[MembershipStatus] = None


class RegisterRequest(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=8, max_length=128)


class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=16, max_length=512)


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=16, max_length=512)
    password: str = Field(min_length=8, max_length=128)


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
    can_ship: bool = False
    shipping_temperature: Optional[ShippingTemperature] = None
    allowed_shipping_channels: List[ShippingChannel] = Field(default_factory=list)
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
    can_ship: bool = False
    shipping_temperature: Optional[ShippingTemperature] = None
    allowed_shipping_channels: List[ShippingChannel] = Field(default_factory=list)
    is_active: bool = True

    @model_validator(mode="after")
    def validate_price_order(self) -> "ProductCreate":
        if self.member_price > self.nonmember_price:
            raise ValueError("社員價不可高於非社員價")
        if self.can_ship and (
            self.shipping_temperature is None
            or not self.allowed_shipping_channels
        ):
            raise ValueError("可配送商品必須設定溫層與至少一個物流通路")
        if not self.can_ship and (
            self.shipping_temperature is not None
            or self.allowed_shipping_channels
        ):
            raise ValueError("不可配送商品不得設定物流溫層或通路")
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
    can_ship: Optional[bool] = None
    shipping_temperature: Optional[ShippingTemperature] = None
    allowed_shipping_channels: Optional[List[ShippingChannel]] = None
    is_active: Optional[bool] = None

    @model_validator(mode="before")
    @classmethod
    def reject_null_for_required_columns(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        nullable_fields = {"image_url", "shipping_temperature"}
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
    can_ship: bool = False
    shipping_temperature: Optional[ShippingTemperature] = None
    allowed_shipping_channels: List[ShippingChannel] = Field(default_factory=list)

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
        if not self.can_ship:
            self.shipping_temperature = None
            self.allowed_shipping_channels = []
            return self
        if (
            self.shipping_temperature is None
            or not self.allowed_shipping_channels
        ):
            raise ValueError("可配送團購必須設定溫層與至少一個物流通路")
        if (
            self.shipping_temperature != ShippingTemperature.AMBIENT
            and any(
                channel != ShippingChannel.HOME_DELIVERY
                for channel in self.allowed_shipping_channels
            )
        ):
            raise ValueError("冷藏或冷凍團購只支援宅配")
        return self


class CampaignUpdate(BaseModel):
    target_type: Optional[TargetType] = None
    target_id: Optional[str] = Field(default=None, min_length=1)
    title: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = None
    image_url: Optional[str] = None
    member_price: Optional[int] = Field(default=None, ge=0)
    nonmember_price: Optional[int] = Field(default=None, ge=0)
    min_paid_quantity: Optional[int] = Field(default=None, ge=1)
    supply_cap: Optional[int] = Field(default=None, ge=1)
    per_user_cap: Optional[int] = Field(default=None, ge=1)
    deadline: Optional[datetime] = None
    estimated_pickup_start: Optional[datetime] = None
    estimated_pickup_end: Optional[datetime] = None
    can_ship: Optional[bool] = None
    shipping_temperature: Optional[ShippingTemperature] = None
    allowed_shipping_channels: Optional[List[ShippingChannel]] = None

    @model_validator(mode="before")
    @classmethod
    def reject_null_for_required_columns(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        nullable_fields = {"image_url", "shipping_temperature"}
        null_fields = sorted(
            field
            for field, value in data.items()
            if field not in nullable_fields and value is None
        )
        if null_fields:
            raise ValueError(f"{'、'.join(null_fields)} 不可為 null")
        return data

    @field_validator(
        "deadline", "estimated_pickup_start", "estimated_pickup_end"
    )
    @classmethod
    def ensure_timezone(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is not None and value.tzinfo is None:
            raise ValueError("時間必須包含時區")
        return value


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
    core_locked_at: Optional[datetime]
    can_ship: bool = False
    shipping_temperature: Optional[ShippingTemperature] = None
    allowed_shipping_channels: List[ShippingChannel] = Field(default_factory=list)
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


class GroupJoinQuoteRequest(BaseModel):
    quantity: int = Field(ge=1, le=999)
    fulfillment_method: FulfillmentMethod = FulfillmentMethod.COOPERATIVE_PICKUP
    shipping_channel: Optional[ShippingChannel] = None

    @model_validator(mode="after")
    def validate_shipping_channel(self) -> "GroupJoinQuoteRequest":
        if (
            self.fulfillment_method == FulfillmentMethod.ECPAY_LOGISTICS
            and self.shipping_channel is None
        ):
            raise ValueError("物流配送必須選擇通路")
        return self


class GroupJoinQuoteRead(ApiModel):
    membership_type: MembershipType
    quantity: int
    unit_price: int
    product_subtotal: int
    shipping_fee: int
    amount_total: int


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
    sales_channel: SalesChannel = SalesChannel.REGULAR
    fulfillment_method: FulfillmentMethod = FulfillmentMethod.COOPERATIVE_PICKUP
    group_campaign_id: Optional[str]
    meal_event_id: Optional[str] = None
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
    fulfillment: Optional["OrderFulfillmentRead"] = None
    shipment: Optional["ShipmentRead"] = None
    meal_event: Optional["MealEventSummary"] = None


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


class MembershipProfileInput(BaseModel):
    legal_name: str = Field(min_length=1, max_length=120)
    phone: str = Field(min_length=8, max_length=40)
    birth_date: date
    address: str = Field(min_length=1, max_length=500)
    emergency_contact: str = Field(min_length=1, max_length=240)
    consent_version: str = Field(min_length=1, max_length=40)


class MembershipApplicationSubmit(MembershipProfileInput):
    pass


class MembershipApplicationReview(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=2000)


class MembershipProfileRead(BaseModel):
    legal_name: str
    phone: str
    birth_date: date
    address: str
    emergency_contact: str
    consent_version: str
    consented_at: datetime


class MembershipDocumentRead(ApiModel):
    id: str
    document_type: MembershipDocumentType
    status: MembershipDocumentStatus
    content_type: str
    size_bytes: int
    checksum_sha256: Optional[str]
    confirmed_at: Optional[datetime]


class MembershipApplicationRead(ApiModel):
    id: str
    user_id: str
    status: MembershipApplicationStatus
    submitted_at: Optional[datetime]
    reviewed_at: Optional[datetime]
    review_reason: Optional[str]
    created_at: datetime
    updated_at: datetime
    profile: Optional[MembershipProfileRead] = None
    documents: List[MembershipDocumentRead] = Field(default_factory=list)


class MembershipDocumentUploadRequest(BaseModel):
    document_type: MembershipDocumentType
    content_type: str = Field(pattern=r"^(image/jpeg|image/png|application/pdf)$")
    size_bytes: int = Field(gt=0, le=8 * 1024 * 1024)
    checksum_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")


class MembershipDocumentUploadRead(ApiModel):
    document_id: str
    object_key: str
    upload_url: str
    expires_in_seconds: int = 300
    required_headers: dict[str, str] = Field(default_factory=dict)


class MembershipDocumentConfirm(BaseModel):
    checksum_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")


class MembershipRead(ApiModel):
    id: str
    user_id: str
    member_number: Optional[str]
    status: MembershipStatus
    activated_at: Optional[datetime]
    suspended_at: Optional[datetime]
    ended_at: Optional[datetime]
    status_reason: Optional[str]


class MembershipChargeRead(ApiModel):
    id: str
    charge_kind: MembershipChargeKind
    amount: int
    status: MembershipChargeStatus
    receipt_number: Optional[str]
    paid_at: Optional[datetime]
    refunded_at: Optional[datetime]


class MembershipFeeScheduleInput(BaseModel):
    charge_kind: MembershipChargeKind
    amount: int = Field(ge=0)
    effective_from: date
    effective_to: Optional[date] = None

    @model_validator(mode="after")
    def validate_effective_range(self) -> "MembershipFeeScheduleInput":
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("費率結束日不可早於生效日")
        return self


class MemberDirectoryUpdate(BaseModel):
    is_public: bool
    nickname: str = Field(min_length=1, max_length=80)
    avatar_url: Optional[str] = Field(default=None, max_length=500)
    expertise: str = Field(default="", max_length=240)
    bio: str = Field(default="", max_length=2000)


class MemberDirectoryRead(ApiModel):
    user_id: str
    is_public: bool
    nickname: str
    avatar_url: Optional[str]
    expertise: str
    bio: str


class MembershipMeRead(BaseModel):
    membership_type: MembershipType
    membership: Optional[MembershipRead]
    directory: Optional[MemberDirectoryRead]


class ActivityCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=10000)
    image_url: Optional[str] = Field(default=None, max_length=500)
    location: str = Field(min_length=1, max_length=240)
    starts_at: datetime
    ends_at: datetime
    registration_deadline: datetime
    capacity: int = Field(ge=1, le=100000)
    waitlist_enabled: bool = True

    @model_validator(mode="after")
    def validate_timeline(self) -> "ActivityCreate":
        if self.ends_at <= self.starts_at:
            raise ValueError("活動結束時間必須晚於開始時間")
        if self.registration_deadline > self.starts_at:
            raise ValueError("報名截止不得晚於活動開始")
        return self


class ActivityReview(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=2000)


class ActivityRegistrationRead(ApiModel):
    id: str
    user_id: str
    status: ActivityRegistrationStatus
    queue_position: int
    registered_at: datetime
    cancelled_at: Optional[datetime]
    checked_in_at: Optional[datetime]


class AdminActivityRegistrationRead(ActivityRegistrationRead):
    display_name: str
    email: EmailStr


class ActivityRead(ApiModel):
    id: str
    created_by_id: str
    title: str
    description: str
    image_url: Optional[str]
    location: str
    starts_at: datetime
    ends_at: datetime
    registration_deadline: datetime
    capacity: int
    waitlist_enabled: bool
    status: ActivityStatus
    reviewed_at: Optional[datetime]
    review_reason: Optional[str]
    registration_count: int = 0
    waitlist_count: int = 0
    my_registration: Optional[ActivityRegistrationRead] = None


class MemberProposalCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=20000)


class MemberProposalReview(BaseModel):
    minimum_voters: int = Field(default=10, ge=1, le=100000)
    discussion_ends_at: datetime
    voting_ends_at: datetime

    @model_validator(mode="after")
    def validate_timeline(self) -> "MemberProposalReview":
        if self.voting_ends_at <= self.discussion_ends_at:
            raise ValueError("投票截止必須晚於討論截止")
        return self


class MemberProposalCommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


class MemberProposalCommentRead(ApiModel):
    id: str
    user_id: str
    display_name: str
    body: str
    created_at: datetime
    updated_at: datetime


class MemberProposalVoteUpsert(BaseModel):
    choice: MemberVoteChoice


class MemberProposalNamedVoteRead(BaseModel):
    user_id: str
    display_name: str
    choice: MemberVoteChoice
    updated_at: datetime


class MemberProposalTally(ApiModel):
    yes: int = 0
    no: int = 0
    abstain: int = 0
    total: int = 0


class MemberProposalRead(ApiModel):
    id: str
    created_by_id: str
    created_by_name: str
    title: str
    body: str
    status: MemberProposalStatus
    minimum_voters: int
    discussion_ends_at: Optional[datetime]
    voting_ends_at: Optional[datetime]
    review_reason: Optional[str]
    result_summary: Optional[str]
    tally: MemberProposalTally = Field(default_factory=MemberProposalTally)
    my_vote: Optional[MemberVoteChoice] = None
    created_at: datetime


class MealCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=5000)
    image_url: Optional[str] = Field(default=None, max_length=500)
    price: int = Field(ge=0)
    tax_type: TaxType = TaxType.TAXABLE
    is_active: bool = True


class MealRead(ApiModel):
    id: str
    slug: str
    name: str
    description: str
    image_url: Optional[str]
    price: int
    tax_type: TaxType
    is_active: bool


class MealOfferingInput(BaseModel):
    meal_id: str
    price: int = Field(ge=0)
    capacity: int = Field(ge=1, le=100000)
    position: int = Field(default=0, ge=0)


class MealEventCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    location: str = Field(min_length=1, max_length=240)
    ordering_starts_at: datetime
    ordering_ends_at: datetime
    pickup_starts_at: datetime
    pickup_ends_at: datetime
    offerings: List[MealOfferingInput] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_timeline(self) -> "MealEventCreate":
        if self.ordering_ends_at <= self.ordering_starts_at:
            raise ValueError("訂購截止必須晚於開賣時間")
        if self.pickup_starts_at < self.ordering_ends_at:
            raise ValueError("取餐開始不得早於訂購截止")
        if self.pickup_ends_at <= self.pickup_starts_at:
            raise ValueError("取餐結束必須晚於取餐開始")
        if len({item.meal_id for item in self.offerings}) != len(self.offerings):
            raise ValueError("同一場次不可重複餐點")
        return self


class MealOfferingRead(ApiModel):
    id: str
    meal_id: str
    meal_name: str
    description: str
    image_url: Optional[str]
    price: int
    capacity: int
    reserved_quantity: int
    paid_quantity: int
    available_quantity: int
    position: int
    is_active: bool


class MealEventSummary(ApiModel):
    id: str
    title: str
    location: str
    pickup_starts_at: datetime
    pickup_ends_at: datetime


class MealEventRead(MealEventSummary):
    ordering_starts_at: datetime
    ordering_ends_at: datetime
    status: MealEventStatus
    offerings: List[MealOfferingRead] = Field(default_factory=list)


class MealOrderLineInput(BaseModel):
    offering_id: str
    quantity: int = Field(ge=1, le=99)


class MealOrderCreate(BaseModel):
    items: List[MealOrderLineInput] = Field(min_length=1, max_length=20)
    contact_email: EmailStr
    invoice_carrier_type: InvoiceCarrierType = InvoiceCarrierType.ECPAY
    invoice_carrier_value: Optional[str] = Field(default=None, max_length=64)


class MealOrderQuoteItemRead(ApiModel):
    offering_id: str
    meal_id: str
    meal_name: str
    quantity: int
    unit_price: int
    subtotal: int
    tax_type: TaxType


class MealPickupWindowRead(ApiModel):
    location: str
    starts_at: datetime
    ends_at: datetime


class MealOrderQuoteRead(ApiModel):
    sales_channel: SalesChannel
    fulfillment_method: FulfillmentMethod
    items: List[MealOrderQuoteItemRead]
    amount_total: int
    pickup: MealPickupWindowRead


class MealOrderItemRead(ApiModel):
    offering_id: str
    meal_id: str
    meal_name: str
    quantity: int
    unit_price: int
    subtotal: int


class MealOrderRead(ApiModel):
    id: str
    order_number: str
    sales_channel: SalesChannel
    fulfillment_method: FulfillmentMethod
    meal_event_id: str
    meal_event_title: str
    venue_name: str
    pickup_start: datetime
    pickup_end: datetime
    pickup_code: Optional[str]
    pickup_qr_payload: Optional[str]
    payment_status: PaymentStatus
    invoice_status: InvoiceStatus
    fulfillment_status: str
    paid_at: Optional[datetime]
    cancelled_at: Optional[datetime]
    amount_total: int
    created_at: datetime
    available_actions: List[str]
    items: List[MealOrderItemRead]


class MealPickupCredentialRead(ApiModel):
    order_id: str
    pickup_code: str
    qr_token: str


class MealPickupVerify(BaseModel):
    pickup_code: Optional[str] = Field(default=None, pattern=r"^\d{6}$")
    qr_token: Optional[str] = Field(default=None, min_length=16, max_length=512)

    @model_validator(mode="after")
    def validate_credential(self) -> "MealPickupVerify":
        if (self.pickup_code is None) == (self.qr_token is None):
            raise ValueError("取餐碼與 QR token 必須擇一提供")
        return self


class MealPickupRedemptionRead(ApiModel):
    order_id: str
    order_number: str
    pickup_code: str
    status: str
    redeemed_at: datetime


class MealEventActionRequest(BaseModel):
    reason: Optional[str] = Field(default=None, min_length=1, max_length=2000)


class MealEventCancelRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class OrderFulfillmentRead(ApiModel):
    method: FulfillmentMethod
    status: FulfillmentState
    pickup_location: Optional[str]
    pickup_starts_at: Optional[datetime]
    pickup_ends_at: Optional[datetime]
    pickup_code: Optional[str]
    fulfilled_at: Optional[datetime]


class ShipmentRead(ApiModel):
    id: str
    channel: ShippingChannel
    temperature: ShippingTemperature
    status: ShipmentStatus
    shipping_fee: int
    ecpay_logistics_id: Optional[str]
    tracking_number: Optional[str]


class LogisticsSelectionRequest(BaseModel):
    channel: ShippingChannel
    temperature: ShippingTemperature
    recipient_name: str = Field(min_length=1, max_length=120)
    recipient_phone: str = Field(min_length=8, max_length=40)
    shipping_address: str = Field(min_length=1, max_length=500)


class ShippingRateInput(BaseModel):
    channel: ShippingChannel
    temperature: ShippingTemperature
    fee: int = Field(ge=0)
    free_shipping_threshold: int = Field(default=1500, ge=0)
    effective_from: date
    effective_to: Optional[date] = None

    @model_validator(mode="after")
    def validate_effective_range(self) -> "ShippingRateInput":
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("運費結束日不可早於生效日")
        return self


class ShippingRateRead(ApiModel):
    id: str
    channel: ShippingChannel
    temperature: ShippingTemperature
    fee: int
    free_shipping_threshold: int
    effective_from: date
    effective_to: Optional[date]
    is_active: bool
