function createLabeler(labels: Readonly<Record<string, string>>) {
  return (value: string): string => labels[value] || value;
}

export const mealEventStatusLabel = createLabeler({
  draft: "草稿",
  published: "開放預訂",
  ordering_closed: "預訂截止",
  pickup_open: "開放取餐",
  cancelled: "已取消",
  completed: "已完成",
});

export const proposalStatusLabel = createLabeler({
  draft: "草稿",
  pending_review: "待審核",
  discussion: "討論中",
  voting: "表決中",
  passed: "通過",
  rejected: "未通過",
  withdrawn: "已撤回",
  closed: "已結案",
});

export const paymentStatusLabel = createLabeler({
  pending: "待付款",
  paid: "已付款",
  late_paid_refund_required: "逾期付款待退款",
  refund_pending: "退款處理中",
  refunded: "已退款",
  failed: "付款失敗",
  expired: "已取消／逾期",
});

export const adminPaymentStatusLabel = createLabeler({
  pending: "待付款",
  paid: "已付款",
  refunded: "已退款",
  expired: "已取消",
});

export const fulfillmentStatusLabel = createLabeler({
  pending_confirmation: "待確認",
  preparing: "備貨中",
  ready_for_pickup: "可領取",
  picked_up: "已完成",
  awaiting_shipment: "待建立物流單",
  shipped: "配送中",
  delivered: "已送達",
  exception: "配送異常",
  cancelled: "已取消",
});

export const invoiceStatusLabel = createLabeler({
  not_eligible: "尚未符合開立條件",
  pending: "等待開立",
  issued: "已開立",
  failed: "開立失敗",
  void_pending: "作廢處理中",
  voided: "已作廢",
});
