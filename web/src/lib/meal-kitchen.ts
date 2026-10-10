export interface KitchenOrder {
  id: string;
  order_number: string;
  customer_name: string;
  meal_event_id: string;
  event_title: string;
  meal_period?: string | null;
  location: string;
  pickup_at: string;
  pickup_time_confirmed: boolean;
  fulfillment_status: "pending_confirmation" | "preparing" | "ready_for_pickup";
  pending_cancellation?: { reason: string; requested_at: string } | null;
  items: Array<{ name: string; quantity: number; unit: string; options: Array<{ group_name: string; name: string }> }>;
}

export interface KitchenList {
  service_date: string;
  meal_period: string;
  total: number;
  limit: number;
  offset: number;
  meal_quantity: number;
  products: Array<{ name: string; unit: string; quantity: number }>;
  items: KitchenOrder[];
}
