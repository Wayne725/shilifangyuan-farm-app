export type UserRole = "customer" | "admin";

export interface User {
  id: string;
  email: string;
  display_name: string;
  user_role: UserRole;
}

export interface AuthResponse {
  access_token: string;
  refresh_token: string;
  user: User;
}

export interface AuthActionResponse {
  message: string;
  delivery_status?: string;
  development_token?: string;
}

export interface Product {
  id: string;
  product_number?: string | null;
  sku?: string | null;
  supplier_id?: string | null;
  supplier_name?: string | null;
  slug: string;
  name: string;
  description: string;
  category: string;
  unit: string;
  image_url?: string | null;
  member_price: number;
  nonmember_price: number;
  stock_quantity: number;
  tax_type: "taxable" | "tax_exempt";
  can_ship: boolean;
  shipping_temperature?: ShippingTemperature | null;
  allowed_shipping_channels: ShippingChannel[];
  is_active: boolean;
}

export type FulfillmentMethod =
  | "cooperative_pickup"
  | "event_pickup"
  | "ecpay_logistics";

export type ShippingTemperature = "ambient" | "chilled" | "frozen";

export type ShippingChannel =
  | "home_delivery"
  | "seven_eleven"
  | "family_mart"
  | "hilife";

export interface ShippingRate {
  id: string;
  channel: ShippingChannel;
  temperature: ShippingTemperature;
  fee: number;
  free_shipping_threshold: number;
  effective_from: string;
  effective_to?: string | null;
  is_active: boolean;
}

export interface Shipment {
  id: string;
  channel: ShippingChannel;
  temperature: ShippingTemperature;
  status: string;
  shipping_fee: number;
  ecpay_logistics_id?: string | null;
  tracking_number?: string | null;
}

export interface OrderFulfillment {
  method: FulfillmentMethod;
  status: string;
  pickup_location_id?: string | null;
  pickup_location?: string | null;
  pickup_starts_at?: string | null;
  pickup_ends_at?: string | null;
  pickup_code?: string | null;
  fulfilled_at?: string | null;
}

export interface Order {
  id: string;
  order_number: string;
  order_kind: "regular" | "group";
  sales_channel: "regular" | "group" | "meal_preorder";
  fulfillment_method: FulfillmentMethod;
  group_campaign_id?: string | null;
  meal_event_id?: string | null;
  membership_type_snapshot: string;
  amount_total: number;
  tax_amount: number;
  contact_email: string;
  invoice_carrier_type: "ecpay" | "mobile_barcode";
  fulfillment_status: string;
  payment_status: string;
  invoice_status: string;
  paid_at?: string | null;
  cancelled_at?: string | null;
  created_at: string;
  available_actions: string[];
  items: Array<{
    id?: string;
    product_id?: string | null;
    product_name: string;
    unit_label: string;
    quantity: number;
    unit_price: number;
    subtotal: number;
    tax_type: string;
  }>;
  fulfillment?: OrderFulfillment | null;
  shipment?: Shipment | null;
}

export interface LogisticsSelection {
  shipment: Shipment;
  selection_url: string;
  shipping_fee: number;
  product_subtotal: number;
  amount_total: number;
  expires_in_seconds: number;
}

export interface PaymentAttempt {
  id: string;
  attempt_id: string;
  payment_url: string;
  status: string;
  expires_at: string;
}

export interface PickupLocation {
  id: string;
  code: string;
  name: string;
  address: string;
  instructions: string;
  sort_order: number;
  is_active: boolean;
}

export interface Activity {
  id: string;
  created_by_id: string;
  title: string;
  description: string;
  location: string;
  starts_at: string;
  ends_at: string;
  registration_deadline: string;
  status: string;
  image_url?: string | null;
  capacity: number;
  waitlist_enabled: boolean;
  registration_count: number;
  waitlist_count: number;
  review_reason?: string | null;
  my_registration?: {
    id: string;
    status: string;
    queue_position: number;
    registered_at?: string;
    cancelled_at?: string | null;
    checked_in_at?: string | null;
  } | null;
}

export interface Meeting {
  id: string;
  title: string;
  starts_at: string;
  location: string;
  meeting_type: string;
  agenda: Array<Record<string, unknown>>;
  attended_count: number;
  eligible_member_count: number;
  attendance_rate: number;
  resolutions: Array<{
    id: string;
    title: string;
    resolution_text: string;
    member_proposal_id?: string | null;
    created_at: string;
  }>;
}

export interface Wish {
  id: string;
  proposer_id: string;
  name: string;
  description: string;
  expected_price?: number | null;
  reference_url?: string | null;
  support_count: number;
  status: string;
  supported_by_me: boolean;
  launched_product_id?: string | null;
  launched_campaign_id?: string | null;
  admin_note?: string | null;
  created_at: string;
}

