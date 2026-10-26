import { campaignTimestamp } from "./group-campaigns";
import type { MealOrder, Order, OrderQuantityAmendmentQuote } from "./types";

export type AmendableOrder = Order | MealOrder;

export function amendmentItems(order: AmendableOrder) {
  return order.items.map((item) => "meal_name" in item
    ? { id: item.order_item_id, name: item.meal_name, quantity: item.quantity, unit: "份", unit_price: item.unit_price, selections: item.selections.map((selection) => `${selection.group_name}：${selection.option_name}`).join("、") }
    : { id: item.id, name: item.product_name, quantity: item.quantity, unit: item.unit_label, unit_price: item.unit_price, selections: "" });
}

export function canAmendOrder(order: AmendableOrder, now = Date.now()) {
  return !("group_campaign_deadline" in order && order.group_campaign_deadline && campaignTimestamp(order.group_campaign_deadline) <= now)
    && order.available_actions.includes("amend_quantities")
    && Number.isInteger(order.revision) && (order.revision || 0) > 0
    && order.items.length > 0 && amendmentItems(order).every((item) => Boolean(item.id));
}

export function replaceAmendedOrder(data: unknown, updated: Order, quote: OrderQuantityAmendmentQuote): unknown {
  if (Array.isArray(data)) return data.map((item) => replaceAmendedOrder(item, updated, quote));
  if (!data || typeof data !== "object") return data;
  const record = data as Record<string, unknown>;
  if (record.id === updated.id && Array.isArray(record.items)) {
    if ("meal_event_title" in record) {
      const meal = data as MealOrder;
      return { ...meal, revision: updated.revision, amount_total: updated.amount_total, tax_amount: updated.tax_amount,
        available_actions: updated.available_actions, amendment_blocked_reason: updated.amendment_blocked_reason,
        amendments: updated.amendments, payment_status: updated.payment_status, fulfillment_status: updated.fulfillment_status,
        promotion: quote.promotion,
        items: meal.items.flatMap((item) => {
          const replacement = updated.items.find((next) => next.id === item.order_item_id);
          return replacement ? [{ ...item, quantity: replacement.quantity, unit_price: replacement.unit_price, subtotal: replacement.subtotal }] : [];
        }),
      };
    }
    return updated;
  }
  if (Array.isArray(record.items)) return { ...record, items: replaceAmendedOrder(record.items, updated, quote) };
  if (record.order) return { ...record, order: replaceAmendedOrder(record.order, updated, quote) };
  return data;
}
