import type {
  FulfillmentStatus,
  GroupDecisionStatus,
  InvoiceStatus,
  MembershipType,
  PaymentStatus,
  ProposalStatus,
  Shipment,
} from "../types";

export function money(value: number) {
  return `$${Math.round(value).toLocaleString("zh-TW")}`;
}

export function dateTime(value: string) {
  return new Intl.DateTimeFormat("zh-TW", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

export function shortDate(value: string) {
  return new Intl.DateTimeFormat("zh-TW", {
    month: "numeric",
    day: "numeric",
  }).format(new Date(value));
}

export function membershipLabel(value: MembershipType) {
  return value === "member" ? "社員" : "非社員";
}

export const proposalLabels: Record<ProposalStatus, string> = {
  pending_review: "待審核",
  voting: "投票中",
  ended_unmet: "未達門檻",
  conversion_pending: "待正式開團",
  converted: "已轉正式團購",
  rejected: "未通過",
  expired_unhandled: "處理期限已過",
};

export const campaignLabels: Record<GroupDecisionStatus, string> = {
  recruiting: "募集付款中",
  pending_confirmation: "待確認成團",
  confirmed: "已確認成團",
  rejected: "未成團",
  failed_unmet: "未達門檻",
  expired_unconfirmed: "確認逾期",
  cancelled: "已取消",
};

export const fulfillmentLabels: Record<FulfillmentStatus, string> = {
  pending_confirmation: "待確認",
  preparing: "備貨中",
  ready_for_pickup: "可取貨",
  picked_up: "已取貨",
  cancelled: "已取消",
};

export function fulfillmentStatusLabel(value: string) {
  return (
    {
      ...fulfillmentLabels,
      pending: "待處理",
      ready: "可取餐",
      awaiting_shipment: "待交寄",
      shipped: "配送中",
      delivered: "已送達",
      no_show: "逾時未取",
    }[value] ?? value
  );
}

export const shipmentLabels: Record<Shipment["status"], string> = {
  draft: "尚未選擇物流",
  selection_pending: "選擇物流中",
  ready_to_create: "待建立物流單",
  created: "物流單已建立",
  in_transit: "配送中",
  delivered: "已送達",
  exception: "配送異常",
  cancelled: "已取消",
};

export function shipmentStatusLabel(value: string) {
  return shipmentLabels[value as Shipment["status"]] ?? value;
}

/** Next Sandbox shipment status an admin may advance to, or null when stuck. */
export function nextShipmentStatus(
  value: Shipment["status"],
): "in_transit" | "delivered" | null {
  if (value === "created" || value === "exception") return "in_transit";
  if (value === "in_transit") return "delivered";
  return null;
}

export const paymentLabels: Record<PaymentStatus, string> = {
  pending: "待付款",
  paid: "已付款",
  late_paid_refund_required: "待退款",
  refund_pending: "退款處理中",
  refunded: "已退款",
  failed: "付款失敗",
  expired: "付款已逾期",
};

export const invoiceLabels: Record<InvoiceStatus, string> = {
  not_eligible: "取貨後開立",
  pending: "開立中",
  issued: "已開立",
  failed: "開立失敗",
};
