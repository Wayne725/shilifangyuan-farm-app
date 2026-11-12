import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { CancellationReviewDetails } from "./CancellationReviewDetails";

import { apiFetch, formatDateTime, formatMoney } from "../lib/api";
import { formatMealDateTime } from "../lib/meal-time";
import type { MealCancellationQueue, MealCancellationQueueItem } from "../lib/types";
import { DataState, LoadingLines } from "./Shared";
import { MealCancellationSummary } from "./MealCancellationSummary";
import { AdminReasonChoices } from "./AdminReasonChoices";
import "./AdminMealFilters.css";

const reviewLabels = { pending: "待審核", approved: "已核准", rejected: "未核准" };
const paymentLabels: Record<string, string> = { pending: "待付款", paid: "已付款", refund_pending: "退款中", late_paid_refund_required: "付款晚到，退款處理中", refunded: "已退款", expired: "已逾期", failed: "付款失敗" };
const invoiceLabels: Record<string, string> = { not_eligible: "尚未開立", pending: "開立中", issued: "已開立", failed: "開立待處理", void_pending: "作廢中", voided: "已作廢" };
const pageSize = 30;
const approvalReasons = [
  { label: "依申請核准", value: "依買家申請核准退單。" },
  { label: "餐點無法供應", value: "餐點無法供應，核准退單。" },
  { label: "重複訂單", value: "重複訂單，核准退單。" },
];
const rejectionReasons = [
  { label: "已開始製作", value: "餐點已開始製作，無法取消。" },
  { label: "已完成取餐", value: "訂單已完成取餐，無法取消。" },
  { label: "需聯絡確認", value: "請電話聯絡合作社確認訂單。" },
];

export function AdminMealCancellations({ initialSearch = "", initialOrderId }: { initialSearch?: string; initialOrderId?: string }) {
  const [status, setStatus] = useState("pending");
  const [searchInput, setSearchInput] = useState(initialSearch);
  const [search, setSearch] = useState(initialSearch);
  const [orderId, setOrderId] = useState(initialOrderId);
  const [offset, setOffset] = useState(0);
  const [startsOn, setStartsOn] = useState("");
  const [endsOn, setEndsOn] = useState("");
  const filters = new URLSearchParams({ search });
  if (orderId) filters.set("order_id", orderId);
  if (startsOn) filters.set("starts_on", startsOn);
  if (endsOn) filters.set("ends_on", endsOn);
  const params = `${filters}&review_status=${status}&limit=${pageSize}&offset=${offset}`;
  const queue = useQuery({
    queryKey: ["admin-meal-cancellations", params],
    queryFn: () => apiFetch<MealCancellationQueue>(`/v1/admin/meal-cancellation-requests?${params}`),
    refetchInterval: 15000,
  });
  // Keep review actions scoped even when an older API ignores order_id.
  const items = queue.isError ? undefined : queue.data?.items.filter(item => !orderId || item.order.id === orderId);
  const total = items && items.length !== queue.data?.items.length ? items.length : queue.data?.total ?? 0;
  return <section className="meal-cancellation-management" aria-labelledby="meal-cancellation-heading">
    <h2 id="meal-cancellation-heading">退單審核</h2>
    <div className="field-grid two-columns"><label className="field"><span>申請開始日期</span><input type="date" value={startsOn} onChange={event => { setStartsOn(event.target.value); setOffset(0); }} /></label><label className="field"><span>申請結束日期</span><input type="date" min={startsOn} value={endsOn} onChange={event => { setEndsOn(event.target.value); setOffset(0); }} /></label></div>
    <MealCancellationSummary filters={filters.toString()} status={status} orderId={orderId} />
    <form className="admin-order-search" onSubmit={(event) => {
      event.preventDefault();
      const term = searchInput.trim();
      if (term !== search) setOrderId(undefined);
      setSearch(term);
      setOffset(0);
    }}>
      <label>訂單編號或 Email<input value={searchInput} maxLength={100} onChange={(event) => setSearchInput(event.target.value)} /></label>
      <label>退單審核狀態<select value={status} onChange={(event) => { setStatus(event.target.value); setOffset(0); }}>{["pending", "approved", "rejected", "all"].map((key) => <option value={key} key={key}>{key === "all" ? "全部紀錄" : reviewLabels[key as keyof typeof reviewLabels]}</option>)}</select></label>
      <button className="button button-system" type="submit">搜尋</button>
    </form>
    {queue.isPending && <LoadingLines count={3} />}
    {queue.isError && <DataState kind="error" title="退單申請無法讀取" detail={queue.error.message} onAction={() => queue.refetch()} />}
    {items?.length === 0 && <DataState title="沒有符合條件的退單申請" detail="可切換審核狀態或修改搜尋條件。" />}
    <div className="admin-review-list">{items?.map((item) => <CancellationReviewCard key={item.request.id} item={item} />)}</div>
    {!queue.isError && queue.data && <div className="admin-pagination">
      <span>共 {total} 筆・第 {Math.floor(offset / pageSize) + 1} 頁</span>
      <button className="button button-quiet" type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - pageSize))}>上一頁</button>
      <button className="button button-quiet" type="button" disabled={offset + pageSize >= total} onClick={() => setOffset(offset + pageSize)}>下一頁</button>
    </div>}
  </section>;
}

