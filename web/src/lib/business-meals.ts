export interface BusinessMealPayment {
  meal_booking_type?: "standard" | "business";
  payment_opens_at?: string | null;
  payment_closes_at?: string | null;
  payment_followup_required?: boolean;
  payment_method?: string;
  payment_status: string;
  cancelled_at?: string | null;
  available_actions: string[];
}

export function businessMealPaymentState(order: BusinessMealPayment, now = Date.now()) {
  if (order.meal_booking_type !== "business" || order.cancelled_at || !["pending", "failed", "expired"].includes(order.payment_status)) return null;
  const start = order.payment_opens_at ? Date.parse(order.payment_opens_at) : NaN;
  const end = order.payment_closes_at ? Date.parse(order.payment_closes_at) : NaN;
  if (!Number.isFinite(start) || !Number.isFinite(end) || start >= end) return "unavailable";
  if (now < start) return "not_open";
  if (now >= end || order.payment_followup_required) return "overdue";
  if (order.payment_method === "cash_on_pickup" && order.payment_status === "pending") return "open";
  return order.available_actions.includes("pay") ? "open" : "unavailable";
}

export function mealPaymentMethodLabel(order: Pick<BusinessMealPayment, "payment_method" | "meal_booking_type" | "payment_status">) {
  if (order.payment_method !== "cash_on_pickup") return "線上付款";
  if (order.meal_booking_type !== "business") return "合作社現場付款";
  if (["paid", "refund_pending", "refunded"].includes(order.payment_status)) return "現金・已收";
  return "現金・未收";
}
