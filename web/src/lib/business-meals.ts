export interface BusinessMealPayment {
  meal_booking_type?: "standard" | "business";
  payment_opens_at?: string | null;
  payment_closes_at?: string | null;
  payment_followup_required?: boolean;
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
  return order.available_actions.includes("pay") ? "open" : "unavailable";
}
