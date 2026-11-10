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
  saveCarrier?: boolean;
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
  clientRequestId?: string;
  expectedTotal?: number;
  expectedQuoteFingerprint?: string;
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
    ...(input.clientRequestId ? { client_request_id: input.clientRequestId } : {}),
    ...(input.expectedTotal !== undefined ? { expected_total: input.expectedTotal } : {}),
    ...(input.expectedQuoteFingerprint ? { expected_quote_fingerprint: input.expectedQuoteFingerprint } : {}),
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

  if (input.expectedTotal !== undefined && (order.amount_total !== input.expectedTotal || (order.revision ?? 1) > 1)) {
    return { kind: "resume", order, message: "此訂單內容已更新，請在訂單頁確認金額後付款" };
  }
  if (order.payment_status === "paid" || order.fulfillment_status === "cancelled") {
    return { kind: "resume", order, message: "已恢復原訂單，請查看目前處理狀態" };
  }

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
  signal?: AbortSignal,
): Promise<PaymentAttemptStatus> {
  return apiFetch<PaymentAttemptStatus>(
    `/v1/payment-attempts/${attemptId}/refresh`,
    { method: "POST", ...(signal ? { signal } : {}) },
  );
}

export type GroupCheckoutInput = Omit<CheckoutInput, "items"> & {
  campaignId: string;
  quantity: number;
};

export interface GroupCommerceGateway extends Pick<CommerceGateway, "createLogisticsSelection" | "createPaymentAttempt"> {
  joinCampaign(campaignId: string, body: Record<string, unknown>): Promise<Order>;
}

const httpGroupCommerceGateway: GroupCommerceGateway = {
  joinCampaign: (campaignId, body) => apiFetch<Order>(`/v1/group-campaigns/${campaignId}/join`, { method: "POST", body: JSON.stringify(body) }),
  createLogisticsSelection: httpCommerceGateway.createLogisticsSelection,
  createPaymentAttempt: httpCommerceGateway.createPaymentAttempt,
};

export async function beginGroupCheckout(
  input: GroupCheckoutInput,
  gateway: GroupCommerceGateway = httpGroupCommerceGateway,
  canContinue?: () => boolean,
): Promise<CheckoutOutcome> {
  const checkIdentity = () => { if (canContinue && !canContinue()) throw new Error("登入身分已變更，請確認目前帳號後重新操作。"); };
  checkIdentity();
  const order = await gateway.joinCampaign(input.campaignId, {
        ...(input.clientRequestId ? { client_request_id: input.clientRequestId } : {}),
        ...(input.expectedTotal !== undefined ? { expected_total: input.expectedTotal } : {}),
        ...(input.expectedQuoteFingerprint ? { expected_quote_fingerprint: input.expectedQuoteFingerprint } : {}),
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
  });
  checkIdentity();
  if ("group_payment_uncertain" in order && order.group_payment_uncertain === true) {
    return { kind: "resume", order, message: "付款結果仍待核對，請勿再次下單或付款" };
  }
  if (order.group_contract_changed || (order.revision ?? 1) > 1 || order.fulfillment_status === "cancelled"
    || (order.payment_status && !["pending", "failed", "expired"].includes(order.payment_status))
    || (input.fulfillment.kind === "pickup" && input.expectedTotal !== undefined && order.amount_total !== input.expectedTotal)) {
    return { kind: "resume", order, message: "已恢復原共同購買訂單，請在訂單中心確認目前內容與處理狀態" };
  }
  try {
    if (input.fulfillment.kind === "shipping") {
      const selection = await gateway.createLogisticsSelection(
        order.id,
        {
          channel: input.fulfillment.channel,
          temperature: input.fulfillment.temperature,
          recipient_name: input.fulfillment.recipientName,
          recipient_phone: input.fulfillment.recipientPhone,
          shipping_address: input.fulfillment.shippingAddress,
        },
      );
      checkIdentity();
      if (!selection.selection_url) throw new Error("物流選擇頁尚未建立");
      return { kind: "redirect", order, url: selection.selection_url, step: "logistics" };
    }
    const payment = await gateway.createPaymentAttempt(order.id);
    checkIdentity();
    if (!payment.payment_url) throw new Error("付款頁尚未建立");
    return { kind: "redirect", order, url: payment.payment_url, step: "payment" };
  } catch (reason) {
    checkIdentity();
    return {
      kind: "resume",
      order,
      message: reason instanceof Error ? reason.message : "共同購買訂單已建立，請從訂單中心繼續",
    };
  }
}

export type MealCheckoutOutcome =
  | { kind: "redirect"; orderId: string; url: string }
  | { kind: "reserved"; orderId: string }
  | { kind: "resume"; orderId: string; message: string };

export async function beginMealCheckout(input: {
  mealBookingType?: "standard" | "business";
  clientRequestId?: string;
  kitchenNote?: string;
  couponCode?: string;
  expectedTotal?: number;
  eventId: string;
  pickupAt: string;
  pickupLocationId?: string;
  needsUtensils?: boolean;
  items: Array<{
    offering_id: string;
    quantity: number;
    option_ids: string[];
  }>;
  contactEmail: string;
  invoicePreference: InvoicePreference;
  paymentMethod?: "online" | "cash_on_pickup";
  contactPhone?: string;
  invoiceHandling?: "on_payment" | "pickup_day" | "external";
}): Promise<MealCheckoutOutcome> {
  const business = input.mealBookingType === "business";
  const paymentMethod = business ? "online" : input.paymentMethod || "online";
  const invoiceHandling = business ? "pickup_day" : input.invoiceHandling;
  const legacyInvoiceHandling = paymentMethod === "cash_on_pickup" ? "external" : "on_payment";
  const order = await apiFetch<{ id: string; payment_method?: string; meal_booking_type?: string }>(
    `/v1/meal-events/${input.eventId}/orders`,
    {
      method: "POST",
      body: JSON.stringify({
        ...(input.clientRequestId ? {client_request_id: input.clientRequestId} : {}),
        ...(input.kitchenNote ? {kitchen_note: input.kitchenNote} : {}),
        items: input.items,
        ...(business ? { meal_booking_type: "business" } : {}),
        ...(input.couponCode ? {coupon_code: input.couponCode} : {}),
        ...(input.expectedTotal !== undefined ? {expected_total: input.expectedTotal} : {}),
        pickup_at: input.pickupAt,
        pickup_location_id: input.pickupLocationId || null,
        needs_utensils: input.needsUtensils ?? false,
        contact_email: input.contactEmail,
        ...(input.contactPhone?.trim() ? { contact_phone: input.contactPhone.trim() } : {}),
        payment_method: paymentMethod,
        ...(invoiceHandling && invoiceHandling !== legacyInvoiceHandling ? { invoice_handling: invoiceHandling } : {}),
        ...(paymentMethod === "online" || (invoiceHandling && invoiceHandling !== "external") ? invoicePreferencePayload(input.invoicePreference) : {}),
      }),
    },
  );
  if (business) {
    return order.meal_booking_type === "business"
      ? { kind: "reserved", orderId: order.id }
      : { kind: "resume", orderId: order.id, message: "會餐預訂尚未確認，請聯絡合作社核對，勿重複下單。" };
  }
  if (paymentMethod === "cash_on_pickup") {
    if (order.payment_method !== "cash_on_pickup") {
      return { kind: "resume", orderId: order.id, message: "現場付款預訂尚未確認，請聯絡合作社核對，勿重複下單。" };
    }
    return { kind: "reserved", orderId: order.id };
  }
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
