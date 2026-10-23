import type { MealOrder, Order } from "./types";

export function filterOrders<T extends Order | MealOrder>(orders: T[], query: string, payment: string): T[] {
  const terms = query.trim().normalize("NFKC").toLocaleLowerCase().split(/\s+/).filter(Boolean);
  return orders.filter((order) => {
    if (payment && order.payment_status !== payment) return false;
    const details = "meal_event_title" in order
      ? [order.meal_event_title, order.venue_name, ...order.items.map((item) => item.meal_name)]
      : [order.fulfillment?.pickup_location, ...order.items.map((item) => item.product_name)];
    const text = [order.order_number, ...details].join(" ").normalize("NFKC").toLocaleLowerCase();
    return terms.every((term) => text.includes(term));
  });
}
