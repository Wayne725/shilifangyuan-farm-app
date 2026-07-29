import {
  demoBundles,
  demoCampaigns,
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
  GroupBundle,
  GroupCampaign,
  InvoiceCarrierType,
  Order,
  PaymentAttempt,
  Product,
  User,
  VoteProposal,
} from "../types";

declare const process: {
  env: Record<string, string | undefined>;
};

const apiBaseUrl = process.env.EXPO_PUBLIC_API_URL?.replace(/\/$/, "") ?? "";
let accessToken: string | null = null;

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

async function request<T>(path: string, options: RequestOptions = {}) {
  if (!apiBaseUrl) {
    throw new ApiError("API 尚未設定", 0);
  }

  const response = await fetch(`${apiBaseUrl}${path}`, {
    method: options.method ?? "GET",
    headers: {
      Accept: "application/json",
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.token ?? accessToken
        ? { Authorization: `Bearer ${options.token ?? accessToken}` }
        : {}),
    },
    ...(options.body ? { body: JSON.stringify(options.body) } : {}),
  }).catch(() => {
    throw new ApiError("無法連線到服務", 0);
  });

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
  products: structuredCloneSafe(demoProducts),
  proposals: structuredCloneSafe(demoProposals),
  campaigns: structuredCloneSafe(demoCampaigns),
  orders: structuredCloneSafe(demoOrders),
  notifications: structuredCloneSafe(demoNotifications),
};

function structuredCloneSafe<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
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

export function setApiAccessToken(token: string | null) {
  accessToken = token;
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
        const order: Order = {
          id: `order-${Date.now()}`,
          order_number: orderNumber("GB"),
          order_kind: "group",
          group_campaign_id: campaign.id,
          fulfillment_status: "pending_confirmation",
          payment_status: "pending",
          invoice_status: "not_eligible",
          membership_type_snapshot: user.membership_type,
          amount_total: unitPrice * input.quantity,
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
      () => request<Order[]>("/v1/orders"),
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
      () => request<Order>(`/v1/orders/${id}`),
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

  createOrder(input: {
    items: CartItem[];
    contact_email: string;
    invoice_carrier_type: InvoiceCarrierType;
    invoice_carrier_value?: string;
  }) {
    return fallback(
      () =>
        request<Order>("/v1/orders", {
          method: "POST",
          body: input,
        }),
      async () => {
        const quote = await api.quote(input.items);
        const user = getDemoUser();
        const order: Order = {
          id: `order-${Date.now()}`,
          order_number: orderNumber("SLF"),
          order_kind: "regular",
          group_campaign_id: null,
          fulfillment_status: "pending_confirmation",
          payment_status: "pending",
          invoice_status: "not_eligible",
          membership_type_snapshot: user.membership_type,
          amount_total: quote.amount_total,
          created_at: new Date().toISOString(),
          available_actions: ["pay", "cancel", "view"],
          items: quote.items,
        };
        demoState.orders.unshift(order);
        return structuredCloneSafe(order);
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
        demoState.products = structuredCloneSafe(demoProducts);
        demoState.proposals = structuredCloneSafe(demoProposals);
        demoState.campaigns = structuredCloneSafe(demoCampaigns);
        demoState.orders = structuredCloneSafe(demoOrders);
        demoState.notifications = structuredCloneSafe(demoNotifications);
        return { message: "展示資料已恢復為初始狀態" };
      },
    );
  },
};

export function getErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : "操作未完成，請稍後再試";
}
