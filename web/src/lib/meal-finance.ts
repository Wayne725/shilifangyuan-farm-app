import type { InvoiceStatus } from "./types";

export interface MealSalesPeriod {
  period: string;
  received: number;
  refunded: number;
  net: number;
  receipt_count: number;
  refund_count: number;
}

export interface MealSalesOrder {
  revision?: number;
  receipts?: Array<{ merchant_trade_no: string; order_revision: number; amount: number; status: string; received_at: string | null; obsolete_revision: boolean }>;
  id: string;
  order_number: string;
  sales_channel: "meal_preorder" | "regular" | "group";
  customer_name: string;
  customer_email: string;
  membership_type: string;
  created_at: string;
  paid_at?: string | null;
  order_amount: number;
  tax_amount: number;
  period_received: number;
  period_refunded: number;
  period_net: number;
  payment_status: string;
  fulfillment_status: string;
  pickup_at?: string | null;
  pickup_time_confirmed: boolean;
  event_title?: string | null;
  meal_period?: string | null;
  location?: string | null;
  invoice_status: InvoiceStatus;
  invoice_number?: string | null;
  cancellation_status?: string | null;
  items: Array<{
    name: string;
    unit: string;
    quantity: number;
    unit_price: number;
    subtotal: number;
    tax_type: string;
    options: Array<{ group_name: string; name: string; price_delta: number }>;
  }>;
  refunds: Array<{ amount: number; status: string; reason: string; requested_at: string; completed_at?: string | null; purpose?: string; payment_attempt_id?: string | null }>;
}

export interface MealSalesReport {
  starts_on: string;
  ends_on: string;
  summary: {
    received: number;
    refunded: number;
    net: number;
    receipt_count: number;
    refund_count: number;
    pending_refund_amount: number;
    paid_meal_quantity: number;
  };
  daily: MealSalesPeriod[];
  weekly: MealSalesPeriod[];
  monthly: MealSalesPeriod[];
  products: Array<{ name: string; unit: string; quantity: number; sales_amount: number; order_count: number }>;
  orders: { items: MealSalesOrder[]; total: number; limit: number; offset: number };
}

export function taipeiDate(now = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Taipei", year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
}

export function financeDateTime(value?: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-TW", { timeZone: "Asia/Taipei", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(value));
}

export function financePeriodPreset(mode: "today" | "week" | "month", today = taipeiDate()): { startsOn: string; endsOn: string } {
  if (mode === "today") return { startsOn: today, endsOn: today };
  if (mode === "month") return { startsOn: `${today.slice(0, 7)}-01`, endsOn: today };
  const date = new Date(`${today}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() - ((date.getUTCDay() + 6) % 7));
  return { startsOn: date.toISOString().slice(0, 10), endsOn: today };
}