function CancellationReviewCard({ item }: { item: MealCancellationQueueItem }) {
  const { request, order } = item;
  const queryClient = useQueryClient();
  const [decision, setDecision] = useState<"approve" | "reject" | "">("");
  const [reason, setReason] = useState("");
  const review = useMutation({
    mutationFn: () => apiFetch(`/v1/admin/meal-cancellation-requests/${request.id}/review`, {
      method: "POST", body: JSON.stringify({ decision, reason: reason.trim() }),
    }),
    onSuccess: async () => {
      setDecision(""); setReason("");
      await queryClient.invalidateQueries();
    },
    onError: () => { void queryClient.invalidateQueries({ queryKey: ["admin-meal-cancellations"] }); },
  });
  const beginReview = (next: "approve" | "reject") => { setDecision(next); setReason(next === "approve" ? approvalReasons[0].value : ""); review.reset(); };
  return <article className="admin-record-card meal-cancellation-card">
    <span className="status-chip">{reviewLabels[request.status]}</span>
    <h3 className="order-number">{order.order_number}</h3>
    <p>{item.customer_name}・{order.venue_name}</p>
    <p>預計取餐：{order.pickup_at ? formatMealDateTime(order.pickup_at) : "未指定，依供餐時段"}</p>
    <ul>{order.items.map((meal, index) => <li key={`${meal.offering_id}-${index}`}>{meal.meal_name} × {meal.quantity}{meal.selections.length > 0 && `（${meal.selections.map((selection) => `${selection.group_name}：${selection.option_name}`).join("、")}）`}</li>)}</ul>
    <p><strong>{formatMoney(order.amount_total)}</strong>・{paymentLabels[order.payment_status] || order.payment_status}・發票{invoiceLabels[order.invoice_status] || order.invoice_status}</p>
    {request.reason && <p className="meal-cancellation-reason">退單原因：{request.reason}</p>}
    {request.review_reason && !request.initiated_by_admin && <p className="meal-cancellation-reason">審核理由：{request.review_reason}</p>}
    <CancellationReviewDetails request={request} />
    <details className="admin-extra-actions"><summary>更多訂單資料</summary><p>{item.customer_email}・{order.meal_event_title}</p><p>履約：{order.cancelled_at ? "已取消" : Date.parse(order.pickup_end) <= Date.now() ? "已結束" : order.fulfillment_status === "preparing" ? "製作中" : order.fulfillment_status === "picked_up" ? "已取餐" : "供餐中"}</p><p>申請時間：{formatDateTime(request.requested_at)}</p></details>
    {request.status === "pending" && <>
      {!item.can_approve && <p>已取消或已結束履約，無法核准。</p>}
      {!decision && <div className="form-actions">
        <button className="button button-system" type="button" disabled={!item.can_approve || review.isPending} onClick={() => beginReview("approve")}>核准退單</button>
        <button className="button button-quiet" type="button" disabled={review.isPending} onClick={() => beginReview("reject")}>拒絕退單</button>
      </div>}
      {decision && <form className="meal-cancellation-review" onSubmit={(event) => { event.preventDefault(); if (!review.isPending && (decision === "reject" || item.can_approve)) review.mutate(); }}>
        <strong>{decision === "approve" ? "確認核准這筆退單" : "確認拒絕這筆退單"}</strong>
        <p>{order.order_number} · {decision === "approve" ? `取消整張訂單${order.payment_status === "paid" ? `，申請退款 ${formatMoney(order.amount_total)}` : ""}` : "保留原訂單"}</p>
        <AdminReasonChoices key={decision} label="審核理由" value={reason} onChange={setReason} choices={decision === "approve" ? approvalReasons : rejectionReasons} disabled={review.isPending} />
        <div className="form-actions">
          <button className="button button-system" type="submit" disabled={review.isPending || (decision === "approve" && !item.can_approve)}>{review.isPending ? "處理中…" : decision === "approve" ? "確認核准" : "確認拒絕"}</button>
          <button className="button button-quiet" type="button" disabled={review.isPending} onClick={() => setDecision("")}>返回</button>
        </div>
      </form>}
    </>}
    {review.isError && <p className="form-error" role="alert">{review.error.message}</p>}
  </article>;
}
