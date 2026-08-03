export type UserRole = "customer" | "admin";
export type MembershipType = "member" | "nonmember";
export type Workspace = "life" | "social";

export type User = {
  id: string;
  email: string;
  display_name: string;
  user_role: UserRole;
  membership_type: MembershipType;
};

export type AuthSession = {
  access_token: string;
  refresh_token?: string;
  token_type: string;
  user: User;
};

export type Category = "當季蔬果" | "米・雜糧" | "蛋品" | "加工品";
export type TaxType = "taxable" | "tax_exempt";

export type Product = {
  id: string;
  slug?: string;
  name: string;
  description: string;
  category: Category;
  unit: string;
  origin?: string;
  image_url?: string | null;
  image_key?: string;
  member_price: number;
  nonmember_price: number;
  tax_type: TaxType;
  stock?: number;
  stock_quantity?: number;
  badge?: string;
  is_active: boolean;
  is_shippable?: boolean;
  temperature_zone?: TemperatureZone;
  allowed_logistics?: LogisticsProvider[];
};

export type BundleItem = {
  product_id: string;
  product_name: string;
  quantity: number;
};

export type GroupBundle = {
  id: string;
  name: string;
  description: string;
  image_url?: string | null;
  image_key?: string;
  member_price: number;
  nonmember_price: number;
  is_active: boolean;
  items: BundleItem[];
};

export type ProposalTargetType = "product" | "bundle";
export type ProposalStatus =
  | "pending_review"
  | "voting"
  | "ended_unmet"
  | "conversion_pending"
  | "converted"
  | "rejected"
  | "expired_unhandled";

export type VoteProposal = {
  id: string;
  target_type: ProposalTargetType;
  target_id: string;
  target_name: string;
  status: ProposalStatus;
  threshold: number;
  deadline: string | null;
  vote_count: number;
  estimated_quantity: number;
  my_vote_quantity?: number | null;
  created_at: string;
};

export type GroupDecisionStatus =
  | "recruiting"
  | "pending_confirmation"
  | "confirmed"
  | "rejected"
  | "failed_unmet"
  | "expired_unconfirmed"
  | "cancelled";

export type GroupIntakeStatus =
  | "open"
  | "paused"
  | "settling"
  | "full"
  | "closed";

export type GroupCampaign = {
  id: string;
  title: string;
  description: string;
  image_key?: string;
  image_url?: string | null;
  decision_status: GroupDecisionStatus;
  intake_status: GroupIntakeStatus;
  member_price: number;
  nonmember_price: number;
  min_paid_quantity: number;
  supply_cap: number;
  per_user_cap: number;
  paid_quantity: number;
  reserved_quantity: number;
  deadline: string;
  estimated_pickup_start: string;
  estimated_pickup_end: string;
  final_pickup_at?: string | null;
  can_ship: boolean;
  temperature_zone?: TemperatureZone;
  allowed_logistics: LogisticsProvider[];
  available_quantity: number;
  created_at: string;
};

export type OrderKind = "regular" | "group" | "meal_preorder";
export type SalesChannel = "regular" | "group" | "meal_preorder";
export type FulfillmentMethod =
  | "cooperative_pickup"
  | "event_pickup"
  | "ecpay_logistics";
export type TemperatureZone = "ambient" | "chilled" | "frozen";
export type LogisticsProvider =
  | "home_delivery"
  | "seven_eleven"
  | "family_mart"
  | "hilife";
export type FulfillmentStatus =
  | "pending_confirmation"
  | "preparing"
  | "ready_for_pickup"
  | "picked_up"
  | "cancelled";
export type PaymentStatus =
  | "pending"
  | "paid"
  | "late_paid_refund_required"
  | "refund_pending"
  | "refunded"
  | "failed"
  | "expired";
export type InvoiceStatus = "not_eligible" | "pending" | "issued" | "failed";
export type OrderAction =
  | "pay"
  | "cancel"
  | "refund"
  | "start_preparing"
  | "mark_ready"
  | "mark_picked_up"
  | "create_shipment"
  | "advance_shipment"
  | "advance_fulfillment"
  | "view";

export type OrderItem = {
  product_id?: string;
  product_name: string;
  quantity: number;
  unit_price: number;
  subtotal: number;
  tax_type: TaxType;
};

