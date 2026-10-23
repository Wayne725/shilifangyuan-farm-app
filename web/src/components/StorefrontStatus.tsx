import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { apiFetch } from "../lib/api";

export interface StorefrontStatus {
  orders_open: boolean;
  closed_message: string;
  support_phone?: string;
  sales_scope?: "all" | "meals_only" | "pickup";
  regular_orders_enabled?: boolean;
  group_orders_enabled?: boolean;
  shipping_enabled?: boolean;
  limited_product_ids?: string[];
}

export function storefrontCapabilities(status?: StorefrontStatus) {
  return {
    regular: status?.regular_orders_enabled ?? status?.sales_scope !== "meals_only",
    group: status?.group_orders_enabled ?? status?.sales_scope !== "meals_only",
    shipping: status?.shipping_enabled ?? (status?.sales_scope == null || status.sales_scope === "all"),
  };
}

export function storefrontOrderBlockReason(
  status: StorefrontStatus | undefined,
  channel: "regular" | "group",
  items: Array<{ product_id: string; quantity: number }> = [],
) {
  if (status?.orders_open === false) return status.closed_message || "目前暫停接受新訂單";
  const limitedProductAllowed = channel === "regular" && items.length === 1
    && items[0].quantity === 1 && status?.limited_product_ids?.includes(items[0].product_id);
  if (limitedProductAllowed) return "";
  if (!storefrontCapabilities(status)[channel]) {
    return channel === "regular" ? "目前未開放一般商品訂購" : "目前未開放團購訂購";
  }
  return "";
}

export function useStorefrontStatus() {
  return useQuery({ queryKey: ["public-platform"], queryFn: () => apiFetch<StorefrontStatus>("/v1/platform"), refetchInterval: 30000, staleTime: 15000, retry: false });
}
export function StorefrontClosed({ message }: { message: string }) {
  return <section className="account-gate"><p className="eyebrow">ORDERING PAUSED / 暫停接單</p><h1>{message}</h1><Link className="button button-quiet" to="/orders">查看既有訂單</Link></section>;
}
