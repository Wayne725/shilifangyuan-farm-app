export const orderCategoryLabels = { all: "全部訂單", general: "一般訂單", meal: "便當訂單" } as const;
export type OrderCategory = keyof typeof orderCategoryLabels;
export type IssueOrderCategory = OrderCategory | "system";

export function channelMatchesCategory(channel: string, category: OrderCategory) {
  return channel === "all" || category === "all" || (category === "meal" ? channel === "meal_preorder" : channel !== "meal_preorder");
}
