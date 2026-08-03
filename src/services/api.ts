import {
  demoBundles,
  demoCampaigns,
  demoActivities,
  demoMeals,
  demoMealEvents,
  demoMealOrders,
  demoMemberDirectory,
  demoMemberProposals,
  demoMembershipApplications,
  demoMembershipCharges,
  demoMemberships,
  demoNotifications,
  demoOrders,
  demoProducts,
  demoProposals,
  demoUsers,
} from "../data/demoData";
import type {
  AdminActivityRegistration,
  AppNotification,
  AuthSession,
  CartItem,
  FulfillmentMethod,
  FulfillmentStatus,
  GroupBundle,
  GroupCampaign,
  GroupJoinQuote,
  InvoiceCarrierType,
  LogisticsProvider,
  MealEvent,
  Meal,
  MealOrder,
  MealPickupRedemption,
  MemberActivity,
  MemberDirectoryEntry,
  Membership,
  MembershipApplication,
  MembershipCharge,
  MembershipDocumentRead,
  MembershipDocumentUpload,
  MemberProposal,
  MemberProposalComment,
  MemberProposalNamedVote,
  MemberVoteChoice,
  Order,
  PaymentAttempt,
  LogisticsSelection,
  Product,
  Shipment,
  ShippingRate,
  TemperatureZone,
  User,
  VoteProposal,
} from "../types";
import {
  normalizeActivityRead,
  normalizeAdminActivityRegistrationRead,
  normalizeCampaignRead,
  normalizeLogisticsSelectionRead,
  normalizeMealEventRead,
  normalizeMealRead,
  normalizeMealOrderRead,
  normalizeMemberDirectoryRead,
  normalizeMemberProposalCommentRead,
  normalizeMemberProposalNamedVoteRead,
  normalizeMemberProposalRead,
  normalizeMembershipApplicationRead,
  normalizeMembershipChargeRead,
  normalizeMembershipRead,
  normalizeOrderRead,
  normalizeProductRead,
  normalizeShipmentRead,
} from "./adapters";
import {
  activateDemoMembership,
  canPayDemoMembershipCharge,
  canWithdrawDemoMembershipApplication,
  createPendingDemoMembership,
  findDemoMembershipApplication,
  isDemoMembershipApplicationEditable,
} from "./demoMembership";

declare const process: {
  env: Record<string, string | undefined>;
};

const apiBaseUrl = process.env.EXPO_PUBLIC_API_URL?.replace(/\/$/, "") ?? "";
let accessToken: string | null = null;
let refreshToken: string | null = null;
let onTokensRefreshed: ((session: AuthSession) => void) | null = null;
let onSessionExpired: (() => void) | null = null;
/** In-flight refresh, so concurrent 401s wait on one call instead of racing. */
let refreshInFlight: Promise<string | null> | null = null;

class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

type RequestOptions = {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  token?: string | null;
};

async function performRefresh(): Promise<string | null> {
  if (!refreshToken) return null;
  const response = await fetch(`${apiBaseUrl}/v1/auth/refresh`, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  }).catch(() => null);
  if (!response?.ok) return null;
  const session = (await response.json().catch(() => null)) as
    | AuthSession
    | null;
  if (!session?.access_token) return null;
  accessToken = session.access_token;
  refreshToken = session.refresh_token ?? refreshToken;
  onTokensRefreshed?.(session);
  return session.access_token;
}

/** Refreshes at most once per burst of 401s. */
function refreshAccessToken(): Promise<string | null> {
  refreshInFlight ??= performRefresh().finally(() => {
    refreshInFlight = null;
  });
  return refreshInFlight;
}

