import { apiFetch } from "./api";
import type {
  CartItem,
  LogisticsSelection,
  Order,
  PaymentAttempt,
  PaymentAttemptStatus,
  ShippingChannel,
  ShippingRate,
  ShippingTemperature,
} from "./types";

export const shippingChannelLabels: Record<ShippingChannel, string> = {
  home_delivery: "宅配到府",
  seven_eleven: "7-ELEVEN 取貨",
  family_mart: "全家取貨",
  hilife: "萊爾富取貨",
};

export const temperatureLabels: Record<ShippingTemperature, string> = {
  ambient: "常溫",
  chilled: "冷藏",
  frozen: "冷凍",
};

export interface ShippingEligibility {
  availableChannels: ShippingChannel[];
  temperature: ShippingTemperature | null;
  blockers: string[];
}

export function shippingEligibility(cart: CartItem[]): ShippingEligibility {
  if (!cart.length) {
    return { availableChannels: [], temperature: null, blockers: [] };
  }
  const blockers = cart
    .filter(({ product }) => !product.can_ship)
    .map(({ product }) => `${product.name}未開放配送`);
  const temperatures = new Set(
    cart.map(({ product }) => product.shipping_temperature || "ambient"),
  );
  if (temperatures.size > 1) blockers.push("不同溫層商品不能同筆配送");

  const availableChannels = blockers.length
    ? []
    : cart
        .map(({ product }) => product.allowed_shipping_channels)
        .reduce<ShippingChannel[]>((available, channels, index) =>
          index === 0
            ? [...channels]
            : available.filter((channel) => channels.includes(channel)),
        [],
      );
  if (!blockers.length && !availableChannels.length) {
    blockers.push("購物車商品沒有共同的物流通路");
  }
  return {
    availableChannels,
    temperature:
      temperatures.size === 1
        ? (Array.from(temperatures)[0] as ShippingTemperature)
        : null,
    blockers,
  };
}

export function shippingFee(
  rates: ShippingRate[],
  channel: ShippingChannel,
  temperature: ShippingTemperature,
  subtotal: number,
): number | null {
  const rate = rates.find(
    (candidate) =>
      candidate.channel === channel &&
      candidate.temperature === temperature &&
      candidate.is_active,
  );
  if (!rate) return null;
  return subtotal >= rate.free_shipping_threshold ? 0 : rate.fee;
}

export interface CommerceGateway {
  createOrder(body: Record<string, unknown>): Promise<Order>;
  createLogisticsSelection(
    orderId: string,
    body: Record<string, unknown>,
  ): Promise<LogisticsSelection>;
  createPaymentAttempt(orderId: string): Promise<PaymentAttempt>;
}

