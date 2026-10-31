import type { InfiniteData } from "@tanstack/react-query";
import type { MealOrder, Order } from "./types";

export type HistoryOrder = Order | MealOrder;
export type HistoryKind = "general" | "meal";
export interface HistoryCursor { createdAt: string; id: string }
export const orderHistoryPageSize = 25;

export function normalizeHistoryQuery(query: string): string {
  return query.normalize("NFKC").toLowerCase().trim().split(/\s+/u).filter(Boolean).join(" ");
}

export function orderHistoryPath(kind: HistoryKind, query: string, payment: string, cursor?: HistoryCursor | null): string {
  const params = new URLSearchParams({ limit: String(orderHistoryPageSize) });
  if (kind === "general") { params.set("own_only", "true"); params.set("history_scope", "general"); }
  const normalized = normalizeHistoryQuery(query);
  if (normalized) params.set("q", normalized);
  if (payment) params.set("payment_status", payment);
  if (cursor) { params.set("before_created_at", cursor.createdAt); params.set("before_id", cursor.id); }
  return `/v1/${kind === "meal" ? "meal-orders" : "orders"}?${params}`;
}

export function nextHistoryCursor<T extends { id: string; created_at: string }>(page: T[]): HistoryCursor | undefined {
  const last = page.at(-1);
  return page.length === orderHistoryPageSize && last?.id && last.created_at
    ? { createdAt: last.created_at, id: last.id } : undefined;
}

export function flattenHistoryPages<T extends { id: string }>(pages: T[][]): T[] {
  const seen = new Set<string>();
  return pages.flatMap(page => page.filter(order => {
    if (seen.has(order.id)) return false;
    seen.add(order.id); return true;
  }));
}

export function replaceHistoryOrder<T extends { id: string }>(data: InfiniteData<T[]> | undefined, updated: T): InfiniteData<T[]> | undefined {
  if (!data) return data;
  let changed = false;
  const pages = data.pages.map(page => page.map(order => {
    if (order.id !== updated.id || order === updated) return order;
    changed = true; return updated;
  }));
  return changed ? { ...data, pages } : data;
}

export function orderNeedsRefresh(order: HistoryOrder): boolean {
  if (("group_payment_uncertain" in order && order.group_payment_uncertain)
    || order.cancellation_request?.status === "pending") return true;
  if (!["paid", "refunded", "failed", "expired"].includes(order.payment_status)) return true;
  if (!["picked_up", "delivered", "no_show", "cancelled"].includes(order.fulfillment_status)) return true;
  if (!["issued", "voided", "not_eligible", "external"].includes(order.invoice_status)) return true;
  return "shipment" in order && Boolean(order.shipment && !["delivered", "cancelled"].includes(order.shipment.status));
}
