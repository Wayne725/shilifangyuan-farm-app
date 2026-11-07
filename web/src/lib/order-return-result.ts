import type { Order, PaymentAttemptStatus } from "./types";

type ReturnedOrder = Pick<Order, "id" | "revision" | "payment_status" | "fulfillment_status" | "shipment" | "group_payment_uncertain">;
export interface OrderReturnSearch {
  orderId?: string; attemptId?: string; result?: string; payment?: string; logistics?: string;
}
export interface OrderReturnNotice {
  kind: string; title: string; message: string; confirming: boolean; success: boolean;
}
export interface OrderReturnVerification {
  orderReading?: boolean; orderError?: boolean; attemptReading?: boolean; attemptError?: boolean; attempt?: PaymentAttemptStatus;
}

export function attemptMatchesReturnedOrder(order: ReturnedOrder | undefined, attempt: PaymentAttemptStatus | undefined,
  orderId?: string, attemptId?: string): boolean {
  return Boolean(orderId && attemptId && order?.id === orderId && attempt?.id === attemptId && attempt.order_id === orderId
    && Number.isInteger(order.revision) && (order.revision || 0) > 0
    && attempt.order_revision === order.revision && attempt.superseded === false);
}

export function resolveOrderReturn(search: OrderReturnSearch, order?: ReturnedOrder,
  state: OrderReturnVerification = {}): OrderReturnNotice | null {
  if (!search.result && !search.payment && !search.logistics && !search.attemptId) return null;
  const notice = (kind: string, title: string, message: string, confirming = false, success = false) => ({ kind, title, message, confirming, success });
  if (!search.orderId) return notice("unconfirmed", "無法確認返回訂單", "請從本人訂單清單核對目前狀態。");
  if (state.orderError) return notice("unconfirmed", "返回結果暫時無法確認", "請更新訂單後重新核對，目前無法確認這次返回結果。");
  if (state.orderReading) return notice("confirming", search.logistics ? "物流資料確認中" : "付款結果確認中", "正在讀取這筆訂單的目前狀態。", !search.logistics);
  if (!order || order.id !== search.orderId) return notice("unconfirmed", "無法確認返回訂單", "請從本人訂單清單選擇訂單，或更新後重新核對。");
  if (order.payment_status === "paid") return notice("paid", "付款已確認", "這筆訂單目前已付款，發票與取貨進度請以下方狀態為準。", false, true);
  if (order.payment_status === "refunded") return notice("refunded", "退款已完成", "這筆訂單目前已退款，請以下方紀錄為準。", false, true);
  if (order.payment_status === "refund_pending") return notice("refund_pending", "退款處理中", "這筆訂單的退款尚未完成，請查看下方進度。");
  if (order.payment_status === "late_paid_refund_required") return notice("late_paid_refund_required", "付款晚到，退款待處理", "這筆訂單的晚到款項仍待退款，請勿再次付款。");
  if (order.payment_status === "expired") return notice("expired", "訂單付款已逾期", "目前未確認這筆訂單已付款；若已扣款，請等待後續核對。");
  if (order.payment_status === "failed") return notice("failed", "付款尚未完成", "這筆訂單目前顯示付款失敗，請依下方可用操作處理。");
  if (order.fulfillment_status === "cancelled") return notice("cancelled", "訂單已取消", "請查看下方訂單與退款進度。");
  if (order.group_payment_uncertain) return notice("confirming", "付款結果確認中", "付款結果仍待核對，請勿再次下單或付款。", true);
  if (state.attemptError) return notice("unconfirmed", "無法確認這次付款結果", "付款查詢暫時無法完成，請更新訂單查看目前狀態。");
  if (state.attemptReading) return notice("confirming", "付款結果確認中", "正在核對這筆訂單的付款狀態。", true);
  if (state.attempt) {
    const matching = attemptMatchesReturnedOrder(order, state.attempt, search.orderId, search.attemptId);
    const contradictory = state.attempt.id !== search.attemptId
      || (state.attempt.order_id !== undefined && state.attempt.order_id !== order.id)
      || (state.attempt.order_revision != null && order.revision != null && state.attempt.order_revision !== order.revision)
      || state.attempt.superseded === true;
    if (contradictory || (!matching && !["pending", "confirming"].includes(state.attempt.status))) {
      return notice("unconfirmed", "無法確認這次付款結果", "此付款查詢無法核對目前訂單，請更新訂單查看最新進度。");
    }
    if (["pending", "confirming"].includes(state.attempt.status) || (matching && ["succeeded", "paid"].includes(state.attempt.status))) {
      return notice("confirming", "付款結果確認中", "目前訂單尚未確認已付款，正在更新狀態。", true);
    }
  }
  if (search.logistics === "selected") {
    const confirmed = order.shipment && ["ready_to_create", "created", "in_transit", "delivered"].includes(order.shipment.status);
    return confirmed ? notice("selected", "物流資料已確認", "已取得這筆訂單的物流資料，請依下方進度繼續。", false, true)
      : notice("unconfirmed", "物流資料尚未確認", "請依下方訂單進度核對取貨門市或配送資料。");
  }
  if (search.result === "resume") return notice("resume", "訂單已保存，請從這裡繼續", "付款或物流服務暫時未開啟，請從下方原訂單繼續處理。");
  if (search.payment === "confirming" && !state.attempt) return notice("confirming", "付款結果確認中", "目前尚未確認這筆訂單已付款，正在更新訂單狀態。", true);
  return notice("pending", "訂單尚待付款", "目前尚未確認這筆訂單已付款，請依下方可用操作處理。");
}

export function holdReturnedOrderActions(search: OrderReturnSearch, order?: ReturnedOrder, state: OrderReturnVerification = {}): boolean {
  if (search.payment !== "confirming" || !search.orderId) return false;
  if (!order || order.id !== search.orderId) return true;
  if (["paid", "refunded", "refund_pending", "late_paid_refund_required", "failed", "expired"].includes(order.payment_status)) return false;
  if (order.payment_status !== "pending") return true;
  if (state.orderReading || state.orderError || state.attemptReading || state.attemptError) return true;
  return !(attemptMatchesReturnedOrder(order, state.attempt, search.orderId, search.attemptId)
    && state.attempt && ["failed", "expired"].includes(state.attempt.status));
}