async function send(path: string, options: RequestOptions, token: string | null) {
  return fetch(`${apiBaseUrl}${path}`, {
    method: options.method ?? "GET",
    headers: {
      Accept: "application/json",
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    ...(options.body ? { body: JSON.stringify(options.body) } : {}),
  }).catch(() => {
    throw new ApiError("無法連線到服務", 0);
  });
}

async function request<T>(path: string, options: RequestOptions = {}) {
  if (!apiBaseUrl) {
    throw new ApiError("API 尚未設定", 0);
  }

  const explicitToken = options.token !== undefined;
  let response = await send(path, options, options.token ?? accessToken);

  // The access token lives 30 minutes; renew it silently rather than dumping
  // the buyer back to the login screen mid-checkout.
  if (
    response.status === 401 &&
    !explicitToken &&
    refreshToken &&
    !path.startsWith("/v1/auth/refresh")
  ) {
    const renewed = await refreshAccessToken();
    if (renewed) {
      response = await send(path, options, renewed);
    } else {
      onSessionExpired?.();
    }
  }

  if (!response.ok) {
    const data = (await response.json().catch(() => null)) as {
      detail?: string;
      message?: string;
    } | null;
    throw new ApiError(
      data?.detail ?? data?.message ?? "服務暫時無法完成操作",
      response.status,
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

async function optionalRequest<T>(path: string, options: RequestOptions = {}) {
  try {
    return await request<T>(path, options);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

async function fallback<T>(network: () => Promise<T>, demo: () => Promise<T>) {
  return apiBaseUrl ? network() : demo();
}

function settledDemoMembershipCharges() {
  return structuredCloneSafe(demoMembershipCharges).map((charge, index) => ({
    ...charge,
    payment_status: "paid" as const,
    receipt_number:
      charge.receipt_number ?? `RCPT-260730-${String(index + 2).padStart(3, "0")}`,
    paid_at: charge.paid_at ?? "2026-07-30T01:05:00.000Z",
  }));
}

function createDemoMembershipChargeState(): Record<
  string,
  MembershipCharge[]
> {
  return {
    "user-member": settledDemoMembershipCharges(),
    "user-customer": [],
    "user-applicant-supplement": [],
    "user-applicant-payment": structuredCloneSafe(demoMembershipCharges),
    "user-admin": settledDemoMembershipCharges(),
  };
}

function createDemoNotificationState(): Record<string, AppNotification[]> {
  return {
    "user-member": structuredCloneSafe(demoNotifications),
    "user-customer": [],
    "user-applicant-supplement": [],
    "user-applicant-payment": [],
    "user-admin": [],
  };
}

const demoState = {
  users: structuredCloneSafe(demoUsers),
  products: demoProductsWithLogistics(),
  proposals: structuredCloneSafe(demoProposals),
  campaigns: structuredCloneSafe(demoCampaigns),
  orders: structuredCloneSafe(demoOrders),
  notifications: createDemoNotificationState(),
  mealEvents: structuredCloneSafe(demoMealEvents),
  meals: structuredCloneSafe(demoMeals),
  mealOrders: structuredCloneSafe(demoMealOrders),
  membershipApplications: structuredCloneSafe(demoMembershipApplications),
  memberships: structuredCloneSafe(demoMemberships),
  membershipCharges: createDemoMembershipChargeState(),
  memberDirectory: structuredCloneSafe(demoMemberDirectory),
  activities: structuredCloneSafe(demoActivities),
  memberProposals: structuredCloneSafe(demoMemberProposals),
  activityRegistrations: createDemoActivityRegistrations(),
};

function structuredCloneSafe<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function demoRegistration(
  status: ApiActivityRegistration["status"] | null,
): ApiActivityRegistration {
  return {
    id: `registration-${Date.now()}`,
    user_id: getDemoUser().id,
    status: status ?? "cancelled",
    queue_position: 1,
    registered_at: new Date().toISOString(),
    cancelled_at: status === "cancelled" ? new Date().toISOString() : null,
    checked_in_at: null,
  };
}

function createDemoActivityRegistrations(): Record<
  string,
  AdminActivityRegistration[]
> {
  return Object.fromEntries(
    demoActivities.map((activity, activityIndex) => {
      const registered = Array.from(
        { length: activity.registered_count },
        (_, index): AdminActivityRegistration => ({
          id: `${activity.id}-registration-${index + 1}`,
          user_id: `${activity.id}-member-${index + 1}`,
          display_name:
            index === 0
              ? "社員 雨青"
              : index === 1
                ? "社員 阿禾"
                : `社員 ${String(index + 1).padStart(2, "0")}`,
          email: `member${activityIndex + 1}${String(index + 1).padStart(2, "0")}@demo.shilifangyuan.tw`,
          status: "registered",
          queue_position: index + 1,
          registered_at: activity.registration_deadline,
          cancelled_at: null,
          checked_in_at: null,
        }),
      );
      const waitlisted = Array.from(
        { length: activity.waitlist_count },
        (_, index): AdminActivityRegistration => ({
          id: `${activity.id}-waitlist-${index + 1}`,
          user_id: `${activity.id}-waitlist-member-${index + 1}`,
          display_name: `候補社員 ${index + 1}`,
          email: `waitlist${activityIndex + 1}${index + 1}@demo.shilifangyuan.tw`,
          status: "waitlisted",
          queue_position: registered.length + index + 1,
          registered_at: activity.registration_deadline,
          cancelled_at: null,
          checked_in_at: null,
        }),
      );
      return [activity.id, [...registered, ...waitlisted]];
    }),
  );
}

/** Mirrors the rates seeded by `backend/app/seed.py`. */
const demoShippingRates: ShippingRate[] = (
  [
    ["home_delivery", "ambient", 160],
    ["home_delivery", "chilled", 220],
    ["home_delivery", "frozen", 260],
    ["seven_eleven", "ambient", 70],
    ["family_mart", "ambient", 70],
    ["hilife", "ambient", 70],
  ] as const
).map(([channel, temperature, fee], index) => ({
  id: `rate-${index + 1}`,
  channel,
  temperature,
  fee,
  free_shipping_threshold: 1500,
  effective_from: "2026-01-01",
  effective_to: null,
  is_active: true,
}));

function demoProductsWithLogistics(): Product[] {
  return structuredCloneSafe(demoProducts).map((product) => {
    const chilled = ["tomatoes", "bok-choy", "fruit-corn", "eggs", "soy-eggs"].includes(
      product.id,
    );
    return {
      ...product,
      is_shippable: true,
      temperature_zone: chilled ? ("chilled" as const) : ("ambient" as const),
      allowed_logistics: chilled
        ? (["home_delivery"] as LogisticsProvider[])
        : ([
            "home_delivery",
            "seven_eleven",
            "family_mart",
            "hilife",
          ] as LogisticsProvider[]),
    };
  });
}

function getDemoUser(): User {
  const userId = accessToken?.replace("demo:", "");
  return (
    Object.values(demoState.users).find(({ user }) => user.id === userId)?.user ??
    {
      id: "guest",
      email: "",
      display_name: "訪客",
      user_role: "customer",
      membership_type: "nonmember",
    }
  );
}

function nextDemoMemberNumber() {
  const highest = Object.values(demoState.memberships).reduce(
    (current, membership) => {
      const parts = membership?.member_number?.split("-") ?? [];
      const sequence = Number(parts[parts.length - 1]);
      return Number.isFinite(sequence) ? Math.max(current, sequence) : current;
    },
    0,
  );
  return `SLF-${new Date().getFullYear()}-${String(highest + 1).padStart(4, "0")}`;
}

function demoPrice(product: Product, user = getDemoUser()) {
  return user.membership_type === "member"
    ? product.member_price
    : product.nonmember_price;
}

function orderNumber(prefix: "SLF" | "GB") {
  const now = new Date();
  const date = [
    String(now.getFullYear()).slice(-2),
    String(now.getMonth() + 1).padStart(2, "0"),
    String(now.getDate()).padStart(2, "0"),
  ].join("");
  return `${prefix}-${date}-${String(demoState.orders.length + 19).padStart(3, "0")}`;
}

type ApiNotification = {
  id: string;
  event_type: string;
  title: string;
  body: string;
  data?: Record<string, unknown> | null;
  read_at?: string | null;
  created_at: string;
};

type ApiActivityRegistration = {
  id: string;
  user_id: string;
  status: "registered" | "waitlisted" | "cancelled" | "attended" | "no_show";
  queue_position: number;
  registered_at: string;
  cancelled_at?: string | null;
  checked_in_at?: string | null;
};

function normalizeNotification(notice: ApiNotification): AppNotification {
  const event = notice.event_type.toLowerCase();
  const kind: AppNotification["kind"] = event.includes("proposal")
    ? "proposal"
    : event.includes("membership")
      ? "membership"
    : event.includes("payment") || event.includes("refund")
      ? "payment"
      : event.includes("pickup")
        ? "pickup"
        : event.includes("invoice")
          ? "invoice"
          : "group";
  const data = notice.data ?? {};
  const route =
    typeof data.route === "string"
      ? data.route
      : typeof data.order_id === "string"
        ? `/order/${data.order_id}`
        : typeof data.campaign_id === "string"
          ? `/campaign/${data.campaign_id}`
          : typeof data.proposal_id === "string"
            ? `/proposal/${data.proposal_id}`
            : null;
  return {
    id: notice.id,
    kind,
    title: notice.title,
    body: notice.body,
    created_at: notice.created_at,
    read_at: notice.read_at,
    route,
  };
}

function normalizeOrder(order: Order) {
  return normalizeOrderRead(order);
}

type MembershipApplicationInput = Omit<
  MembershipApplication,
  | "id"
  | "user_id"
  | "status"
  | "submitted_at"
  | "required_documents"
  | "confirmed_documents"
  | "review_note"
>;

function membershipApplicationPayload(input: MembershipApplicationInput) {
  return {
    legal_name: input.legal_name.trim(),
    phone: input.phone.trim(),
    birth_date: input.birth_date.trim(),
    address: input.address.trim(),
    emergency_contact: [
      input.emergency_contact_name.trim(),
      input.emergency_contact_phone.trim(),
    ].join("｜"),
    consent_version: "sandbox-v1",
  };
}

export function setApiAccessToken(token: string | null) {
  accessToken = token;
}

export function setApiSession(session: AuthSession | null) {
  accessToken = session?.access_token ?? null;
  refreshToken = session?.refresh_token ?? null;
}

/** Lets AuthContext persist rotated tokens and react to an expired session. */
export function setSessionHandlers(handlers: {
  onRefreshed: (session: AuthSession) => void;
  onExpired: () => void;
}) {
  onTokensRefreshed = handlers.onRefreshed;
  onSessionExpired = handlers.onExpired;
}

export function getApiBaseUrl() {
  return apiBaseUrl;
}

export const api = {
  login(email: string, password: string) {
    return fallback(
      () =>
        request<AuthSession>("/v1/auth/login", {
          method: "POST",
          body: { email, password },
        }),
      async () => {
        const account = demoState.users[email.toLowerCase()];
        if (!account || account.password !== password) {
          throw new ApiError("帳號或密碼不正確", 401);
        }
        return {
          access_token: `demo:${account.user.id}`,
          refresh_token: `demo-refresh:${account.user.id}`,
          token_type: "bearer",
          user: structuredCloneSafe(account.user),
        };
      },
    );
  },

  me() {
    return fallback(
      () => request<User>("/v1/auth/me"),
      async () => structuredCloneSafe(getDemoUser()),
    );
  },

  products() {
    return fallback(
      () =>
        request<unknown[]>("/v1/products").then((products) =>
          products.map(normalizeProductRead),
        ),
      async () => {
        const products =
          getDemoUser().user_role === "admin"
            ? demoState.products
            : demoState.products.filter((product) => product.is_active);
        return structuredCloneSafe(products);
      },
    );
  },

  bundles() {
    return fallback(
      () => request<GroupBundle[]>("/v1/group-bundles"),
      async () => structuredCloneSafe(demoBundles),
    );
  },

  proposals() {
    return fallback(
      () => request<VoteProposal[]>("/v1/vote-proposals"),
      async () => structuredCloneSafe(demoState.proposals),
    );
  },

  async proposal(id: string) {
    return fallback(
      () => request<VoteProposal>(`/v1/vote-proposals/${id}`),
      async () => {
        const proposal = demoState.proposals.find((item) => item.id === id);
        if (!proposal) throw new ApiError("找不到這筆提案", 404);
        return structuredCloneSafe(proposal);
      },
    );
  },

  createProposal(target_type: "product" | "bundle", target_id: string) {
    return fallback(
      () =>
        request<VoteProposal>("/v1/vote-proposals", {
          method: "POST",
          body: { target_type, target_id },
        }),
      async () => {
        const duplicate = demoState.proposals.find(
          (item) =>
            item.target_type === target_type &&
            item.target_id === target_id &&
            ["pending_review", "voting", "conversion_pending"].includes(
              item.status,
            ),
        );
        if (duplicate) return structuredCloneSafe(duplicate);
        const target =
          target_type === "product"
            ? demoState.products.find((item) => item.id === target_id)
            : demoBundles.find((item) => item.id === target_id);
        if (!target) throw new ApiError("找不到提案商品", 404);
        const proposal: VoteProposal = {
          id: `proposal-${Date.now()}`,
          target_type,
          target_id,
          target_name: target.name,
          status: "pending_review",
          threshold: 10,
          deadline: new Date(Date.now() + 7 * 86400000).toISOString(),
          vote_count: 0,
          estimated_quantity: 0,
          my_vote_quantity: null,
          created_at: new Date().toISOString(),
        };
        demoState.proposals.unshift(proposal);
        return structuredCloneSafe(proposal);
      },
    );
  },

  voteProposal(id: string, estimated_quantity: number) {
    return fallback(
      () =>
        request<VoteProposal>(`/v1/vote-proposals/${id}/vote`, {
          method: "PUT",
          body: { estimated_quantity },
        }),
      async () => {
        const proposal = demoState.proposals.find((item) => item.id === id);
        if (!proposal) throw new ApiError("找不到這筆提案", 404);
        const wasVoting = Boolean(proposal.my_vote_quantity);
        proposal.estimated_quantity +=
          estimated_quantity - (proposal.my_vote_quantity ?? 0);
        proposal.my_vote_quantity = estimated_quantity;
        if (!wasVoting) proposal.vote_count += 1;
        return structuredCloneSafe(proposal);
      },
    );
  },

  withdrawVote(id: string) {
    return fallback(
      () =>
        request<void>(`/v1/vote-proposals/${id}/vote`, {
          method: "DELETE",
        }),
      async () => {
        const proposal = demoState.proposals.find((item) => item.id === id);
        if (!proposal) return;
        proposal.estimated_quantity -= proposal.my_vote_quantity ?? 0;
        if (proposal.my_vote_quantity) proposal.vote_count -= 1;
        proposal.my_vote_quantity = null;
      },
    );
  },

  campaigns() {
    return fallback(
      () =>
        request<unknown[]>("/v1/group-campaigns").then((campaigns) =>
          campaigns.map(normalizeCampaignRead),
        ),
      async () => structuredCloneSafe(demoState.campaigns),
    );
  },

  async campaign(id: string) {
    return fallback(
      () =>
        request<unknown>(`/v1/group-campaigns/${id}`).then(
          normalizeCampaignRead,
        ),
      async () => {
        const campaign = demoState.campaigns.find((item) => item.id === id);
        if (!campaign) throw new ApiError("找不到這個共同購買", 404);
        return structuredCloneSafe(campaign);
      },
    );
  },

  quoteCampaign(
    id: string,
    input: {
      quantity: number;
      fulfillment_method: FulfillmentMethod;
      shipping_channel?: LogisticsProvider;
    },
  ) {
    return fallback<GroupJoinQuote>(
      () =>
        request<GroupJoinQuote>(`/v1/group-campaigns/${id}/quote`, {
          method: "POST",
          body: input,
        }),
      async () => {
        const campaign = demoState.campaigns.find((item) => item.id === id);
        if (!campaign) throw new ApiError("找不到這個共同購買", 404);
        const membershipType = getDemoUser().membership_type;
        const unitPrice =
          membershipType === "member"
            ? campaign.member_price
            : campaign.nonmember_price;
        const productSubtotal = unitPrice * input.quantity;
        const rate =
          input.fulfillment_method === "ecpay_logistics"
            ? demoShippingRates.find(
                (item) =>
                  item.channel === input.shipping_channel &&
                  item.temperature ===
                    (campaign.temperature_zone ?? "ambient"),
              )
            : undefined;
        const shippingFee =
          rate && productSubtotal < rate.free_shipping_threshold ? rate.fee : 0;
        return {
          membership_type: membershipType,
          quantity: input.quantity,
          unit_price: unitPrice,
          product_subtotal: productSubtotal,
          shipping_fee: shippingFee,
          amount_total: productSubtotal + shippingFee,
        };
      },
    );
  },

  joinCampaign(
    id: string,
    input: {
      quantity: number;
      contact_email: string;
      invoice_carrier_type: InvoiceCarrierType;
      invoice_carrier_value?: string;
      fulfillment_method?: FulfillmentMethod;
      logistics_provider?: LogisticsProvider;
      delivery_address?: string;
    },
  ) {
    return fallback(
      () =>
        request<unknown>(`/v1/group-campaigns/${id}/join`, {
          method: "POST",
          body: {
            quantity: input.quantity,
            contact_email: input.contact_email,
            invoice_carrier_type: input.invoice_carrier_type,
            ...(input.invoice_carrier_value
              ? { invoice_carrier_value: input.invoice_carrier_value }
              : {}),
          },
        }).then(normalizeOrderRead),
      async () => {
        const campaign = demoState.campaigns.find((item) => item.id === id);
        if (!campaign) throw new ApiError("找不到這個共同購買", 404);
        if (input.quantity > campaign.available_quantity) {
          throw new ApiError("剩餘數量不足", 409);
        }
        const user = getDemoUser();
        const unitPrice =
          user.membership_type === "member"
            ? campaign.member_price
            : campaign.nonmember_price;
        const subtotal = unitPrice * input.quantity;
        const usesLogistics = input.fulfillment_method === "ecpay_logistics";
        const shippingFee =
          usesLogistics && subtotal < 1500
            ? input.logistics_provider === "home_delivery"
              ? 160
              : 70
            : 0;
        const order: Order = {
          id: `order-${Date.now()}`,
          order_number: orderNumber("GB"),
          order_kind: "group",
          group_campaign_id: campaign.id,
          fulfillment_status: "pending_confirmation",
          payment_status: "pending",
          invoice_status: "not_eligible",
          membership_type_snapshot: user.membership_type,
          amount_total: subtotal + shippingFee,
          created_at: new Date().toISOString(),
          available_actions: ["pay", "cancel", "view"],
          items: [
            {
              product_name: campaign.title,
              quantity: input.quantity,
              unit_price: unitPrice,
              subtotal: unitPrice * input.quantity,
              tax_type: "tax_exempt",
            },
          ],
          sales_channel: "group",
          fulfillment: {
            method: usesLogistics
              ? "ecpay_logistics"
              : "cooperative_pickup",
            status: "pending_confirmation",
            ...(usesLogistics
              ? { address_summary: input.delivery_address ?? "配送地址" }
              : { venue_name: "十里方圓合作社" }),
          },
          shipment: usesLogistics
            ? {
                id: `shipment-${Date.now()}`,
                logistics_provider:
                  input.logistics_provider ?? "home_delivery",
                temperature_zone: "ambient",
                status: "draft",
                tracking_number: null,
                shipping_fee: shippingFee,
              }
            : null,
        };
        campaign.reserved_quantity += input.quantity;
        campaign.available_quantity -= input.quantity;
        demoState.orders.unshift(order);
        return structuredCloneSafe(order);
      },
    );
  },

  orders() {
    return fallback(
      () =>
        request<Order[]>("/v1/orders").then((orders) =>
          orders.map(normalizeOrder),
        ),
      async () => {
        const orders = structuredCloneSafe(demoState.orders);
        if (getDemoUser().user_role !== "admin") return orders;
        return orders.map((order) => {
          const actions = [...order.available_actions];
          if (
            order.payment_status === "paid" &&
            order.fulfillment_status !== "picked_up"
          ) {
            actions.push("refund");
          }
          const groupConfirmed =
            order.order_kind === "regular" ||
            demoState.campaigns.find(
              (campaign) => campaign.id === order.group_campaign_id,
            )?.decision_status === "confirmed";
          if (
            order.payment_status === "paid" &&
            groupConfirmed &&
            order.fulfillment_status === "pending_confirmation"
          ) {
            actions.push("start_preparing");
          } else if (order.fulfillment_status === "preparing") {
            actions.push("mark_ready");
          } else if (order.fulfillment_status === "ready_for_pickup") {
            actions.push("mark_picked_up");
          }
          return {
            ...order,
            available_actions: Array.from(new Set(actions)),
          };
        });
      },
    );
  },

  async order(id: string) {
    return fallback(
      () => request<Order>(`/v1/orders/${id}`).then(normalizeOrder),
      async () => {
        const order = demoState.orders.find((item) => item.id === id);
        if (!order) throw new ApiError("找不到這筆訂單", 404);
        return structuredCloneSafe(order);
      },
    );
  },

  quote(items: CartItem[]) {
    return fallback(
      () =>
        request<{
          amount_total: number;
          items: Order["items"];
        }>("/v1/orders/quote", {
          method: "POST",
          body: { items },
        }),
      async () => {
        const quotedItems = items.flatMap((item) => {
          const product = demoState.products.find(
            (candidate) => candidate.id === item.product_id,
          );
          if (!product) return [];
          const unitPrice = demoPrice(product);
          return [
            {
              product_id: product.id,
              product_name: product.name,
              quantity: item.quantity,
              unit_price: unitPrice,
              subtotal: unitPrice * item.quantity,
              tax_type: product.tax_type,
            },
          ];
        });
        return {
          items: quotedItems,
          amount_total: quotedItems.reduce(
            (sum, item) => sum + item.subtotal,
            0,
          ),
        };
      },
    );
  },

  /**
   * Creates the order only. Shipping is chosen afterwards through
   * `createLogisticsSelection`, which is what sets the fulfilment method,
   * the shipping fee and the final `amount_total`.
   */
  createOrder(input: {
    items: CartItem[];
    contact_email: string;
    invoice_carrier_type: InvoiceCarrierType;
    invoice_carrier_value?: string;
    fulfillment_method?: FulfillmentMethod;
    logistics_provider?: LogisticsProvider;
    delivery_address?: string;
  }) {
    return fallback(
      () =>
        request<unknown>("/v1/orders", {
          method: "POST",
          body: {
            items: input.items,
            contact_email: input.contact_email,
            invoice_carrier_type: input.invoice_carrier_type,
            ...(input.invoice_carrier_value
              ? { invoice_carrier_value: input.invoice_carrier_value }
              : {}),
          },
        }).then(normalizeOrderRead),
      async () => {
        const quote = await api.quote(input.items);
        const user = getDemoUser();
        const usesLogistics = input.fulfillment_method === "ecpay_logistics";
        const shippingFee =
          usesLogistics && quote.amount_total < 1500
            ? input.logistics_provider === "home_delivery"
              ? 160
              : 70
            : 0;
        const order: Order = {
          id: `order-${Date.now()}`,
          order_number: orderNumber("SLF"),
          order_kind: "regular",
          group_campaign_id: null,
          fulfillment_status: "pending_confirmation",
          payment_status: "pending",
          invoice_status: "not_eligible",
          membership_type_snapshot: user.membership_type,
          amount_total: quote.amount_total + shippingFee,
          created_at: new Date().toISOString(),
          available_actions: ["pay", "cancel", "view"],
          items: quote.items,
          sales_channel: "regular",
          fulfillment: {
            method: usesLogistics
              ? "ecpay_logistics"
              : "cooperative_pickup",
            status: "pending_confirmation",
            ...(usesLogistics
              ? { address_summary: input.delivery_address ?? "配送地址" }
              : { venue_name: "十里方圓合作社" }),
          },
          shipment: usesLogistics
            ? {
                id: `shipment-${Date.now()}`,
                logistics_provider:
                  input.logistics_provider ?? "home_delivery",
                temperature_zone: "ambient",
                status: "draft",
                tracking_number: null,
                shipping_fee: shippingFee,
              }
            : null,
        };
        demoState.orders.unshift(order);
        return structuredCloneSafe(order);
      },
    );
  },

  shippingRates() {
    return fallback<ShippingRate[]>(
      () => request<ShippingRate[]>("/v1/shipping-rates"),
      async () => structuredCloneSafe(demoShippingRates),
    );
  },

  /**
   * Locks in channel, temperature and recipient, then returns the URL of the
   * ECPay store picker. The picker page is deliberately token-authenticated:
   * a top-level browser navigation cannot send an Authorization header.
   */
  createLogisticsSelection(
    orderId: string,
    input: {
      channel: LogisticsProvider;
      temperature: TemperatureZone;
      recipient_name: string;
      recipient_phone: string;
      shipping_address: string;
    },
  ) {
    return fallback<LogisticsSelection>(
      () =>
        request<unknown>(
          `/v1/orders/${orderId}/logistics/selection`,
          { method: "POST", body: input },
        ).then(normalizeLogisticsSelectionRead),
      async () => {
        const order = demoState.orders.find((item) => item.id === orderId);
        if (!order) throw new ApiError("找不到這筆訂單", 404);
        const rate = demoShippingRates.find(
          (item) =>
            item.channel === input.channel &&
            item.temperature === input.temperature,
        );
        const subtotal = order.items.reduce(
          (sum, item) => sum + item.subtotal,
          0,
        );
        const shippingFee =
          !rate || subtotal >= rate.free_shipping_threshold ? 0 : rate.fee;
        order.amount_total = subtotal + shippingFee;
        order.fulfillment = {
          method: "ecpay_logistics",
          status: "pending_confirmation",
          address_summary: input.shipping_address,
        };
        order.shipment = {
          id: `shipment-${Date.now()}`,
          logistics_provider: input.channel,
          temperature_zone: input.temperature,
          status: "ready_to_create",
          tracking_number: null,
          shipping_fee: shippingFee,
        };
        return {
          shipment: structuredCloneSafe(order.shipment),
          selection_url: "",
          shipping_fee: shippingFee,
          product_subtotal: subtotal,
          amount_total: order.amount_total,
          expires_in_seconds: 1800,
        };
      },
    );
  },

  /** Re-opens the ECPay picker for an order whose selection was abandoned. */
  reissueLogisticsSelectionLink(orderId: string) {
    return fallback<LogisticsSelection>(
      () =>
        request<unknown>(
          `/v1/orders/${orderId}/logistics/selection-link`,
          { method: "POST" },
        ).then(normalizeLogisticsSelectionRead),
      async () => {
        const order = demoState.orders.find((item) => item.id === orderId);
        if (!order?.shipment) throw new ApiError("訂單尚未選擇物流", 404);
        order.shipment.status = "ready_to_create";
        return {
          shipment: structuredCloneSafe(order.shipment),
          selection_url: "",
          shipping_fee: order.shipment.shipping_fee,
          product_subtotal:
            order.amount_total - order.shipment.shipping_fee,
          amount_total: order.amount_total,
          expires_in_seconds: 1800,
        };
      },
    );
  },

  createPaymentAttempt(orderId: string) {
    return fallback<PaymentAttempt>(
      () =>
        request<{
          attempt_id: string;
          payment_url: string;
          status: PaymentAttempt["status"];
          expires_at: string;
        }>(`/v1/orders/${orderId}/payment-attempts`, {
          method: "POST",
        }).then((result) => ({
          id: result.attempt_id,
          order_id: orderId,
          payment_url: result.payment_url,
          status: result.status,
        })),
      async () => {
        const order = demoState.orders.find((item) => item.id === orderId);
        if (!order) throw new ApiError("找不到這筆訂單", 404);
        order.payment_status = "paid";
        order.paid_at = new Date().toISOString();
        order.available_actions = order.group_campaign_id
          ? ["cancel", "refund", "view"]
          : ["cancel", "refund", "start_preparing", "view"];
        if (order.group_campaign_id) {
          const campaign = demoState.campaigns.find(
            (item) => item.id === order.group_campaign_id,
          );
          if (campaign) {
            const quantity = order.items[0]?.quantity ?? 0;
            campaign.reserved_quantity = Math.max(
              0,
              campaign.reserved_quantity - quantity,
            );
            campaign.paid_quantity += quantity;
            if (campaign.paid_quantity >= campaign.min_paid_quantity) {
              campaign.decision_status = "pending_confirmation";
              campaign.intake_status = "paused";
            }
          }
        }
        if (order.sales_channel === "meal_preorder") {
          const mealOrder = demoState.mealOrders.find(
            (item) => item.id === order.id,
          );
          if (mealOrder) {
            const pickupCode = String(
              Math.floor(100000 + Math.random() * 900000),
            );
            mealOrder.payment_status = "paid";
            mealOrder.fulfillment_status = "pending";
            mealOrder.pickup_code = pickupCode;
            mealOrder.pickup_qr_payload = `shilifangyuan://meal-pickup/${order.id}/${pickupCode}`;
            mealOrder.available_actions = ["cancel", "view"];
            order.fulfillment_status = "pending_confirmation";
            order.available_actions = ["cancel", "refund", "view"];
            order.pickup_code = pickupCode;
            if (order.fulfillment) {
              order.fulfillment.status = "pending_confirmation";
            }
          }
        }
        return {
          id: `payment-${Date.now()}`,
          order_id: orderId,
          payment_url: null,
          status: "paid" as const,
        };
      },
    );
  },

  cancelOrder(id: string) {
    return fallback(
      () =>
        request<Order>(`/v1/orders/${id}/cancel`, {
          method: "POST",
          body: { reason: "買家取消" },
        }),
      async () => {
        const order = demoState.orders.find((item) => item.id === id);
        if (!order) throw new ApiError("找不到這筆訂單", 404);
        order.fulfillment_status = "cancelled";
        order.payment_status =
          order.payment_status === "paid" ? "refund_pending" : "expired";
        order.available_actions = ["view"];
        return structuredCloneSafe(order);
      },
    );
  },

  notifications() {
    return fallback(
      () =>
        request<ApiNotification[]>("/v1/notifications").then((notices) =>
          notices.map(normalizeNotification),
        ),
      async () =>
        structuredCloneSafe(demoState.notifications[getDemoUser().id] ?? []),
    );
  },

  markNotificationRead(id: string) {
    return fallback(
      () =>
        request<ApiNotification>(`/v1/notifications/${id}/read`, {
          method: "PATCH",
        }).then(normalizeNotification),
      async () => {
        const notices = demoState.notifications[getDemoUser().id] ?? [];
        const notice = notices.find((item) => item.id === id);
        if (!notice) throw new ApiError("找不到通知", 404);
        notice.read_at = new Date().toISOString();
        return structuredCloneSafe(notice);
      },
    );
  },

  validateMobileBarcode(barcode: string) {
    return fallback(
      () =>
        request<{
          barcode: string;
          valid: boolean;
          provider_checked: boolean;
          message?: string;
        }>("/v1/invoice-carriers/mobile-barcode/validate", {
          method: "POST",
          body: { barcode },
        }),
      async () => ({
        barcode,
        valid: /^\/[0-9A-Z.+-]{7}$/.test(barcode),
        provider_checked: false,
        message: /^\/[0-9A-Z.+-]{7}$/.test(barcode)
          ? "載具格式正確"
          : "請輸入 / 開頭的 8 碼手機條碼",
      }),
    );
  },

  reviewProposal(
    id: string,
    action: "approve" | "reject",
    input?: { threshold?: number; deadline?: string; reason?: string },
  ) {
    return fallback(
      () =>
        request<VoteProposal>(`/v1/vote-proposals/${id}/admin/${action}`, {
          method: "POST",
          body: input,
        }),
      async () => {
        const proposal = demoState.proposals.find((item) => item.id === id);
        if (!proposal) throw new ApiError("找不到提案", 404);
        proposal.status = action === "approve" ? "voting" : "rejected";
        if (input?.threshold) proposal.threshold = input.threshold;
        if (input?.deadline) proposal.deadline = input.deadline;
        return structuredCloneSafe(proposal);
      },
    );
  },

  convertProposal(
    id: string,
    input: {
      source_proposal_id: string;
      target_type: "product" | "bundle";
      target_id: string;
      title: string;
      description: string;
      image_url?: string | null;
      member_price: number;
      nonmember_price: number;
      min_paid_quantity: number;
      supply_cap: number;
      per_user_cap: number;
      deadline: string;
      estimated_pickup_start: string;
      estimated_pickup_end: string;
      can_ship?: boolean;
      shipping_temperature?: TemperatureZone | null;
      allowed_shipping_channels?: LogisticsProvider[];
    },
  ) {
    return fallback(
      () =>
        request<unknown>(
          `/v1/vote-proposals/${id}/admin/convert`,
          {
            method: "POST",
            body: input,
          },
        ).then(normalizeCampaignRead),
      async () => {
        const proposal = demoState.proposals.find((item) => item.id === id);
        if (!proposal) throw new ApiError("找不到提案", 404);
        const campaign: GroupCampaign = {
          id: `campaign-${Date.now()}`,
          title: input.title,
          description: input.description,
          image_url: input.image_url,
          decision_status: "recruiting",
          intake_status: "open",
          member_price: input.member_price,
          nonmember_price: input.nonmember_price,
          min_paid_quantity: input.min_paid_quantity,
          supply_cap: input.supply_cap,
          per_user_cap: input.per_user_cap,
          paid_quantity: 0,
          reserved_quantity: 0,
          available_quantity: input.supply_cap,
          deadline: input.deadline,
          estimated_pickup_start: input.estimated_pickup_start,
          estimated_pickup_end: input.estimated_pickup_end,
          final_pickup_at: null,
          can_ship: input.can_ship ?? false,
          temperature_zone: input.can_ship
            ? (input.shipping_temperature ?? "ambient")
            : undefined,
          allowed_logistics: input.can_ship
            ? (input.allowed_shipping_channels ?? ["home_delivery"])
            : [],
          created_at: new Date().toISOString(),
        };
        proposal.status = "converted";
        demoState.campaigns.unshift(campaign);
        return structuredCloneSafe(campaign);
      },
    );
  },

  createCampaign(input: {
    target_type: "product" | "bundle";
    target_id: string;
    title: string;
    description: string;
    image_url?: string | null;
    member_price: number;
    nonmember_price: number;
    min_paid_quantity: number;
    supply_cap: number;
    per_user_cap: number;
    deadline: string;
    estimated_pickup_start: string;
    estimated_pickup_end: string;
    can_ship?: boolean;
    shipping_temperature?: TemperatureZone | null;
    allowed_shipping_channels?: LogisticsProvider[];
  }) {
    return fallback(
      () =>
        request<unknown>("/v1/group-campaigns", {
          method: "POST",
          body: input,
        }).then(normalizeCampaignRead),
      async () => {
        const campaign: GroupCampaign = {
          id: `campaign-${Date.now()}`,
          title: input.title,
          description: input.description,
          image_url: input.image_url,
          decision_status: "recruiting",
          intake_status: "open",
          member_price: input.member_price,
          nonmember_price: input.nonmember_price,
          min_paid_quantity: input.min_paid_quantity,
          supply_cap: input.supply_cap,
          per_user_cap: input.per_user_cap,
          paid_quantity: 0,
          reserved_quantity: 0,
          available_quantity: input.supply_cap,
          deadline: input.deadline,
          estimated_pickup_start: input.estimated_pickup_start,
          estimated_pickup_end: input.estimated_pickup_end,
          final_pickup_at: null,
          can_ship: input.can_ship ?? false,
          temperature_zone: input.can_ship
            ? (input.shipping_temperature ?? "ambient")
            : undefined,
          allowed_logistics: input.can_ship
            ? (input.allowed_shipping_channels ?? ["home_delivery"])
            : [],
          created_at: new Date().toISOString(),
        };
        demoState.campaigns.unshift(campaign);
        return structuredCloneSafe(campaign);
      },
    );
  },

  confirmCampaign(id: string, final_pickup_at: string) {
    return fallback(
      () =>
        request<unknown>(`/v1/group-campaigns/${id}/admin/confirm`, {
          method: "POST",
          body: { final_pickup_at },
        }).then(normalizeCampaignRead),
      async () => {
        const campaign = demoState.campaigns.find((item) => item.id === id);
        if (!campaign) throw new ApiError("找不到團購", 404);
        campaign.decision_status = "confirmed";
        campaign.intake_status =
          campaign.available_quantity > 0 ? "open" : "full";
        campaign.final_pickup_at = final_pickup_at;
        return structuredCloneSafe(campaign);
      },
    );
  },

  cancelCampaign(id: string, reason: string) {
    return fallback(
      () =>
        request<unknown>(`/v1/group-campaigns/${id}/admin/cancel`, {
          method: "POST",
          body: { reason },
        }).then(normalizeCampaignRead),
      async () => {
        const campaign = demoState.campaigns.find((item) => item.id === id);
        if (!campaign) throw new ApiError("找不到團購", 404);
        campaign.decision_status = "cancelled";
        campaign.intake_status = "closed";
        return structuredCloneSafe(campaign);
      },
    );
  },

  rejectCampaign(id: string, reason: string) {
    return fallback(
      () =>
        request<unknown>(
          `/v1/group-campaigns/${id}/admin/reject`,
          {
            method: "POST",
            body: { reason },
          },
        ).then(normalizeCampaignRead),
      async () => {
        const campaign = demoState.campaigns.find((item) => item.id === id);
        if (!campaign) throw new ApiError("找不到團購", 404);
        campaign.decision_status = "rejected";
        campaign.intake_status = "closed";
        return structuredCloneSafe(campaign);
      },
    );
  },

  adminRefundOrder(id: string, reason: string) {
    return fallback(
      () =>
        request<Order>(`/v1/orders/${id}/admin/refund`, {
          method: "POST",
          body: { reason },
        }),
      async () => {
        const order = demoState.orders.find((item) => item.id === id);
        if (!order) throw new ApiError("找不到訂單", 404);
        order.payment_status = "refund_pending";
        order.fulfillment_status = "cancelled";
        order.available_actions = ["view"];
        return structuredCloneSafe(order);
      },
    );
  },

  toggleProduct(id: string, is_active: boolean) {
    return fallback(
      () =>
        request<unknown>(`/v1/products/${id}`, {
          method: "PATCH",
          body: { is_active },
        }).then(normalizeProductRead),
      async () => {
        const product = demoState.products.find((item) => item.id === id);
        if (!product) throw new ApiError("找不到商品", 404);
        product.is_active = is_active;
        return structuredCloneSafe(product);
      },
    );
  },

  createProduct(input: {
    name: string;
    description: string;
    category: Product["category"];
    unit: string;
    member_price: number;
    nonmember_price: number;
    stock_quantity: number;
    tax_type: Product["tax_type"];
    is_shippable?: boolean;
    temperature_zone?: TemperatureZone;
    allowed_logistics?: LogisticsProvider[];
  }) {
    return fallback(
      () =>
        request<unknown>("/v1/products", {
          method: "POST",
          body: {
            name: input.name,
            description: input.description,
            category: input.category,
            unit: input.unit,
            member_price: input.member_price,
            nonmember_price: input.nonmember_price,
            stock_quantity: input.stock_quantity,
            tax_type: input.tax_type,
            image_url: null,
            can_ship: input.is_shippable ?? false,
            shipping_temperature: input.is_shippable
              ? (input.temperature_zone ?? "ambient")
              : null,
            allowed_shipping_channels: input.is_shippable
              ? (input.allowed_logistics ?? ["home_delivery"])
              : [],
            is_active: true,
          },
        }).then(normalizeProductRead),
      async () => {
        if (getDemoUser().user_role !== "admin") {
          throw new ApiError("需要管理權限", 403);
        }
        const product: Product = {
          id: `product-${Date.now()}`,
          name: input.name,
          description: input.description,
          category: input.category,
          unit: input.unit,
          member_price: input.member_price,
          nonmember_price: input.nonmember_price,
          stock: input.stock_quantity,
          stock_quantity: input.stock_quantity,
          tax_type: input.tax_type,
          is_shippable: input.is_shippable ?? true,
          temperature_zone: input.temperature_zone ?? "ambient",
          allowed_logistics: input.allowed_logistics ?? [
            "home_delivery",
            "seven_eleven",
            "family_mart",
            "hilife",
          ],
          is_active: true,
        };
        demoState.products.unshift(product);
        return structuredCloneSafe(product);
      },
    );
  },

  advanceOrder(id: string, status: Order["fulfillment_status"]) {
    return fallback(
      () =>
        request<Order>(`/v1/orders/${id}/fulfillment`, {
          method: "POST",
          body: { status },
        }),
      async () => {
        const order = demoState.orders.find((item) => item.id === id);
        if (!order) throw new ApiError("找不到訂單", 404);
        order.fulfillment_status = status;
        if (order.fulfillment) {
          order.fulfillment.status =
            status === "preparing"
              ? "preparing"
              : status === "ready_for_pickup"
                ? "ready_for_pickup"
                : status === "picked_up"
                  ? "picked_up"
                  : order.fulfillment.status;
        }
        order.available_actions =
          status === "preparing"
            ? order.shipment?.status === "ready_to_create"
              ? ["refund", "create_shipment", "view"]
              : ["refund", "mark_ready", "view"]
            : status === "ready_for_pickup"
              ? ["refund", "mark_picked_up", "view"]
              : ["view"];
        if (order.fulfillment_status === "picked_up") {
          order.invoice_status = "pending";
        }
        return structuredCloneSafe(order);
      },
    );
  },

  register(input: {
    email: string;
    password: string;
    display_name: string;
  }) {
    return fallback(
      () =>
        request<{ message: string }>("/v1/auth/register", {
          method: "POST",
          body: input,
        }),
      async () => ({ message: "驗證信已寄出，請完成 Email 驗證。" }),
    );
  },

  verifyEmail(token: string) {
    return fallback(
      () =>
        request<{ message: string }>("/v1/auth/verify-email", {
          method: "POST",
          body: { token },
        }),
      async () => ({ message: "Email 已完成驗證。" }),
    );
  },

  resendVerification(email: string) {
    return fallback(
      () =>
        request<{ message: string }>("/v1/auth/resend-verification", {
          method: "POST",
          body: { email },
        }),
      async () => ({ message: "驗證信已重新寄出。" }),
    );
  },

  forgotPassword(email: string) {
    return fallback(
      () =>
        request<{ message: string }>("/v1/auth/forgot-password", {
          method: "POST",
          body: { email },
        }),
      async () => ({ message: "若帳號存在，重設密碼信將寄到信箱。" }),
    );
  },

  resetPassword(token: string, password: string) {
    return fallback(
      () =>
        request<{ message: string }>("/v1/auth/reset-password", {
          method: "POST",
          body: { token, password },
        }),
      async () => ({ message: "密碼已更新，請重新登入。" }),
    );
  },

  membershipApplication() {
    return fallback(
      () =>
        optionalRequest<unknown>("/v1/membership/application").then((value) =>
          value ? normalizeMembershipApplicationRead(value) : null,
        ),
      async () => {
        const user = getDemoUser();
        const application = findDemoMembershipApplication(
          demoState.membershipApplications,
          user.id,
        );
        return application ? structuredCloneSafe(application) : null;
      },
    );
  },

  saveMembershipApplication(
    input: MembershipApplicationInput,
  ) {
    return fallback(
      () =>
        request<unknown>("/v1/membership/application", {
          method: "PUT",
          body: membershipApplicationPayload(input),
        }).then(normalizeMembershipApplicationRead),
      async () => {
        const user = getDemoUser();
        let application = findDemoMembershipApplication(
          demoState.membershipApplications,
          user.id,
        );
        if (!application) {
          application = {
            id: `application-${Date.now()}`,
            user_id: user.id,
            status: "draft",
            ...input,
            submitted_at: null,
            review_note: null,
            required_documents: ["id_front", "id_back", "secondary"],
            confirmed_documents: [],
          };
          demoState.membershipApplications.unshift(application);
        } else {
          if (!isDemoMembershipApplicationEditable(application)) {
            throw new ApiError("此申請目前不可修改", 409);
          }
          Object.assign(application, input);
        }
        return structuredCloneSafe(application);
      },
    );
  },

  submitMembershipApplication(input: MembershipApplicationInput) {
    return fallback(
      async () => {
        const saved = normalizeMembershipApplicationRead(
          await request<unknown>("/v1/membership/application", {
            method: "PUT",
            body: membershipApplicationPayload(input),
          }),
        );
        const action =
          saved.status === "needs_revision" ? "supplement" : "submit";
        return request<unknown>(`/v1/membership/application/${action}`, {
          method: "POST",
        }).then(normalizeMembershipApplicationRead);
      },
      async () => {
        const application = findDemoMembershipApplication(
          demoState.membershipApplications,
          getDemoUser().id,
        );
        if (!application) throw new ApiError("請先填寫入社資料", 400);
        if (!isDemoMembershipApplicationEditable(application)) {
          throw new ApiError("此申請目前不可重複送件", 409);
        }
        if (
          application.confirmed_documents.length <
          application.required_documents.length
        ) {
          throw new ApiError("請先補齊三份測試證件", 400);
        }
        Object.assign(application, input);
        application.status = "submitted";
        application.submitted_at = new Date().toISOString();
        application.review_note = null;
        return structuredCloneSafe(application);
      },
    );
  },

  withdrawMembershipApplication() {
    return fallback(
      () =>
        request<unknown>("/v1/membership/application/withdraw", {
          method: "POST",
          body: { reason: "申請人主動撤回入社申請" },
        }).then(normalizeMembershipApplicationRead),
      async () => {
        const user = getDemoUser();
        const application = findDemoMembershipApplication(
          demoState.membershipApplications,
          user.id,
        );
        if (!application) throw new ApiError("找不到入社申請", 404);
        const membership = demoState.memberships[user.id];
        if (
          !canWithdrawDemoMembershipApplication(
            application,
            membership ?? null,
          )
        ) {
          throw new ApiError(
            membership?.member_number || membership?.started_at
              ? "會籍啟用後不可撤回申請"
              : "此申請目前不可撤回",
            409,
          );
        }
        application.status = "withdrawn";
        for (const charge of demoState.membershipCharges[user.id] ?? []) {
          if (charge.payment_status === "paid") {
            charge.payment_status = "refunded";
          } else if (charge.payment_status === "pending") {
            charge.payment_status = "expired";
          }
        }
        if (membership?.status === "pending_payment") {
          membership.status = "terminated";
          membership.ended_at = new Date().toISOString();
          membership.status_reason = "入社申請已撤回";
        }
        user.membership_type = "nonmember";
        return structuredCloneSafe(application);
      },
    );
  },

  membershipDocumentUploadUrl(input: {
    document_type: "id_front" | "id_back" | "secondary";
    content_type: "image/jpeg" | "image/png" | "application/pdf";
    size_bytes: number;
    checksum_sha256: string;
  }) {
    return fallback<MembershipDocumentUpload>(
      () =>
        request<MembershipDocumentUpload>(
          "/v1/membership/documents/upload-url",
          { method: "POST", body: input },
        ),
      async () => ({
        document_id: `document-${Date.now()}-${input.document_type}`,
        // Non-https on purpose: demo mode must not attempt a real R2 PUT.
        upload_url: "sandbox://no-upload",
        object_key: `sandbox/${Date.now()}-${input.document_type}`,
        expires_in_seconds: 300,
        required_headers: {},
      }),
    );
  },

  confirmMembershipDocument(input: {
    document_id: string;
    document_type: "id_front" | "id_back" | "secondary";
    checksum_sha256: string;
  }) {
    return fallback(
      () =>
        request<MembershipDocumentRead>(
          `/v1/membership/documents/${input.document_id}/confirm`,
          {
            method: "POST",
            body: { checksum_sha256: input.checksum_sha256 },
          },
        ),
      async () => {
        const application = findDemoMembershipApplication(
          demoState.membershipApplications,
          getDemoUser().id,
        );
        if (!application) throw new ApiError("請先建立入社申請", 400);
        if (!isDemoMembershipApplicationEditable(application)) {
          throw new ApiError("此申請目前不可上傳證件", 409);
        }
        if (!application.confirmed_documents.includes(input.document_type)) {
          application.confirmed_documents.push(input.document_type);
        }
        application.documents = [
          ...(application.documents ?? []).filter(
            (document) => document.document_type !== input.document_type,
          ),
          {
            id: input.document_id,
            document_type: input.document_type,
            status: "confirmed" as const,
            checksum_sha256: input.checksum_sha256,
          },
        ];
        return {
          id: input.document_id,
          document_type: input.document_type,
          status: "confirmed" as const,
          checksum_sha256: input.checksum_sha256,
        };
      },
    );
  },

  deleteMembershipDocument(documentId: string) {
    return fallback(
      () =>
        request<void>(`/v1/membership/documents/${documentId}`, {
          method: "DELETE",
        }),
      async () => {
        const application = findDemoMembershipApplication(
          demoState.membershipApplications,
          getDemoUser().id,
        );
        if (!application) throw new ApiError("找不到入社申請", 404);
        if (
          ![
            "draft",
            "needs_revision",
            "rejected",
            "withdrawn",
          ].includes(application.status)
        ) {
          throw new ApiError("此申請目前不可刪除證件", 409);
        }
        const document = application.documents?.find(
          (item) => item.id === documentId,
        );
        if (document) {
          application.confirmed_documents =
            application.confirmed_documents.filter(
              (type) => type !== document.document_type,
            );
          application.documents = (application.documents ?? []).filter(
            (item) => item.id !== documentId,
          );
        }
        return undefined;
      },
    );
  },

  membership() {
    return fallback(
      () => request<unknown>("/v1/members/me").then(normalizeMembershipRead),
      async () =>
        structuredCloneSafe(demoState.memberships[getDemoUser().id] ?? null),
    );
  },

  membershipCharges() {
    return fallback(
      () =>
        request<unknown[]>("/v1/membership/charges").then((charges) =>
          charges.map(normalizeMembershipChargeRead),
        ),
      async () =>
        structuredCloneSafe(
          demoState.membershipCharges[getDemoUser().id] ?? [],
        ),
    );
  },

  payMembershipCharge(id: string) {
    return fallback<PaymentAttempt>(
      () =>
        request<{
          attempt_id: string;
          payment_url: string;
          status: PaymentAttempt["status"];
          expires_at: string;
        }>(`/v1/membership/charges/${id}/payment-attempts`, {
          method: "POST",
        }).then((result) => ({
          id: result.attempt_id,
          order_id: id,
          payment_url: result.payment_url,
          status: result.status,
        })),
      async () => {
        const user = getDemoUser();
        const charges = demoState.membershipCharges[user.id] ?? [];
        const charge = charges.find((item) => item.id === id);
        if (!charge) throw new ApiError("找不到應繳款", 404);
        const application = findDemoMembershipApplication(
          demoState.membershipApplications,
          user.id,
        );
        const membership = demoState.memberships[user.id];
        if (
          !canPayDemoMembershipCharge(
            application ?? null,
            membership ?? null,
            charge,
          )
        ) {
          throw new ApiError("此會籍目前不可付款", 409);
        }
        charge.payment_status = "paid";
        charge.paid_at = new Date().toISOString();
        charge.receipt_number = `RCPT-${String(Date.now()).slice(-9)}`;
        const activated = activateDemoMembership(
          user,
          membership ?? null,
          charges,
          nextDemoMemberNumber(),
          new Date().toISOString(),
        );
        if (activated && membership) {
          const notices = (demoState.notifications[user.id] ??= []);
          notices.unshift({
            id: `notification-membership-${Date.now()}`,
            kind: "membership",
            title: "十里方圓會籍已啟用",
            body: `社員編號 ${membership.member_number} 已啟用。`,
            route: "/members",
            read_at: null,
            created_at: new Date().toISOString(),
          });
        }
        return {
          id: `payment-${Date.now()}`,
          order_id: id,
          payment_url: null,
          status: "paid" as const,
        };
      },
    );
  },

  memberDirectory() {
    return fallback(
      () =>
        request<unknown[]>("/v1/members/directory").then((entries) =>
          entries.map(normalizeMemberDirectoryRead),
        ),
      async () => structuredCloneSafe(demoState.memberDirectory),
    );
  },

  updateMemberDirectory(input: {
    is_public: boolean;
    nickname: string;
    avatar_url?: string | null;
    expertise: string;
    bio: string;
  }) {
    return fallback(
      () =>
        request<unknown>("/v1/members/me/directory", {
          method: "PUT",
          body: input,
        }).then(normalizeMemberDirectoryRead),
      async () => {
        const user = getDemoUser();
        const membership = demoState.memberships[user.id];
        if (!membership || membership.status !== "active") {
          throw new ApiError("只有有效社員可以更新公開名錄", 403);
        }
        Object.assign(membership, {
          directory_visible: input.is_public,
          nickname: input.nickname,
          avatar_url: input.avatar_url ?? null,
          expertise: input.expertise,
          bio: input.bio,
        });
        const index = demoState.memberDirectory.findIndex(
          (entry) => entry.id === user.id || entry.member_number === membership.member_number,
        );
        const entry: MemberDirectoryEntry = {
          id: user.id,
          member_number: membership.member_number ?? "社員",
          is_public: input.is_public,
          nickname: input.nickname,
          avatar_url: input.avatar_url ?? null,
          expertise: input.expertise || null,
          bio: input.bio || null,
        };
        if (input.is_public) {
          if (index >= 0) demoState.memberDirectory[index] = entry;
          else demoState.memberDirectory.unshift(entry);
        } else if (index >= 0) {
          demoState.memberDirectory.splice(index, 1);
        }
        return structuredCloneSafe(entry);
      },
    );
  },

  activities() {
    return fallback(
      () =>
        request<unknown[]>("/v1/activities").then((activities) =>
          activities.map(normalizeActivityRead),
        ),
      async () => structuredCloneSafe(demoState.activities),
    );
  },

  createActivity(input: {
    title: string;
    description: string;
    venue_name: string;
    starts_at: string;
    ends_at: string;
    registration_deadline: string;
    capacity: number;
  }) {
    return fallback(
      () =>
        request<unknown>("/v1/activities", {
          method: "POST",
          body: {
            title: input.title,
            description: input.description,
            location: input.venue_name,
            starts_at: input.starts_at,
            ends_at: input.ends_at,
            registration_deadline: input.registration_deadline,
            capacity: input.capacity,
            waitlist_enabled: true,
          },
        }).then(normalizeActivityRead),
      async () => {
        const activity: MemberActivity = {
          id: `activity-${Date.now()}`,
          ...input,
          image_key: "member-hike",
          status: "pending_review",
          registered_count: 0,
          waitlist_count: 0,
          my_registration_status: null,
          created_by_name: getDemoUser().display_name,
        };
        demoState.activities.unshift(activity);
        return structuredCloneSafe(activity);
      },
    );
  },

  registerActivity(id: string) {
    return fallback<ApiActivityRegistration>(
      () =>
        request<ApiActivityRegistration>(`/v1/activities/${id}/register`, {
          method: "POST",
        }),
      async () => {
        const activity = demoState.activities.find((item) => item.id === id);
        if (!activity) throw new ApiError("找不到活動", 404);
        if (activity.registered_count >= activity.capacity) {
          activity.waitlist_count += 1;
          activity.my_registration_status = "waitlisted";
        } else {
          activity.registered_count += 1;
          activity.my_registration_status = "registered";
        }
        return demoRegistration(activity.my_registration_status);
      },
    );
  },

  cancelActivityRegistration(id: string) {
    return fallback<ApiActivityRegistration>(
      () =>
        request<ApiActivityRegistration>(
          `/v1/activities/${id}/cancel-registration`,
          { method: "POST" },
        ),
      async () => {
        const activity = demoState.activities.find((item) => item.id === id);
        if (!activity) throw new ApiError("找不到活動", 404);
        if (activity.my_registration_status === "registered") {
          if (activity.waitlist_count > 0) {
            activity.waitlist_count -= 1;
          } else {
            activity.registered_count = Math.max(
              0,
              activity.registered_count - 1,
            );
          }
        }
        if (activity.my_registration_status === "waitlisted") {
          activity.waitlist_count = Math.max(0, activity.waitlist_count - 1);
        }
        activity.my_registration_status = "cancelled";
        return demoRegistration("cancelled");
      },
    );
  },

  memberProposals() {
    return fallback(
      () =>
        request<unknown[]>("/v1/member-proposals").then((proposals) =>
          proposals.map(normalizeMemberProposalRead),
        ),
      async () => structuredCloneSafe(demoState.memberProposals),
    );
  },

  memberProposalComments(id: string) {
    return fallback<MemberProposalComment[]>(
      () =>
        request<unknown[]>(`/v1/member-proposals/${id}/comments`).then(
          (comments) => comments.map(normalizeMemberProposalCommentRead),
        ),
      async () => {
        const proposal = demoState.memberProposals.find((item) => item.id === id);
        if (!proposal) throw new ApiError("找不到社員提案", 404);
        return structuredCloneSafe(proposal.comments ?? []);
      },
    );
  },

  memberProposalVotes(id: string) {
    return fallback<MemberProposalNamedVote[]>(
      () =>
        request<unknown[]>(`/v1/member-proposals/${id}/votes`).then((items) =>
          items.map(normalizeMemberProposalNamedVoteRead),
        ),
      async () => {
        const proposal = demoState.memberProposals.find((item) => item.id === id);
        if (!proposal) throw new ApiError("找不到社員提案", 404);
        const choices: MemberVoteChoice[] = [
          ...Array<MemberVoteChoice>(proposal.yes_count).fill("yes"),
          ...Array<MemberVoteChoice>(proposal.no_count).fill("no"),
          ...Array<MemberVoteChoice>(proposal.abstain_count).fill("abstain"),
        ];
        return choices.map((choice, index) => ({
          user_id: `demo-voter-${index + 1}`,
          display_name: `社員 ${String(index + 1).padStart(2, "0")}`,
          choice,
          updated_at: proposal.voting_ends_at ?? new Date().toISOString(),
        }));
      },
    );
  },

  voteMemberProposal(id: string, choice: MemberVoteChoice) {
    return fallback(
      () =>
        request<unknown>(`/v1/member-proposals/${id}/vote`, {
          method: "PUT",
          body: { choice },
        }).then(normalizeMemberProposalRead),
      async () => {
        const proposal = demoState.memberProposals.find((item) => item.id === id);
        if (!proposal) throw new ApiError("找不到社員提案", 404);
        const countKey = (value: MemberVoteChoice) =>
          `${value}_count` as "yes_count" | "no_count" | "abstain_count";
        if (proposal.my_vote) {
          const previousKey = countKey(proposal.my_vote);
          proposal[previousKey] = Math.max(0, proposal[previousKey] - 1);
        }
        proposal[countKey(choice)] += 1;
        proposal.my_vote = choice;
        return structuredCloneSafe(proposal);
      },
    );
  },

  voteMemberProposalOption(id: string, optionId: string) {
    return request<unknown>(`/v1/member-proposals/${id}/vote`, {
      method: "PUT",
      body: { option_id: optionId },
    }).then(normalizeMemberProposalRead);
  },

  createMemberProposal(input: { title: string; summary: string; proposal_type?: "resolution" | "multiple_choice"; options?: string[] }) {
    return fallback(
      async () => {
        const draft = await request<{ id: string }>("/v1/member-proposals", {
          method: "POST",
          body: { title: input.title, body: input.summary, proposal_type: input.proposal_type ?? "resolution", options: (input.options ?? []).map((label) => ({ label })) },
        });
        return request<unknown>(`/v1/member-proposals/${draft.id}/submit`, {
          method: "POST",
        }).then(normalizeMemberProposalRead);
      },
      async () => {
        const proposal: MemberProposal = {
          id: `member-proposal-${Date.now()}`,
          ...input,
          status: "pending_review",
          created_by_name: getDemoUser().display_name,
          discussion_ends_at: null,
          voting_ends_at: null,
          minimum_voters: 10,
          yes_count: 0,
          no_count: 0,
          abstain_count: 0,
          proposal_type: input.proposal_type ?? "resolution",
          options: (input.options ?? []).map((label, position) => ({ id: `demo-option-${position}`, label, position, vote_count: 0 })),
          my_vote: null,
        };
        demoState.memberProposals.unshift(proposal);
        return structuredCloneSafe(proposal);
      },
    );
  },

  addMemberProposalComment(id: string, body: string) {
    return fallback(
      () =>
        request<unknown>(`/v1/member-proposals/${id}/comments`, {
          method: "POST",
          body: { body },
        }).then(normalizeMemberProposalCommentRead),
      async () => {
        const proposal = demoState.memberProposals.find((item) => item.id === id);
        if (!proposal) throw new ApiError("找不到社員提案", 404);
        proposal.comments ??= [];
        const created: MemberProposalComment = {
          id: `member-comment-${Date.now()}`,
          user_id: getDemoUser().id,
          author_name: getDemoUser().display_name,
          body,
          created_at: new Date().toISOString(),
        };
        proposal.comments.push(created);
        return structuredCloneSafe(created);
      },
    );
  },

  mealEvents() {
    return fallback(
      () =>
        request<unknown[]>("/v1/meal-events").then((events) =>
          events.map(normalizeMealEventRead),
        ),
      async () => structuredCloneSafe(demoState.mealEvents),
    );
  },

  mealEvent(id: string) {
    return fallback(
      () =>
        request<unknown>(`/v1/meal-events/${id}`).then(normalizeMealEventRead),
      async () => {
        const event = demoState.mealEvents.find((item) => item.id === id);
        if (!event) throw new ApiError("找不到便當場次", 404);
        return structuredCloneSafe(event);
      },
    );
  },

  mealOrders() {
    return fallback(
      () =>
        request<unknown[]>("/v1/meal-orders").then((orders) =>
          orders.map(normalizeMealOrderRead),
        ),
      async () => structuredCloneSafe(demoState.mealOrders),
    );
  },

  mealOrder(id: string) {
    return fallback(
      () =>
        request<unknown>(`/v1/meal-orders/${id}`).then(normalizeMealOrderRead),
      async () => {
        const order = demoState.mealOrders.find((item) => item.id === id);
        if (!order) throw new ApiError("找不到便當訂單", 404);
        return structuredCloneSafe(order);
      },
    );
  },

  cancelMealOrder(id: string) {
    return fallback(
      () =>
        request<{
          id: string;
          payment_status: string;
          fulfillment_status: string;
        }>(`/v1/meal-orders/${id}/cancel`, {
          method: "POST",
          body: { reason: "買家於允許期限內取消便當預購" },
        }),
      async () => {
        const order = demoState.mealOrders.find((item) => item.id === id);
        if (!order) throw new ApiError("找不到便當訂單", 404);
        if (!order.available_actions?.includes("cancel")) {
          throw new ApiError("已超過自行取消期限", 409);
        }
        order.payment_status =
          order.payment_status === "paid" ? "refunded" : "expired";
        order.fulfillment_status = "cancelled";
        order.pickup_code = null;
        order.pickup_qr_payload = null;
        order.available_actions = ["view"];
        const unifiedOrder = demoState.orders.find((item) => item.id === id);
        if (unifiedOrder) {
          unifiedOrder.payment_status = order.payment_status;
          unifiedOrder.fulfillment_status = "cancelled";
          unifiedOrder.available_actions = ["view"];
          if (unifiedOrder.fulfillment) {
            unifiedOrder.fulfillment.status = "cancelled";
          }
        }
        return {
          id,
          payment_status: order.payment_status,
          fulfillment_status: order.fulfillment_status,
        };
      },
    );
  },

  createMealOrder(
    eventId: string,
    input: {
      items: { offering_id: string; quantity: number }[];
      contact_email: string;
      invoice_carrier_type: InvoiceCarrierType;
      invoice_carrier_value?: string;
    },
  ) {
    return fallback(
      () =>
        request<unknown>(`/v1/meal-events/${eventId}/orders`, {
          method: "POST",
          body: input,
        }).then(normalizeMealOrderRead),
      async () => {
        const event = demoState.mealEvents.find((item) => item.id === eventId);
        if (!event) throw new ApiError("找不到便當場次", 404);
        const items = input.items.flatMap((item) => {
          const meal = event.items.find(
            (candidate) => candidate.offering_id === item.offering_id,
          );
          if (!meal || item.quantity < 1) return [];
          if (item.quantity > meal.available_quantity) {
            throw new ApiError(`${meal.meal_name} 剩餘數量不足`, 409);
          }
          meal.reserved_quantity += item.quantity;
          meal.available_quantity -= item.quantity;
          return [{
            meal_id: meal.meal_id,
            meal_name: meal.meal_name,
            quantity: item.quantity,
            unit_price: meal.price,
            subtotal: meal.price * item.quantity,
          }];
        });
        if (!items.length) throw new ApiError("請至少選擇一份便當", 400);
        const order: MealOrder = {
          id: `meal-order-${Date.now()}`,
          order_number: `MEAL-${String(Date.now()).slice(-9)}`,
          meal_event_id: event.id,
          meal_event_title: event.title,
          venue_name: event.venue_name,
          pickup_start: event.pickup_start,
          pickup_end: event.pickup_end,
          pickup_code: null,
          pickup_qr_payload: null,
          payment_status: "pending",
          fulfillment_status: "pending",
          amount_total: items.reduce((total, item) => total + item.subtotal, 0),
          created_at: new Date().toISOString(),
          available_actions: ["pay", "cancel", "view"],
          items,
        };
        demoState.mealOrders.unshift(order);
        demoState.orders.unshift({
          id: order.id,
          order_number: order.order_number,
          order_kind: "meal_preorder",
          fulfillment_status: "pending_confirmation",
          payment_status: order.payment_status,
          invoice_status: "not_eligible",
          membership_type_snapshot: getDemoUser().membership_type,
          amount_total: order.amount_total,
          paid_at: null,
          created_at: order.created_at,
          available_actions: ["pay", "cancel", "view"],
          sales_channel: "meal_preorder",
          fulfillment: {
            method: "event_pickup",
            status: "pending",
            venue_name: order.venue_name,
            pickup_start: order.pickup_start,
            pickup_end: order.pickup_end,
          },
          meal_event: {
            id: event.id,
            title: event.title,
            venue_name: event.venue_name,
            pickup_start: event.pickup_start,
            pickup_end: event.pickup_end,
          },
          pickup_code: order.pickup_code,
          items: order.items.map((item) => ({
            product_id: item.meal_id,
            product_name: item.meal_name,
            quantity: item.quantity,
            unit_price: item.unit_price,
            subtotal: item.subtotal,
            tax_type: "taxable",
          })),
        });
        return structuredCloneSafe(order);
      },
    );
  },

  adminMembershipApplications() {
    return fallback(
      () =>
        request<unknown[]>(
          "/v1/admin/membership-applications",
        ).then((applications) =>
          applications.map(normalizeMembershipApplicationRead),
        ),
      async () => structuredCloneSafe(demoState.membershipApplications),
    );
  },

  adminMembershipApplication(id: string) {
    return fallback(
      () =>
        request<unknown>(`/v1/admin/membership-applications/${id}`).then(
          normalizeMembershipApplicationRead,
        ),
      async () => {
        const application = demoState.membershipApplications.find(
          (item) => item.id === id,
        );
        if (!application) throw new ApiError("找不到入社申請", 404);
        return structuredCloneSafe(application);
      },
    );
  },

  adminMembershipDocumentDownloadUrl(
    applicationId: string,
    documentId: string,
  ) {
    return fallback<{ download_url: string; expires_in_seconds: number }>(
      () =>
        request(
          `/v1/admin/membership-applications/${applicationId}/documents/${documentId}/download-url`,
        ),
      async () => ({ download_url: "", expires_in_seconds: 120 }),
    );
  },

  adminMembers() {
    return fallback(
      () =>
        request<unknown[]>("/v1/admin/members").then((memberships) =>
          memberships
            .map(normalizeMembershipRead)
            .filter((membership): membership is Membership => Boolean(membership)),
        ),
      async () =>
        structuredCloneSafe(
          Object.entries(demoState.memberships).flatMap(
            ([user_id, membership]) =>
              membership ? [{ ...membership, user_id }] : [],
          ),
        ),
    );
  },

  adminMembershipAction(
    id: string,
    action: "suspend" | "resign" | "terminate" | "share-capital-return",
    reason: string,
  ) {
    return fallback<unknown>(
      () =>
        request<unknown>(`/v1/admin/members/${id}/${action}`, {
          method: "POST",
          body: { reason },
        }),
      async () => {
        const membershipEntry = Object.entries(demoState.memberships).find(
          ([, item]) => item?.id === id,
        );
        const userId = membershipEntry?.[0];
        const membership = membershipEntry?.[1];
        if (!membership) throw new ApiError("找不到會籍", 404);
        if (action === "share-capital-return") {
          if (membership.status !== "resigned") {
            throw new ApiError("只有已退社會籍可返還股金", 409);
          }
          const shareCapital = (
            demoState.membershipCharges[userId ?? ""] ?? []
          ).find((charge) => charge.charge_type === "share_capital");
          if (shareCapital?.payment_status !== "paid") {
            throw new ApiError("沒有可返還的已繳股金", 409);
          }
          shareCapital.payment_status = "refunded";
          return {
            refund_id: `membership-refund-${Date.now()}`,
            amount: shareCapital.amount,
            status: "completed",
          };
        }
        const allowedStatuses: Record<
          Exclude<typeof action, "share-capital-return">,
          Membership["status"][]
        > = {
          suspend: ["active"],
          resign: ["active", "suspended"],
          terminate: ["pending_payment", "active", "suspended"],
        };
        if (!allowedStatuses[action].includes(membership.status)) {
          throw new ApiError("此會籍目前不可執行該操作", 409);
        }
        membership.status =
          action === "suspend"
            ? "suspended"
            : action === "resign"
              ? "resigned"
              : "terminated";
        membership.status_reason = reason;
        if (membership.status === "suspended") {
          membership.suspended_at = new Date().toISOString();
        } else {
          membership.ended_at = new Date().toISOString();
        }
        const account = Object.values(demoState.users).find(
          ({ user }) => user.id === userId,
        );
        if (account) account.user.membership_type = "nonmember";
        return structuredCloneSafe(membership);
      },
    );
  },

  reviewMembershipApplication(
    id: string,
    action: "request_revision" | "approve" | "reject",
    note: string,
  ) {
    return fallback<unknown>(
      () => {
        const routeAction =
          action === "request_revision" ? "request-supplement" : action;
        return request<unknown>(
          `/v1/admin/membership-applications/${id}/${routeAction}`,
          { method: "POST", body: { reason: note || null } },
        );
      },
      async () => {
        const application = demoState.membershipApplications.find(
          (item) => item.id === id,
        );
        if (!application) throw new ApiError("找不到入社申請", 404);
        const canReview =
          application.status === "submitted" ||
          (action === "reject" && application.status === "needs_revision");
        if (!canReview) {
          throw new ApiError("此申請目前不可重複審核", 409);
        }
        if (
          action === "approve" &&
          new Set(application.confirmed_documents).size <
            application.required_documents.length
        ) {
          throw new ApiError("申請人證件尚未齊全", 409);
        }
        const approvedAccount =
          action === "approve"
            ? Object.values(demoState.users).find(
                ({ user }) => user.id === application.user_id,
              )
            : null;
        if (action === "approve" && !approvedAccount) {
          throw new ApiError("申請人沒有可登入的展示帳號", 409);
        }
        application.status =
          action === "approve"
            ? "approved"
            : action === "reject"
              ? "rejected"
              : "needs_revision";
        application.review_note = note || null;
        if (approvedAccount) {
          const existingMembership =
            demoState.memberships[approvedAccount.user.id];
          if (!existingMembership) {
            const pending = createPendingDemoMembership(approvedAccount.user);
            demoState.memberships[approvedAccount.user.id] = pending.membership;
            demoState.membershipCharges[approvedAccount.user.id] =
              pending.charges;
          }
          approvedAccount.user.membership_type =
            demoState.memberships[approvedAccount.user.id]?.status === "active"
              ? "member"
              : "nonmember";
        }
        return structuredCloneSafe(application);
      },
    );
  },

  adminActivities() {
    return fallback(
      () =>
        request<unknown[]>("/v1/admin/activities").then((activities) =>
          activities.map(normalizeActivityRead),
        ),
      async () => structuredCloneSafe(demoState.activities),
    );
  },

  adminActivityRegistrations(id: string) {
    return fallback<AdminActivityRegistration[]>(
      () =>
        request<unknown[]>(
          `/v1/admin/activities/${id}/registrations`,
        ).then((registrations) =>
          registrations.map(normalizeAdminActivityRegistrationRead),
        ),
      async () =>
        structuredCloneSafe(demoState.activityRegistrations[id] ?? []),
    );
  },

  adminMarkActivityAttendance(
    activityId: string,
    registrationId: string,
    attendanceStatus: "attended" | "no_show",
  ) {
    return fallback<ApiActivityRegistration>(
      () =>
        request<ApiActivityRegistration>(
          `/v1/admin/activities/${activityId}/registrations/${registrationId}/${attendanceStatus}`,
          { method: "POST" },
        ),
      async () => {
        const registration = (
          demoState.activityRegistrations[activityId] ?? []
        ).find((item) => item.id === registrationId);
        if (!registration) throw new ApiError("找不到活動報名", 404);
        if (registration.status !== "registered") {
          throw new ApiError("此報名無法標記出席", 409);
        }
        registration.status = attendanceStatus;
        registration.checked_in_at =
          attendanceStatus === "attended" ? new Date().toISOString() : null;
        return structuredCloneSafe(registration);
      },
    );
  },

  adminReviewActivity(
    id: string,
    action: "approve" | "reject" | "cancel" | "complete",
    reason = "",
  ) {
    return fallback(
      () =>
        request<unknown>(`/v1/admin/activities/${id}/${action}`, {
          method: "POST",
          body: { reason: reason || null },
        }).then(normalizeActivityRead),
      async () => {
        const activity = demoState.activities.find((item) => item.id === id);
        if (!activity) throw new ApiError("找不到活動", 404);
        activity.status =
          action === "approve"
            ? "published"
            : action === "complete"
              ? "completed"
              : action === "reject"
                ? "cancelled"
                : "cancelled";
        return structuredCloneSafe(activity);
      },
    );
  },

  adminMemberProposals() {
    return fallback(
      () =>
        request<unknown[]>("/v1/admin/member-proposals").then((proposals) =>
          proposals.map(normalizeMemberProposalRead),
        ),
      async () => structuredCloneSafe(demoState.memberProposals),
    );
  },

  adminReviewMemberProposal(
    id: string,
    action: "approve" | "reject" | "close",
    reason = "",
  ) {
    return fallback(
      () => {
        const now = Date.now();
        const body =
          action === "approve"
            ? {
                minimum_voters: 10,
                discussion_ends_at: new Date(now + 3 * 86400000).toISOString(),
                voting_ends_at: new Date(now + 10 * 86400000).toISOString(),
              }
            : {
                reason:
                  reason ||
                  (action === "reject"
                    ? "目前不符合社員提案審核條件"
                    : "管理員已記錄提案處理結果"),
              };
        return request<unknown>(
          `/v1/admin/member-proposals/${id}/${action}`,
          { method: "POST", body },
        ).then(normalizeMemberProposalRead);
      },
      async () => {
        const proposal = demoState.memberProposals.find((item) => item.id === id);
        if (!proposal) throw new ApiError("找不到社員提案", 404);
        proposal.status =
          action === "approve"
            ? "discussion"
            : action === "reject"
              ? "rejected"
              : "closed";
        return structuredCloneSafe(proposal);
      },
    );
  },

  adminMealEvents() {
    return fallback(
      () =>
        request<unknown[]>("/v1/admin/meal-events").then((events) =>
          events.map(normalizeMealEventRead),
        ),
      async () => structuredCloneSafe(demoState.mealEvents),
    );
  },

  adminMeals() {
    return fallback<Meal[]>(
      () =>
        request<unknown[]>("/v1/admin/meals").then((meals) =>
          meals.map(normalizeMealRead),
        ),
      async () => structuredCloneSafe(demoState.meals),
    );
  },

  adminCreateMeal(input: {
    name: string;
    description: string;
    price: number;
    tax_type: "taxable" | "tax_exempt";
  }) {
    return fallback<Meal>(
      () =>
        request<unknown>("/v1/admin/meals", {
          method: "POST",
          body: { ...input, is_active: true },
        }).then(normalizeMealRead),
      async () => {
        const meal: Meal = {
          id: `meal-${Date.now()}`,
          ...input,
          image_key: "meal-lunchbox",
          is_active: true,
        };
        demoState.meals.unshift(meal);
        return structuredCloneSafe(meal);
      },
    );
  },

  adminCreateMealEvent(input: {
    title: string;
    location: string;
    ordering_starts_at: string;
    ordering_ends_at: string;
    pickup_starts_at: string;
    pickup_ends_at: string;
    offerings: {
      meal_id: string;
      price: number;
      capacity: number;
      position: number;
    }[];
  }) {
    return fallback<MealEvent>(
      () =>
        request<unknown>("/v1/admin/meal-events", {
          method: "POST",
          body: input,
        }).then(normalizeMealEventRead),
      async () => {
        const items = input.offerings.map((offering) => {
          const meal = demoState.meals.find(
            (item) => item.id === offering.meal_id,
          );
          if (!meal || !meal.is_active) {
            throw new ApiError("場次包含無效餐點", 422);
          }
          return {
            offering_id: `offering-${Date.now()}-${offering.position}`,
            meal_id: meal.id,
            meal_name: meal.name,
            description: meal.description,
            price: offering.price,
            capacity: offering.capacity,
            reserved_quantity: 0,
            paid_quantity: 0,
            available_quantity: offering.capacity,
            image_key: meal.image_key,
            image_url: meal.image_url,
          };
        });
        const event: MealEvent = {
          id: `meal-event-${Date.now()}`,
          title: input.title,
          school_name: input.location,
          venue_name: input.location,
          sales_start: input.ordering_starts_at,
          order_deadline: input.ordering_ends_at,
          pickup_start: input.pickup_starts_at,
          pickup_end: input.pickup_ends_at,
          status: "draft",
          items,
        };
        demoState.mealEvents.unshift(event);
        return structuredCloneSafe(event);
      },
    );
  },

  adminMealEventAction(
    id: string,
    action: "publish" | "cancel" | "open_pickup" | "complete",
    reason = "",
  ) {
    return fallback(
      () => {
        const routeAction = action === "open_pickup" ? "open-pickup" : action;
        return request<unknown>(`/v1/admin/meal-events/${id}/${routeAction}`, {
          method: "POST",
          ...(action === "publish"
            ? {}
            : {
                body: {
                  reason:
                    reason ||
                    (action === "cancel"
                      ? "管理員取消便當場次"
                      : "管理員更新便當場次"),
                },
              }),
        }).then(normalizeMealEventRead);
      },
      async () => {
        const event = demoState.mealEvents.find((item) => item.id === id);
        if (!event) throw new ApiError("找不到便當場次", 404);
        const eventOrders = demoState.mealOrders.filter(
          (order) => order.meal_event_id === id,
        );
        if (
          action === "cancel" &&
          eventOrders.some((order) => order.fulfillment_status === "picked_up")
        ) {
          throw new ApiError("已有訂單完成取餐，無法取消整個場次", 409);
        }
        event.status =
          action === "publish"
            ? "published"
            : action === "open_pickup"
              ? "pickup_open"
              : action === "complete"
              ? "completed"
                : "cancelled";
        if (action === "open_pickup") {
          eventOrders
            .filter(
              (order) =>
                order.payment_status === "paid" &&
                order.fulfillment_status === "pending",
            )
            .forEach((order) => {
              order.fulfillment_status = "ready";
              const unifiedOrder = demoState.orders.find(
                (item) => item.id === order.id,
              );
              if (unifiedOrder) {
                unifiedOrder.fulfillment_status = "ready_for_pickup";
                if (unifiedOrder.fulfillment) {
                  unifiedOrder.fulfillment.status = "ready_for_pickup";
                }
              }
            });
        }
        if (action === "cancel") {
          eventOrders
            .filter(
              (order) =>
                !["picked_up", "cancelled"].includes(
                  order.fulfillment_status,
                ),
            )
            .forEach((order) => {
              order.payment_status =
                order.payment_status === "paid" ? "refunded" : "expired";
              order.fulfillment_status = "cancelled";
              order.pickup_code = null;
              order.pickup_qr_payload = null;
              order.available_actions = ["view"];
              const unifiedOrder = demoState.orders.find(
                (item) => item.id === order.id,
              );
              if (unifiedOrder) {
                unifiedOrder.payment_status = order.payment_status;
                unifiedOrder.fulfillment_status = "cancelled";
                unifiedOrder.available_actions = ["view"];
                if (unifiedOrder.fulfillment) {
                  unifiedOrder.fulfillment.status = "cancelled";
                }
              }
            });
        }
        if (action === "complete") {
          eventOrders
            .filter(
              (order) =>
                order.payment_status === "paid" &&
                ["pending", "ready"].includes(order.fulfillment_status),
            )
            .forEach((order) => {
              order.fulfillment_status = "no_show";
              order.pickup_code = null;
              order.pickup_qr_payload = null;
              order.available_actions = ["view"];
              const unifiedOrder = demoState.orders.find(
                (item) => item.id === order.id,
              );
              if (unifiedOrder) {
                unifiedOrder.fulfillment_status = "picked_up";
                unifiedOrder.invoice_status = "pending";
                unifiedOrder.available_actions = ["view"];
                if (unifiedOrder.fulfillment) {
                  unifiedOrder.fulfillment.status = "no_show";
                }
              }
            });
        }
        return structuredCloneSafe(event);
      },
    );
  },

  adminDuplicateMealEvent(id: string) {
    return fallback(
      async () => {
        const events = await request<unknown[]>("/v1/admin/meal-events");
        const sourceRaw = events.find(
          (item) => String((item as { id?: unknown }).id) === id,
        );
        if (!sourceRaw) throw new ApiError("找不到便當場次", 404);
        const source = normalizeMealEventRead(sourceRaw);
        const now = Date.now();
        const orderingDuration = Math.max(
          Date.parse(source.order_deadline) - Date.parse(source.sales_start),
          86400000,
        );
        const pickupDuration = Math.max(
          Date.parse(source.pickup_end) - Date.parse(source.pickup_start),
          3600000,
        );
        const orderingStartsAt = now + 3600000;
        const orderingEndsAt = orderingStartsAt + orderingDuration;
        const pickupStartsAt = orderingEndsAt + 86400000;
        return request<unknown>(`/v1/admin/meal-events/${id}/duplicate`, {
          method: "POST",
          body: {
            title: `${source.title}（複製）`,
            location: source.venue_name,
            ordering_starts_at: new Date(orderingStartsAt).toISOString(),
            ordering_ends_at: new Date(orderingEndsAt).toISOString(),
            pickup_starts_at: new Date(pickupStartsAt).toISOString(),
            pickup_ends_at: new Date(
              pickupStartsAt + pickupDuration,
            ).toISOString(),
            offerings: source.items.map((item, position) => ({
              meal_id: item.meal_id,
              price: item.price,
              capacity: item.capacity,
              position,
            })),
          },
        }).then(normalizeMealEventRead);
      },
      async () => {
        const source = demoState.mealEvents.find((item) => item.id === id);
        if (!source) throw new ApiError("找不到便當場次", 404);
        const copy: MealEvent = {
          ...structuredCloneSafe(source),
          id: `meal-event-${Date.now()}`,
          title: `${source.title}（複製）`,
          status: "draft",
          items: source.items.map((item) => ({
            ...item,
            reserved_quantity: 0,
            paid_quantity: 0,
            available_quantity: item.capacity,
          })),
        };
        demoState.mealEvents.unshift(copy);
        return structuredCloneSafe(copy);
      },
    );
  },

  adminRedeemMealOrder(eventId: string, pickupCode: string) {
    return fallback<MealPickupRedemption>(
      () =>
        request<MealPickupRedemption>(
          `/v1/admin/meal-events/${eventId}/redeem`,
          {
            method: "POST",
            body: { pickup_code: pickupCode },
          },
        ),
      async () => {
        const event = demoState.mealEvents.find((item) => item.id === eventId);
        if (!event || event.status !== "pickup_open") {
          throw new ApiError("此場次尚未開放取餐", 409);
        }
        const order = demoState.mealOrders.find(
          (item) =>
            item.meal_event_id === eventId && item.pickup_code === pickupCode,
        );
        if (!order || order.payment_status !== "paid") {
          throw new ApiError("找不到有效取餐碼", 404);
        }
        if (order.fulfillment_status === "picked_up") {
          throw new ApiError("此取餐碼已核銷", 409);
        }
        order.fulfillment_status = "picked_up";
        const unifiedOrder = demoState.orders.find((item) => item.id === order.id);
        if (unifiedOrder) {
          unifiedOrder.fulfillment_status = "picked_up";
          if (unifiedOrder.fulfillment) {
            unifiedOrder.fulfillment.status = "picked_up";
          }
        }
        return {
          order_id: order.id,
          order_number: order.order_number,
          pickup_code: pickupCode,
          status: "picked_up",
          redeemed_at: new Date().toISOString(),
        };
      },
    );
  },

  adminAdvanceShipment(orderId: string, status: Shipment["status"]) {
    return fallback<Shipment>(
      () =>
        request<unknown>(
          `/v1/admin/orders/${orderId}/logistics/sandbox-status`,
          {
            method: "POST",
            body: { status, reason: "管理員於後台推進 Sandbox 貨態" },
          },
        ).then((shipment) => {
          const normalized = normalizeShipmentRead(shipment);
          if (!normalized) throw new ApiError("物流貨態回應不完整", 502);
          return normalized;
        }),
      async () => {
        const order = demoState.orders.find((item) => item.id === orderId);
        if (!order?.shipment) throw new ApiError("找不到物流訂單", 404);
        order.shipment.status = status;
        order.available_actions =
          status === "delivered"
            ? ["view"]
            : ["refund", "advance_shipment", "view"];
        if (status === "delivered") {
          order.fulfillment_status = "picked_up";
          if (order.fulfillment) order.fulfillment.status = "delivered";
        }
        return structuredCloneSafe(order.shipment);
      },
    );
  },

  adminCreateShipment(orderId: string) {
    return fallback<Shipment>(
      () =>
        request<{ shipment: unknown }>(
          `/v1/admin/orders/${orderId}/logistics/create`,
          { method: "POST" },
        ).then((result) => {
          const shipment = normalizeShipmentRead(result.shipment);
          if (!shipment) throw new ApiError("物流建單結果不完整", 502);
          return shipment;
        }),
      async () => {
        const order = demoState.orders.find((item) => item.id === orderId);
        if (!order?.shipment) throw new ApiError("找不到物流訂單", 404);
        if (order.payment_status !== "paid") {
          throw new ApiError("訂單付款後才能建立物流單", 409);
        }
        order.shipment.status = "created";
        order.shipment.ecpay_logistics_id = `DEMO-${String(Date.now()).slice(-8)}`;
        order.shipment.tracking_number = `SLF${String(Date.now()).slice(-9)}`;
        if (order.fulfillment) order.fulfillment.status = "awaiting_shipment";
        order.fulfillment_status = "preparing";
        order.available_actions = ["refund", "advance_shipment", "view"];
        return structuredCloneSafe(order.shipment);
      },
    );
  },

  cooperativeEducation() {
    return request<{
      lectures: { id: string; title: string; body: string; position: number }[];
      passed: boolean;
      required_for_membership: boolean;
    }>("/v1/education");
  },

  startEducationAttempt() {
    return request<{
      attempt_id: string;
      questions: { id: string; prompt: string; options: string[] }[];
    }>("/v1/education/attempts", { method: "POST" });
  },

  submitEducationAttempt(attemptId: string, answers: Record<string, number>) {
    return request<{ score: number; passed: boolean; correct: number; total: number }>(
      `/v1/education/attempts/${attemptId}/submit`,
      { method: "POST", body: { answers } },
    );
  },

  myPoints() {
    return request<{
      balance: number;
      transactions: { id: string; amount: number; source_type: string; note: string; created_at: string }[];
    }>("/v1/me/points");
  },

  myBadges() {
    return request<{ key: string; name: string; description: string; granted_at: string }[]>("/v1/me/badges");
  },

  wishes() {
    return request<{ id: string; name: string; description: string; expected_price?: number; status: string; support_count: number; supported_by_me: boolean }[]>("/v1/wishes");
  },

  createWish(body: { name: string; description: string; expected_price?: number }) {
    return request("/v1/wishes", { method: "POST", body });
  },

  supportWish(id: string) {
    return request<{ support_count: number }>(`/v1/wishes/${id}/support`, { method: "POST" });
  },

  meetings() {
    return request<{ id: string; title: string; meeting_type: string; starts_at: string; location: string; attended_count: number; eligible_member_count: number; attendance_rate: number }[]>("/v1/meetings");
  },

  mySurplusDistributions() {
    return request<{ fiscal_year_id: string; label: string; contribution_amount: number; distribution_amount: number; confirmed_at: string }[]>("/v1/me/surplus-distributions");
  },

  adminNonmemberSales(startsOn: string, endsOn: string) {
    return request<{ total_revenue: number; nonmember_revenue: number; ratio: number; headroom_amount: number; level: string; transactions_blocked: boolean }>(`/v1/admin/finance/nonmember-sales?starts_on=${startsOn}&ends_on=${endsOn}`);
  },

  adminTaxLedger(startsOn: string, endsOn: string) {
    return request<{ rows: { tax_type: string; membership_type: string; sales_channel: string; sales_amount: number; tax_amount: number; order_count: number }[] }>(`/v1/admin/finance/tax-ledger?starts_on=${startsOn}&ends_on=${endsOn}`);
  },

  adminSurplusDryRun(body: { label: string; starts_on: string; ends_on: string; total_cost: number; reserve_percentage: number }) {
    return request<{ total_revenue: number; total_surplus: number; reserve_amount: number; distributable_surplus: number; distributions: { member_id: string; member_name: string; contribution_amount: number; distribution_amount: number }[] }>("/v1/admin/surplus/dry-run", { method: "POST", body });
  },

  adminConfirmSurplus(body: { label: string; starts_on: string; ends_on: string; total_cost: number; reserve_percentage: number }) {
    return request("/v1/admin/surplus/confirm", { method: "POST", body });
  },

  resetDemo(confirmation: string) {
    return fallback(
      () =>
        request<{ message: string }>("/v1/admin/demo/reset", {
          method: "POST",
          body: { confirmation },
        }),
      async () => {
        if (confirmation !== "RESET") {
          throw new ApiError("重設確認碼不正確", 403);
        }
        demoState.users = structuredCloneSafe(demoUsers);
        demoState.products = demoProductsWithLogistics();
        demoState.proposals = structuredCloneSafe(demoProposals);
        demoState.campaigns = structuredCloneSafe(demoCampaigns);
        demoState.orders = structuredCloneSafe(demoOrders);
        demoState.notifications = createDemoNotificationState();
        demoState.mealEvents = structuredCloneSafe(demoMealEvents);
        demoState.meals = structuredCloneSafe(demoMeals);
        demoState.mealOrders = structuredCloneSafe(demoMealOrders);
        demoState.membershipApplications = structuredCloneSafe(
          demoMembershipApplications,
        );
        demoState.memberships = structuredCloneSafe(demoMemberships);
        demoState.membershipCharges = createDemoMembershipChargeState();
        demoState.memberDirectory = structuredCloneSafe(demoMemberDirectory);
        demoState.activities = structuredCloneSafe(demoActivities);
        demoState.activityRegistrations = createDemoActivityRegistrations();
        demoState.memberProposals = structuredCloneSafe(demoMemberProposals);
        return { message: "展示資料已恢復為初始狀態" };
      },
    );
  },
};

export function getErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : "操作未完成，請稍後再試";
}