export interface Proposal {
  id: string;
  created_by_id: string;
  created_by_name: string;
  title: string;
  body: string;
  proposal_type: "resolution" | "multiple_choice";
  options: Array<{
    id: string;
    label: string;
    position: number;
    vote_count: number;
  }>;
  status: string;
  minimum_voters: number;
  discussion_ends_at?: string | null;
  voting_ends_at?: string | null;
  review_reason?: string | null;
  result_summary?: string | null;
  tally: { yes: number; no: number; abstain: number; total: number };
  my_vote?: "yes" | "no" | "abstain" | null;
  my_option_id?: string | null;
  created_at: string;
}

export interface ProposalComment {
  id: string;
  user_id: string;
  display_name: string;
  body: string;
  created_at: string;
  updated_at: string;
}

export interface ProposalVote {
  user_id: string;
  display_name: string;
  choice?: "yes" | "no" | "abstain" | null;
  option_id?: string | null;
  option_label?: string | null;
  updated_at: string;
}

export interface MemberDirectoryEntry {
  user_id: string;
  is_public: boolean;
  nickname: string;
  avatar_url?: string | null;
  expertise: string;
  bio: string;
}

export interface MembershipDocument {
  id: string;
  document_type: "id_front" | "id_back" | "secondary";
  status: string;
  content_type: string;
  size_bytes: number;
  checksum_sha256?: string | null;
  confirmed_at?: string | null;
}

export interface MembershipApplication {
  id: string;
  user_id: string;
  status: string;
  submitted_at?: string | null;
  reviewed_at?: string | null;
  review_reason?: string | null;
  created_at: string;
  updated_at: string;
  profile?: {
    legal_name: string;
    phone: string;
    birth_date: string;
    address: string;
    emergency_contact: string;
    consent_version: string;
    consented_at: string;
    identity_number?: string | null;
    gender?: string | null;
    place_of_origin?: string | null;
    occupation?: string | null;
    registered_address?: string | null;
    correspondence_address?: string | null;
    landline_phone?: string | null;
    line_id?: string | null;
  } | null;
  documents: MembershipDocument[];
}

export interface GroupCampaign {
  id: string;
  title: string;
  description: string;
  image_url?: string | null;
  target_type: "product" | "bundle";
  target_id: string;
  member_price: number;
  nonmember_price: number;
  min_paid_quantity: number;
  supply_cap: number;
  per_user_cap: number;
  paid_quantity: number;
  reserved_quantity: number;
  available_quantity: number;
  deadline: string;
  estimated_pickup_start: string;
  estimated_pickup_end: string;
  decision_status: string;
  intake_status: string;
  can_ship: boolean;
  shipping_temperature?: ShippingTemperature | null;
  allowed_shipping_channels: ShippingChannel[];
  final_pickup_at?: string | null;
  confirmation_deadline?: string | null;
  confirmed_at?: string | null;
  core_locked_at?: string | null;
  created_at?: string;
}

export interface GroupBundle {
  id: string;
  name: string;
  description: string;
  image_url?: string | null;
  member_price: number;
  nonmember_price: number;
  is_active: boolean;
  items: Array<{
    product_id: string;
    product_name: string;
    quantity: number;
  }>;
}

export interface GroupVoteProposal {
  id: string;
  proposer_id: string;
  target_type: "product" | "bundle";
  target_id: string;
  target_name: string;
  status: string;
  threshold: number;
  deadline?: string | null;
  conversion_deadline?: string | null;
  vote_count: number;
  estimated_quantity: number;
  my_vote_quantity?: number | null;
  review_reason?: string | null;
  created_at: string;
}

export interface MealOption {
  id: string;
  name: string;
  price_delta: number;
  position: number;
  is_active: boolean;
}

export interface MealOptionGroup {
  id: string;
  name: string;
  min_selections: number;
  max_selections: number;
  position: number;
  is_active: boolean;
  options: MealOption[];
}

export interface MealOptionSelection {
  group_id?: string | null;
  group_name: string;
  option_id?: string | null;
  option_name: string;
  price_delta: number;
}

export interface MealEvent {
  id: string;
  title: string;
  location: string;
  pickup_starts_at: string;
  pickup_ends_at: string;
  ordering_starts_at: string;
  ordering_ends_at: string;
  status: string;
  offerings: Array<{
    id: string;
    meal_id: string;
    meal_name: string;
    description: string;
    image_url?: string | null;
    price: number;
    capacity: number;
    reserved_quantity: number;
    paid_quantity: number;
    available_quantity: number;
    position: number;
    is_active: boolean;
    option_groups: MealOptionGroup[];
  }>;
}

