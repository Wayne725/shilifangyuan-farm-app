export type UserRole = "customer" | "admin";
export type MembershipType = "member" | "nonmember";

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
  available_quantity: number;
  created_at: string;
};

export type OrderKind = "regular" | "group";
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
};

export type NotificationKind =
  | "proposal"
  | "group"
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