const httpCommerceGateway: CommerceGateway = {
  createOrder: (body) =>
    apiFetch<Order>("/v1/orders", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  createLogisticsSelection: (orderId, body) =>
    apiFetch<LogisticsSelection>(
      `/v1/orders/${orderId}/logistics/selection`,
      { method: "POST", body: JSON.stringify(body) },
    ),
  createPaymentAttempt: (orderId) =>
    apiFetch<PaymentAttempt>(`/v1/orders/${orderId}/payment-attempts`, {
      method: "POST",
    }),
};

export type InvoicePreference = {
  carrierType: "cloud" | "mobile_barcode";
  carrierValue?: string;
  buyerEmail: string;
} & (
  | {
      buyerType: "personal";
    }
  | {
      buyerType: "company";
      buyerTaxId: string;
      buyerName: string;
    }
);

export function invoicePreferencePayload(
  preference: InvoicePreference,
): Record<string, string> {
  return {
    invoice_buyer_type: preference.buyerType,
    invoice_buyer_email: preference.buyerEmail.trim().toLowerCase(),
    invoice_carrier_type: preference.carrierType,
    ...(preference.buyerType === "company"
      ? {
          invoice_buyer_tax_id: preference.buyerTaxId.trim(),
          invoice_buyer_name: preference.buyerName.trim(),
        }
      : {}),
    ...(preference.carrierType === "mobile_barcode" && preference.carrierValue
      ? { invoice_carrier_value: preference.carrierValue.trim().toUpperCase() }
      : {}),
  };
}

export type CheckoutInput = {
  items: Array<{ product_id: string; quantity: number }>;
  contactEmail: string;
  invoicePreference: InvoicePreference;
  fulfillment:
    | { kind: "pickup"; pickupLocationId: string }
    | {
        kind: "shipping";
        channel: ShippingChannel;
        temperature: ShippingTemperature;
        recipientName: string;
        recipientPhone: string;
        shippingAddress: string;
      };
};

export type CheckoutOutcome =
  | { kind: "redirect"; order: Order; url: string; step: "logistics" | "payment" }
  | { kind: "resume"; order: Order; message: string };

export async function beginCheckout(
  input: CheckoutInput,
  gateway: CommerceGateway = httpCommerceGateway,
): Promise<CheckoutOutcome> {
  const order = await gateway.createOrder({
    items: input.items,
    contact_email: input.contactEmail,
    ...invoicePreferencePayload(input.invoicePreference),
    fulfillment_method:
      input.fulfillment.kind === "shipping"
        ? "ecpay_logistics"
        : "cooperative_pickup",
    ...(input.fulfillment.kind === "pickup"
      ? { pickup_location_id: input.fulfillment.pickupLocationId }
      : {}),
  });

  try {
    if (input.fulfillment.kind === "shipping") {
      const selection = await gateway.createLogisticsSelection(order.id, {
        channel: input.fulfillment.channel,
        temperature: input.fulfillment.temperature,
        recipient_name: input.fulfillment.recipientName,
        recipient_phone: input.fulfillment.recipientPhone,
        shipping_address: input.fulfillment.shippingAddress,
      });
      if (!selection.selection_url) throw new Error("物流選擇頁尚未建立");
      return {
        kind: "redirect",
        order,
        url: selection.selection_url,
        step: "logistics",
      };
    }
    const payment = await gateway.createPaymentAttempt(order.id);
    if (!payment.payment_url) throw new Error("付款頁尚未建立");
    return {
      kind: "redirect",
      order,
      url: payment.payment_url,
      step: "payment",
    };
  } catch (reason) {
    return {
      kind: "resume",
      order,
      message:
        reason instanceof Error
          ? reason.message
          : "訂單已建立，但下一步暫時無法開啟",
    };
  }
}

export async function createOrderPayment(orderId: string): Promise<string> {
  const attempt = await httpCommerceGateway.createPaymentAttempt(orderId);
  return attempt.payment_url;
}

export async function reissueLogisticsSelection(orderId: string): Promise<string> {
  const selection = await apiFetch<LogisticsSelection>(
    `/v1/orders/${orderId}/logistics/selection-link`,
    { method: "POST" },
  );
  return selection.selection_url;
}

export async function createMembershipPayment(chargeId: string): Promise<string> {
  const attempt = await apiFetch<PaymentAttempt>(
    `/v1/membership/charges/${chargeId}/payment-attempts`,
    { method: "POST" },
  );
  return attempt.payment_url;
}

export async function refreshPaymentAttempt(
  attemptId: string,
): Promise<PaymentAttemptStatus> {
  return apiFetch<PaymentAttemptStatus>(
    `/v1/payment-attempts/${attemptId}/refresh`,
    { method: "POST" },
  );
}

export type GroupCheckoutInput = Omit<CheckoutInput, "items"> & {
  campaignId: string;
  quantity: number;
};

export async function beginGroupCheckout(
  input: GroupCheckoutInput,
): Promise<CheckoutOutcome> {
  const order = await apiFetch<Order>(
    `/v1/group-campaigns/${input.campaignId}/join`,
    {
      method: "POST",
      body: JSON.stringify({
        quantity: input.quantity,
        contact_email: input.contactEmail,
        ...invoicePreferencePayload(input.invoicePreference),
        fulfillment_method:
          input.fulfillment.kind === "shipping"
            ? "ecpay_logistics"
            : "cooperative_pickup",
        ...(input.fulfillment.kind === "pickup"
          ? { pickup_location_id: input.fulfillment.pickupLocationId }
          : { shipping_channel: input.fulfillment.channel }),
      }),
    },
  );
  try {
    if (input.fulfillment.kind === "shipping") {
      const selection = await httpCommerceGateway.createLogisticsSelection(
        order.id,
        {
          channel: input.fulfillment.channel,
          temperature: input.fulfillment.temperature,
          recipient_name: input.fulfillment.recipientName,
          recipient_phone: input.fulfillment.recipientPhone,
          shipping_address: input.fulfillment.shippingAddress,
        },
      );
      return { kind: "redirect", order, url: selection.selection_url, step: "logistics" };
    }
    const payment = await httpCommerceGateway.createPaymentAttempt(order.id);
    return { kind: "redirect", order, url: payment.payment_url, step: "payment" };
  } catch (reason) {
    return {
      kind: "resume",
      order,
      message: reason instanceof Error ? reason.message : "團購訂單已建立，請從訂單中心繼續",
    };
  }
}

export type MealCheckoutOutcome =
  | { kind: "redirect"; orderId: string; url: string }
  | { kind: "resume"; orderId: string; message: string };

export async function beginMealCheckout(input: {
  eventId: string;
  pickupAt: string;
  items: Array<{
    offering_id: string;
    quantity: number;
    option_ids: string[];
  }>;
  contactEmail: string;
  invoicePreference: InvoicePreference;
}): Promise<MealCheckoutOutcome> {
  const order = await apiFetch<{ id: string }>(
    `/v1/meal-events/${input.eventId}/orders`,
    {
      method: "POST",
      body: JSON.stringify({
        items: input.items,
        pickup_at: input.pickupAt,
        contact_email: input.contactEmail,
        ...invoicePreferencePayload(input.invoicePreference),
      }),
    },
  );
  try {
    const payment = await httpCommerceGateway.createPaymentAttempt(order.id);
    return { kind: "redirect", orderId: order.id, url: payment.payment_url };
  } catch (reason) {
    return {
      kind: "resume",
      orderId: order.id,
      message: reason instanceof Error ? reason.message : "便當訂單已建立，請從訂單中心繼續",
    };
  }
}
