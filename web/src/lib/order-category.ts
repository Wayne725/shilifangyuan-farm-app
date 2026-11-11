export const orderCategoryLabels = { all: "全部訂單", general: "一般訂單", meal: "便當訂單" } as const;
export type OrderCategory = keyof typeof orderCategoryLabels;
export type IssueOrderCategory = OrderCategory | "system";
export const financialOrderCategoryLabels = { ...orderCategoryLabels, walk_in_unclassified: "現場未分類" } as const;
export type FinancialOrderCategory = keyof typeof financialOrderCategoryLabels;

export function channelMatchesCategory(channel: string, category: OrderCategory) {
  return channel === "all" || category === "all" || (category === "meal" ? channel === "meal_preorder" : channel !== "meal_preorder");
}