export interface Meal {
  id: string;
  slug: string;
  name: string;
  description: string;
  image_url?: string | null;
  price: number;
  tax_type: "taxable" | "tax_exempt";
  is_active: boolean;
  option_groups: MealOptionGroup[];
}

export interface MealOrder {
  id: string;
  order_number: string;
  sales_channel: "meal_preorder";
  fulfillment_method: "event_pickup";
  meal_event_id: string;
  meal_event_title: string;
  venue_name: string;
  pickup_start: string;
  pickup_end: string;
  pickup_code?: string | null;
  pickup_qr_payload?: string | null;
  payment_status: string;
  invoice_status: string;
  fulfillment_status: string;
  paid_at?: string | null;
  cancelled_at?: string | null;
  amount_total: number;
  created_at: string;
  available_actions: string[];
  items: Array<{
    offering_id: string;
    meal_id: string;
    meal_name: string;
    quantity: number;
    base_price: number;
    option_price: number;
    unit_price: number;
    subtotal: number;
    selections: MealOptionSelection[];
  }>;
}

export interface MealPickupCredential {
  order_id: string;
  pickup_code: string;
  qr_token: string;
}

export type SupplierAccreditationStatus = "pending" | "approved" | "rejected";

export interface Supplier {
  id: string;
  supplier_number?: string | null;
  business_name: string;
  tax_id?: string | null;
  responsible_person: string;
  contact_person: string;
  phone: string;
  email: string;
  line_id?: string | null;
  settlement_terms: string;
  bank_account: string;
  accredited_on?: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  accreditations: Array<{
    id: string;
    supplier_id: string;
    reviewed_on: string;
    reviewer_id: string;
    status: SupplierAccreditationStatus;
    process_notes: string;
    result_notes: string;
    created_at: string;
  }>;
  documents: Array<{
    id: string;
    supplier_id: string;
    accreditation_id?: string | null;
    label: string;
    content_type: string;
    size_bytes: number;
    confirmed_at?: string | null;
    created_at: string;
  }>;
}

export interface SalesBreakdownReport {
  starts_on: string;
  ends_on: string;
  total_revenue: number;
  nonmember_revenue: number;
  nonmember_ratio: number;
  trainee_revenue: number;
  trainee_ratio: number;
  member_revenue: number;
  member_ratio: number;
  warning_threshold: number;
  legal_limit: number;
  headroom_amount: number;
  level: "normal" | "warning" | "limit";
  transactions_blocked: boolean;
}

export interface TaxLedgerReport {
  starts_on: string;
  ends_on: string;
  rows: Array<{
    tax_type: string;
    membership_type: string;
    sales_channel: string;
    sales_amount: number;
    tax_amount: number;
    order_count: number;
  }>;
}

export interface SurplusPreview {
  label: string;
  starts_on: string;
  ends_on: string;
  total_revenue: number;
  total_cost: number;
  total_surplus: number;
  reserve_percentage: number;
  reserve_amount: number;
  distributable_surplus: number;
  distributions: Array<{
    member_id: string;
    member_name: string;
    contribution_amount: number;
    contribution_basis_points: number;
    distribution_amount: number;
  }>;
}

export interface MembershipSummary {
  membership_type?: string;
  membership?: {
    id?: string;
    status?: string;
    member_number?: string | null;
    trainee_number?: string | null;
    share_certificate_number?: string | null;
    share_capital_amount?: number;
    share_count?: number;
    share_subscribed_on?: string | null;
    share_paid_on?: string | null;
  } | null;
  directory?: {
    nickname?: string;
    expertise?: string;
    bio?: string;
    is_public?: boolean;
  } | null;
}

export interface MembershipCharge {
  id: string;
  charge_kind: string;
  amount: number;
  status: string;
  receipt_number?: string | null;
  paid_at?: string | null;
  refunded_at?: string | null;
}

export interface PointSummary {
  balance: number;
  transactions: Array<{
    id: string;
    amount: number;
    source_type: string;
    reference_id?: string | null;
    note?: string | null;
    created_at: string;
  }>;
}

export interface MemberBadge {
  key: string;
  name: string;
  description: string;
  granted_at: string;
}

export interface SurplusDistribution {
  fiscal_year_id: string;
  label: string;
  contribution_amount: number;
  contribution_basis_points: number;
  distribution_amount: number;
  distributable_surplus: number;
  confirmed_at?: string | null;
}

export interface Notification {
  id: string;
  event_type: string;
  title: string;
  body: string;
  data: Record<string, unknown>;
  read_at?: string | null;
  created_at: string;
}

export interface CartItem {
  product: Product;
  quantity: number;
}

export interface OrderQuote {
  membership_type: string;
  amount_total: number;
  items: Array<{
    product_id: string;
    product_name: string;
    unit_label: string;
    quantity: number;
    unit_price: number;
    subtotal: number;
    tax_type: string;
  }>;
}