export type Order = {
  id: string;
  order_number: string;
  order_kind: OrderKind;
  group_campaign_id?: string | null;
  fulfillment_status: FulfillmentStatus;
  payment_status: PaymentStatus;
  invoice_status: InvoiceStatus;
  membership_type_snapshot: MembershipType;
  amount_total: number;
  paid_at?: string | null;
  created_at: string;
  available_actions: OrderAction[];
  items: OrderItem[];
  sales_channel?: SalesChannel;
  fulfillment?: OrderFulfillment;
  shipment?: Shipment | null;
  meal_event?: Pick<
    MealEvent,
    "id" | "title" | "venue_name" | "pickup_start" | "pickup_end"
  > | null;
  pickup_code?: string | null;
};

export type OrderFulfillment = {
  method: FulfillmentMethod;
  status:
    | FulfillmentStatus
    | "pending"
    | "ready"
    | "awaiting_shipment"
    | "shipped"
    | "delivered"
    | "no_show";
  venue_name?: string | null;
  pickup_start?: string | null;
  pickup_end?: string | null;
  address_summary?: string | null;
};

export type Shipment = {
  id: string;
  logistics_provider: LogisticsProvider;
  temperature_zone: TemperatureZone;
  status:
    | "draft"
    | "selection_pending"
    | "ready_to_create"
    | "created"
    | "in_transit"
    | "delivered"
    | "exception"
    | "cancelled";
  tracking_number?: string | null;
  ecpay_logistics_id?: string | null;
  shipping_fee: number;
};

export type NotificationKind =
  | "proposal"
  | "group"
  | "membership"
  | "payment"
  | "pickup"
  | "invoice";

export type AppNotification = {
  id: string;
  kind: NotificationKind;
  title: string;
  body: string;
  created_at: string;
  read_at?: string | null;
  route?: string | null;
};

export type InvoiceCarrierType = "ecpay" | "mobile_barcode";

export type CartItem = {
  product_id: string;
  quantity: number;
};

export type OrderQuote = {
  items: OrderItem[];
  amount_total: number;
  membership_type: MembershipType;
};

export type PaymentAttempt = {
  id: string;
  order_id: string;
  payment_url?: string | null;
  checkout_url?: string | null;
  status: PaymentStatus;
};

export type DashboardSummary = {
  pending_orders: number;
  active_campaigns: number;
  pending_proposals: number;
  pickup_orders: number;
};

export type MembershipStatus =
  | "pending_payment"
  | "active"
  | "suspended"
  | "resigned"
  | "terminated";

export type MembershipApplicationStatus =
  | "draft"
  | "submitted"
  | "needs_revision"
  | "approved"
  | "rejected"
  | "withdrawn";

export type MembershipApplication = {
  id: string;
  user_id: string;
  status: MembershipApplicationStatus;
  legal_name: string;
  phone: string;
  birth_date: string;
  address: string;
  emergency_contact_name: string;
  emergency_contact_phone: string;
  consented_at?: string | null;
  review_note?: string | null;
  submitted_at?: string | null;
  required_documents: ("id_front" | "id_back" | "secondary")[];
  confirmed_documents: ("id_front" | "id_back" | "secondary")[];
  documents?: MembershipDocumentRead[];
};

export type Membership = {
  id: string;
  user_id?: string;
  member_number?: string | null;
  status: MembershipStatus;
  started_at?: string | null;
  suspended_at?: string | null;
  ended_at?: string | null;
  status_reason?: string | null;
  directory_visible: boolean;
  nickname: string;
  avatar_url?: string | null;
  expertise?: string | null;
  bio?: string | null;
};

export type MembershipCharge = {
  id: string;
  charge_type: "joining_fee" | "share_capital";
  amount: number;
  payment_status: PaymentStatus;
  receipt_number?: string | null;
  paid_at?: string | null;
};

export type MemberDirectoryEntry = {
  id: string;
  member_number: string;
  is_public?: boolean;
  nickname: string;
  avatar_url?: string | null;
  expertise?: string | null;
  bio?: string | null;
};

export type ShippingRate = {
  id: string;
  channel: LogisticsProvider;
  temperature: TemperatureZone;
  fee: number;
  free_shipping_threshold: number;
  effective_from: string;
  effective_to?: string | null;
  is_active: boolean;
};

export type GroupJoinQuote = {
  membership_type: MembershipType;
  quantity: number;
  unit_price: number;
  product_subtotal: number;
  shipping_fee: number;
  amount_total: number;
};

