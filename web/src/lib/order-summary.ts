export type OrderSummarySalesChannel = "meal_preorder" | "regular" | "group";
export type OrderSummaryStatus = "active" | "paid" | "pending" | "inactive" | "all";

export const orderSummaryChannelLabels = {
  all: "全部銷售類型",
  meal_preorder: "便當",
  regular: "日常選品",
  group: "共同購買",
} as const;

export const orderSummaryStatusLabels = {
  active: "有效訂單",
  paid: "已付款",
  pending: "待付款",
  inactive: "取消／失效／退款",
  all: "全部訂單",
} as const;

export interface OrderSummaryFilters {
  startsOn: string;
  endsOn: string;
  salesChannel: OrderSummarySalesChannel | "all";
  status: OrderSummaryStatus;
  category: string;
  search: string;
}

export interface OrderSummaryProduct {
  key: string;
  sales_channel: OrderSummarySalesChannel;
  category: string;
  name: string;
  unit: string;
  quantity: number;
  order_count: number;
}

export interface OrderSummaryReport {
  starts_on: string;
  ends_on: string;
  sales_channel: OrderSummarySalesChannel | "all";
  status: OrderSummaryStatus;
  search: string;
  category: string;
  summary: {
    order_count: number;
    quantity: number;
    product_count: number;
    paid_order_count: number;
    pending_order_count: number;
    inactive_order_count: number;
  };
  categories: Array<{ category: string; order_count: number; quantity: number }>;
  channels: Array<{ sales_channel: OrderSummarySalesChannel; order_count: number; quantity: number }>;
  category_options: string[];
  products: {
    items: OrderSummaryProduct[];
    total: number;
    limit: number;
    offset: number;
  };
}
