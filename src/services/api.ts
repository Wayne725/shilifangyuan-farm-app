import {
  demoBundles,
  demoCampaigns,
  demoActivities,
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
  AppNotification,
  AuthSession,
  CartItem,
  FulfillmentMethod,
  FulfillmentStatus,
  GroupBundle,
  GroupCampaign,
  InvoiceCarrierType,
  LogisticsProvider,
  MealEvent,
  MealOrder,
  MemberActivity,
  MemberDirectoryEntry,
  Membership,
  MembershipApplication,
  MembershipCharge,
  MembershipDocumentRead,
  MembershipDocumentUpload,
  MemberProposal,
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
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
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

async function fallback<T>(network: () => Promise<T>, demo: () => Promise<T>) {
  return apiBaseUrl ? network() : demo();
}

const demoState = {
  products: demoProductsWithLogistics(),
  proposals: structuredCloneSafe(demoProposals),
  campaigns: structuredCloneSafe(demoCampaigns),
  orders: structuredCloneSafe(demoOrders),
  notifications: structuredCloneSafe(demoNotifications),
  mealEvents: structuredCloneSafe(demoMealEvents),
  mealOrders: structuredCloneSafe(demoMealOrders),
  membershipApplications: structuredCloneSafe(demoMembershipApplications),
  memberships: structuredCloneSafe(demoMemberships),
  membershipCharges: structuredCloneSafe(demoMembershipCharges),
  activities: structuredCloneSafe(demoActivities),
  memberProposals: structuredCloneSafe(demoMemberProposals),
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
    Object.values(demoUsers).find(({ user }) => user.id === userId)?.user ??
    demoUsers["member@shilifangyuan.tw"]!.user
  );
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
  const nestedStatus = order.fulfillment?.status;
  const legacyStatus: FulfillmentStatus =
    order.fulfillment_status ??
    (nestedStatus === "ready"
      ? "ready_for_pickup"
      : nestedStatus === "delivered"
        ? "picked_up"
        : nestedStatus === "shipped"
          ? "preparing"
          : nestedStatus === "no_show"
            ? "cancelled"
            : nestedStatus === "pending"
              ? "pending_confirmation"
              : (nestedStatus as FulfillmentStatus | undefined)) ??
    "pending_confirmation";
  return {
    ...order,
    order_kind: order.order_kind ?? order.sales_channel ?? "regular",
    fulfillment_status: legacyStatus,
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
        const account = demoUsers[email.toLowerCase()];
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
        request<Product[]>("/v1/products").then((products) =>
          products.map((product) => ({
            ...product,
            stock: product.stock_quantity ?? product.stock,
          })),
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
      () => request<GroupCampaign[]>("/v1/group-campaigns"),
      async () => structuredCloneSafe(demoState.campaigns),
    );
  },

  async campaign(id: string) {
    return fallback(
      () => request<GroupCampaign>(`/v1/group-campaigns/${id}`),
      async () => {
        const campaign = demoState.campaigns.find((item) => item.id === id);
        if (!campaign) throw new ApiError("找不到這個共同購買", 404);
        return structuredCloneSafe(campaign);
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
        request<Order>(`/v1/group-campaigns/${id}/join`, {
          method: "POST",
          body: input,
        }),
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
        request<Order>("/v1/orders", {
          method: "POST",
          body: {
            items: input.items,
            contact_email: input.contact_email,
            invoice_carrier_type: input.invoice_carrier_type,
            ...(input.invoice_carrier_value
              ? { invoice_carrier_value: input.invoice_carrier_value }
              : {}),
          },
        }),
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
        request<LogisticsSelection>(
          `/v1/orders/${orderId}/logistics/selection`,
          { method: "POST", body: input },
        ),
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
        request<LogisticsSelection>(
          `/v1/orders/${orderId}/logistics/selection-link`,
          { method: "POST" },
        ),
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
        order.available_actions = ["cancel", "view"];
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
      async () => structuredCloneSafe(demoState.notifications),
    );
  },

  markNotificationRead(id: string) {
    return fallback(
      () =>
        request<ApiNotification>(`/v1/notifications/${id}/read`, {
          method: "PATCH",
        }).then(normalizeNotification),
      async () => {
        const notice = demoState.notifications.find((item) => item.id === id);
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
    },
  ) {
    return fallback(
      () =>
        request<GroupCampaign>(
          `/v1/vote-proposals/${id}/admin/convert`,
          {
            method: "POST",
            body: input,
          },
        ),
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
          created_at: new Date().toISOString(),
        };
        proposal.status = "converted";
        demoState.campaigns.unshift(campaign);
        return structuredCloneSafe(campaign);
      },
    );
  },

  confirmCampaign(id: string, final_pickup_at: string) {
    return fallback(
      () =>
        request<GroupCampaign>(`/v1/group-campaigns/${id}/admin/confirm`, {
          method: "POST",
          body: { final_pickup_at },
        }),
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
        request<GroupCampaign>(`/v1/group-campaigns/${id}/admin/cancel`, {
          method: "POST",
          body: { reason },
        }),
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
        request<GroupCampaign>(
          `/v1/group-campaigns/${id}/admin/reject`,
          {
            method: "POST",
            body: { reason },
          },
        ),
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
        request<Product>(`/v1/products/${id}`, {
          method: "PATCH",
          body: { is_active },
        }),
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
        request<Product>("/v1/products", {
          method: "POST",
          body: {
            ...input,
            image_url: null,
            is_active: true,
          },
        }).then((product) => ({
          ...product,
          stock: product.stock_quantity ?? product.stock,
        })),
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
      () => request<MembershipApplication | null>("/v1/membership/application"),
      async () => {
        const user = getDemoUser();
        const application = demoState.membershipApplications.find(
          (item) => item.legal_name === user.display_name,
        );
        return application ? structuredCloneSafe(application) : null;
      },
    );
  },

  saveMembershipApplication(
    input: Omit<
      MembershipApplication,
      | "id"
      | "status"
      | "submitted_at"
      | "required_documents"
      | "confirmed_documents"
      | "review_note"
    >,
  ) {
    return fallback(
      () =>
        request<MembershipApplication>("/v1/membership/application", {
          method: "PUT",
          body: input,
        }),
      async () => {
        const user = getDemoUser();
        let application = demoState.membershipApplications.find(
          (item) => item.legal_name === user.display_name,
        );
        if (!application) {
          application = {
            id: `application-${Date.now()}`,
            status: "draft",
            ...input,
            submitted_at: null,
            review_note: null,
            required_documents: ["id_front", "id_back", "secondary"],
            confirmed_documents: [],
          };
          demoState.membershipApplications.unshift(application);
        } else {
          Object.assign(application, input);
        }
        return structuredCloneSafe(application);
      },
    );
  },

  submitMembershipApplication() {
    return fallback(
      () =>
        request<MembershipApplication>("/v1/membership/application/submit", {
          method: "POST",
        }),
      async () => {
        const application = demoState.membershipApplications.find(
          (item) => item.legal_name === getDemoUser().display_name,
        );
        if (!application) throw new ApiError("請先填寫入社資料", 400);
        if (
          application.confirmed_documents.length <
          application.required_documents.length
        ) {
          throw new ApiError("請先補齊三份測試證件", 400);
        }
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
        request<MembershipApplication>("/v1/membership/application/withdraw", {
          method: "POST",
        }),
      async () => {
        const application = demoState.membershipApplications.find(
          (item) => item.legal_name === getDemoUser().display_name,
        );
        if (!application) throw new ApiError("找不到入社申請", 404);
        application.status = "withdrawn";
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
        const application = demoState.membershipApplications.find(
          (item) => item.legal_name === getDemoUser().display_name,
        );
        if (!application) throw new ApiError("請先建立入社申請", 400);
        if (!application.confirmed_documents.includes(input.document_type)) {
          application.confirmed_documents.push(input.document_type);
        }
        return {
          id: input.document_id,
          document_type: input.document_type,
          status: "confirmed" as const,
          checksum_sha256: input.checksum_sha256,
        };
      },
    );
  },

  membership() {
    return fallback(
      () => request<Membership | null>("/v1/members/me"),
      async () =>
        structuredCloneSafe(demoState.memberships[getDemoUser().id] ?? null),
    );
  },

  membershipCharges() {
    return fallback(
      () => request<MembershipCharge[]>("/v1/membership/charges"),
      async () => structuredCloneSafe(demoState.membershipCharges),
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
        const charge = demoState.membershipCharges.find((item) => item.id === id);
        if (!charge) throw new ApiError("找不到應繳款", 404);
        charge.payment_status = "paid";
        charge.paid_at = new Date().toISOString();
        charge.receipt_number = `RCPT-${String(Date.now()).slice(-9)}`;
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
      () => request<MemberDirectoryEntry[]>("/v1/members/directory"),
      async () => structuredCloneSafe(demoMemberDirectory),
    );
  },

  activities() {
    return fallback(
      () => request<MemberActivity[]>("/v1/activities"),
      async () => structuredCloneSafe(demoState.activities),
    );
  },

  createActivity(input: {
    title: string;
    description: string;
    venue_name: string;
    starts_at: string;
    registration_deadline: string;
    capacity: number;
  }) {
    return fallback(
      () =>
        request<MemberActivity>("/v1/activities", {
          method: "POST",
          body: input,
        }),
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
      () => request<MemberProposal[]>("/v1/member-proposals"),
      async () => structuredCloneSafe(demoState.memberProposals),
    );
  },

  voteMemberProposal(id: string, choice: MemberVoteChoice) {
    return fallback(
      () =>
        request<MemberProposal>(`/v1/member-proposals/${id}/vote`, {
          method: "PUT",
          body: { choice },
        }),
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

  createMemberProposal(input: { title: string; summary: string }) {
    return fallback(
      () =>
        request<MemberProposal>("/v1/member-proposals", {
          method: "POST",
          body: input,
        }),
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
        request<MemberProposal>(`/v1/member-proposals/${id}/comments`, {
          method: "POST",
          body: { body },
        }),
      async () => {
        const proposal = demoState.memberProposals.find((item) => item.id === id);
        if (!proposal) throw new ApiError("找不到社員提案", 404);
        proposal.comments ??= [];
        proposal.comments.push({
          id: `member-comment-${Date.now()}`,
          author_name: getDemoUser().display_name,
          body,
          created_at: new Date().toISOString(),
        });
        return structuredCloneSafe(proposal);
      },
    );
  },

  mealEvents() {
    return fallback(
      () => request<MealEvent[]>("/v1/meal-events"),
      async () => structuredCloneSafe(demoState.mealEvents),
    );
  },

  mealEvent(id: string) {
    return fallback(
      () => request<MealEvent>(`/v1/meal-events/${id}`),
      async () => {
        const event = demoState.mealEvents.find((item) => item.id === id);
        if (!event) throw new ApiError("找不到便當場次", 404);
        return structuredCloneSafe(event);
      },
    );
  },

  mealOrders() {
    return fallback(
      () => request<MealOrder[]>("/v1/meal-orders"),
      async () => structuredCloneSafe(demoState.mealOrders),
    );
  },

  mealOrder(id: string) {
    return fallback(
      () => request<MealOrder>(`/v1/meal-orders/${id}`),
      async () => {
        const order = demoState.mealOrders.find((item) => item.id === id);
        if (!order) throw new ApiError("找不到便當訂單", 404);
        return structuredCloneSafe(order);
      },
    );
  },

  createMealOrder(
    eventId: string,
    input: { items: { meal_id: string; quantity: number }[] },
  ) {
    return fallback(
      () =>
        request<MealOrder>(`/v1/meal-events/${eventId}/orders`, {
          method: "POST",
          body: input,
        }),
      async () => {
        const event = demoState.mealEvents.find((item) => item.id === eventId);
        if (!event) throw new ApiError("找不到便當場次", 404);
        const items = input.items.flatMap((item) => {
          const meal = event.items.find(
            (candidate) => candidate.meal_id === item.meal_id,
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
          pickup_code: String(Math.floor(100000 + Math.random() * 900000)),
          payment_status: "paid",
          fulfillment_status: "ready",
          amount_total: items.reduce((total, item) => total + item.subtotal, 0),
          created_at: new Date().toISOString(),
          items,
        };
        demoState.mealOrders.unshift(order);
        demoState.orders.unshift({
          id: order.id,
          order_number: order.order_number,
          order_kind: "meal_preorder",
          fulfillment_status: "ready_for_pickup",
          payment_status: order.payment_status,
          invoice_status: "not_eligible",
          membership_type_snapshot: getDemoUser().membership_type,
          amount_total: order.amount_total,
          paid_at: new Date().toISOString(),
          created_at: order.created_at,
          available_actions: ["view"],
          sales_channel: "meal_preorder",
          fulfillment: {
            method: "event_pickup",
            status: "ready",
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
        request<MembershipApplication[]>(
          "/v1/admin/membership-applications",
        ),
      async () => structuredCloneSafe(demoState.membershipApplications),
    );
  },

  reviewMembershipApplication(
    id: string,
    action: "request_revision" | "approve" | "reject",
    note: string,
  ) {
    return fallback(
      () =>
        request<MembershipApplication>(
          `/v1/admin/membership-applications/${id}/${action}`,
          { method: "POST", body: { note } },
        ),
      async () => {
        const application = demoState.membershipApplications.find(
          (item) => item.id === id,
        );
        if (!application) throw new ApiError("找不到入社申請", 404);
        application.status =
          action === "approve"
            ? "approved"
            : action === "reject"
              ? "rejected"
              : "needs_revision";
        application.review_note = note || null;
        return structuredCloneSafe(application);
      },
    );
  },

  adminReviewActivity(
    id: string,
    action: "approve" | "cancel" | "complete",
  ) {
    return fallback(
      () =>
        request<MemberActivity>(`/v1/admin/activities/${id}/${action}`, {
          method: "POST",
        }),
      async () => {
        const activity = demoState.activities.find((item) => item.id === id);
        if (!activity) throw new ApiError("找不到活動", 404);
        activity.status =
          action === "approve"
            ? "published"
            : action === "complete"
              ? "completed"
              : "cancelled";
        return structuredCloneSafe(activity);
      },
    );
  },

  adminReviewMemberProposal(
    id: string,
    action: "approve" | "reject" | "close",
  ) {
    return fallback(
      () =>
        request<MemberProposal>(
          `/v1/admin/member-proposals/${id}/${action}`,
          { method: "POST" },
        ),
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

  adminMealEventAction(
    id: string,
    action: "publish" | "cancel" | "open_pickup" | "complete",
  ) {
    return fallback(
      () =>
        request<MealEvent>(`/v1/admin/meal-events/${id}/${action}`, {
          method: "POST",
        }),
      async () => {
        const event = demoState.mealEvents.find((item) => item.id === id);
        if (!event) throw new ApiError("找不到便當場次", 404);
        event.status =
          action === "publish"
            ? "published"
            : action === "open_pickup"
              ? "pickup_open"
              : action === "complete"
                ? "completed"
                : "cancelled";
        return structuredCloneSafe(event);
      },
    );
  },

  adminDuplicateMealEvent(id: string) {
    return fallback(
      () =>
        request<MealEvent>(`/v1/admin/meal-events/${id}/duplicate`, {
          method: "POST",
        }),
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

  adminAdvanceShipment(orderId: string, status: Shipment["status"]) {
    return fallback<Shipment>(
      () =>
        request<Shipment>(
          `/v1/admin/orders/${orderId}/logistics/sandbox-status`,
          {
            method: "POST",
            body: { status, reason: "管理員於後台推進 Sandbox 貨態" },
          },
        ),
      async () => {
        const order = demoState.orders.find((item) => item.id === orderId);
        if (!order?.shipment) throw new ApiError("找不到物流訂單", 404);
        order.shipment.status = status;
        if (status === "delivered") {
          order.fulfillment_status = "picked_up";
          if (order.fulfillment) order.fulfillment.status = "delivered";
        }
        return structuredCloneSafe(order.shipment);
      },
    );
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
        demoState.products = demoProductsWithLogistics();
        demoState.proposals = structuredCloneSafe(demoProposals);
        demoState.campaigns = structuredCloneSafe(demoCampaigns);
        demoState.orders = structuredCloneSafe(demoOrders);
        demoState.notifications = structuredCloneSafe(demoNotifications);
        demoState.mealEvents = structuredCloneSafe(demoMealEvents);
        demoState.mealOrders = structuredCloneSafe(demoMealOrders);
        demoState.membershipApplications = structuredCloneSafe(
          demoMembershipApplications,
        );
        demoState.memberships = structuredCloneSafe(demoMemberships);
        demoState.membershipCharges = structuredCloneSafe(
          demoMembershipCharges,
        );
        demoState.activities = structuredCloneSafe(demoActivities);
        demoState.memberProposals = structuredCloneSafe(demoMemberProposals);
        return { message: "展示資料已恢復為初始狀態" };
      },
    );
  },
};

export function getErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : "操作未完成，請稍後再試";
}
