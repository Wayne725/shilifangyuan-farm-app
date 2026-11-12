import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch, formatMoney } from "../lib/api";
import type { Order } from "../lib/types";
import { AdminReasonChoices } from "./AdminReasonChoices";
import { CancellationReviewDetails } from "./CancellationReviewDetails";

export function AdminOrderCancellationReview({ order }: { order: Order }) {
  const client = useQueryClient();
  const [reason, setReason] = useState("已核對買家申請");
  const request = order.cancellation_request;
  const review = useMutation({
    mutationFn: (decision: "approve" | "reject") => apiFetch<Order>(`/v1/orders/${order.id}/admin/cancellation-requests/${request?.id}/review`, { method: "POST", body: JSON.stringify({ decision, reason }) }),
    onSuccess: () => client.invalidateQueries(),
  });
  if (!request || (order.sales_channel === "meal_preorder" && request.status === "pending")) return null;
  return <section className="order-cancellation-review" aria-label="退單審核">
    <h3>{request.status === "pending" ? "退單待審核" : request.status === "approved" ? "退單已核准" : "退單未核准"}</h3>
    {request.reason && <p>{request.initiated_by_admin ? "退單原因" : "買家申請原因"}：{request.reason}</p>}
    {request.review_reason && !request.initiated_by_admin && <p>審核原因：{request.review_reason}</p>}
    <CancellationReviewDetails request={request} />
    {request.status === "pending" && <>
      <AdminReasonChoices label="審核原因" value={reason} onChange={setReason} disabled={review.isPending} choices={[{ label: "已核對申請", value: "已核對買家申請" }, { label: "無法供貨", value: "商品無法供應" }, { label: "已交付商品", value: "商品已交付，無法退單" }]} />
      <div className="form-actions"><button className="button button-primary" type="button" disabled={review.isPending} onClick={() => review.mutate("approve")}>{order.payment_status === "paid" ? `核准退單並退款 ${formatMoney(order.amount_total)}` : "核准退單"}</button><button className="button button-quiet" type="button" disabled={review.isPending} onClick={() => review.mutate("reject")}>拒絕退單申請</button></div>
    </>}
    {review.isError && <p className="form-error" role="alert">{review.error.message}</p>}
    {review.isSuccess && <p className="form-success" role="status">{review.data.cancellation_request?.status === "approved" ? "已核准退單，可查看最新退款與訂單狀態。" : "已拒絕申請，原訂單繼續處理。"}</p>}
  </section>;
}