export type LogisticsSelection = {
  shipment: Shipment;
  /** Empty in demo mode; otherwise the ECPay store-picker page. */
  selection_url: string;
  shipping_fee: number;
  product_subtotal: number;
  amount_total: number;
  expires_in_seconds: number;
};

export type MembershipDocumentUpload = {
  document_id: string;
  upload_url: string;
  object_key: string;
  expires_in_seconds: number;
  required_headers?: Record<string, string>;
};

export type MembershipDocumentRead = {
  id: string;
  document_type: "id_front" | "id_back" | "secondary";
  status: "pending_upload" | "confirmed" | "rejected" | "deleted";
  checksum_sha256?: string | null;
};

export type ActivityStatus =
  | "pending_review"
  | "published"
  | "cancelled"
  | "completed";
export type ActivityRegistrationStatus =
  | "registered"
  | "waitlisted"
  | "cancelled"
  | "attended"
  | "no_show";

export type AdminActivityRegistration = {
  id: string;
  user_id: string;
  display_name: string;
  email: string;
  status: ActivityRegistrationStatus;
  queue_position: number;
  registered_at: string;
  cancelled_at?: string | null;
  checked_in_at?: string | null;
};

export type MemberActivity = {
  id: string;
  title: string;
  description: string;
  image_key?: string;
  image_url?: string | null;
  venue_name: string;
  starts_at: string;
  ends_at: string;
  registration_deadline: string;
  capacity: number;
  registered_count: number;
  waitlist_count: number;
  status: ActivityStatus;
  my_registration_status?: ActivityRegistrationStatus | null;
  created_by_name: string;
};

export type MemberProposalStatus =
  | "draft"
  | "pending_review"
  | "discussion"
  | "voting"
  | "passed"
  | "rejected"
  | "withdrawn"
  | "closed";
export type MemberVoteChoice = "yes" | "no" | "abstain";
export type MemberProposalType = "resolution" | "multiple_choice";

export type MemberProposal = {
  id: string;
  title: string;
  summary: string;
  proposal_type: MemberProposalType;
  options: { id: string; label: string; position: number; vote_count: number }[];
  status: MemberProposalStatus;
  created_by_name: string;
  discussion_ends_at?: string | null;
  voting_ends_at?: string | null;
  minimum_voters: number;
  yes_count: number;
  no_count: number;
  abstain_count: number;
  my_vote?: MemberVoteChoice | null;
  my_option_id?: string | null;
  admin_outcome?: string | null;
  comments?: MemberProposalComment[];
};

export type MemberProposalComment = {
  id: string;
  user_id?: string;
  author_name: string;
  body: string;
  created_at: string;
};

export type MemberProposalNamedVote = {
  user_id: string;
  display_name: string;
  choice?: MemberVoteChoice | null;
  option_id?: string | null;
  option_label?: string | null;
  updated_at: string;
};

export type Meal = {
  id: string;
  name: string;
  description: string;
  price: number;
  image_key?: string;
  image_url?: string | null;
  tax_type?: TaxType;
  is_active: boolean;
};

export type MealEventItem = {
  offering_id: string;
  meal_id: string;
  meal_name: string;
  description: string;
  price: number;
  capacity: number;
  reserved_quantity: number;
  paid_quantity: number;
  available_quantity: number;
  image_key?: string;
  image_url?: string | null;
};

export type MealEvent = {
  id: string;
  title: string;
  school_name: string;
  venue_name: string;
  sales_start: string;
  order_deadline: string;
  pickup_start: string;
  pickup_end: string;
  status:
    | "draft"
    | "published"
    | "ordering_closed"
    | "pickup_open"
    | "completed"
    | "cancelled";
  items: MealEventItem[];
};

export type MealOrder = {
  id: string;
  order_number: string;
  meal_event_id: string;
  meal_event_title: string;
  venue_name: string;
  pickup_start: string;
  pickup_end: string;
  pickup_code: string | null;
  pickup_qr_payload?: string | null;
  payment_status: PaymentStatus;
  fulfillment_status: "pending" | "ready" | "picked_up" | "no_show" | "cancelled";
  amount_total: number;
  created_at: string;
  available_actions?: OrderAction[];
  items: {
    meal_id: string;
    meal_name: string;
    quantity: number;
    unit_price: number;
    subtotal: number;
  }[];
};

export type MealPickupRedemption = {
  order_id: string;
  order_number: string;
  pickup_code: string;
  status: "picked_up";
  redeemed_at: string;
};
