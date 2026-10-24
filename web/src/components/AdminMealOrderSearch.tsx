import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import { apiFetch, formatDateTime, formatMoney } from "../lib/api";
import { invoiceStatusLabel, paymentStatusLabel } from "../lib/labels";
import { formatMealDateTime, shiftMealDate, taipeiDate } from "../lib/meal-time";
import type { MealAdminOrderItem, MealAdminOrderPage, MealOperationsSummary } from "../lib/types";
import { InvoiceTaxSummary } from "./InvoiceTaxSummary";
import { OrderStatusBadge } from "./OrderStatusBadge";
import { AdminCancelOrder } from "./AdminCancelOrder";
import { AdminCashPayment } from "./AdminCashPayment";
import { DataState, LoadingLines } from "./Shared";
import "./AdminMealFilters.css";

const pageSize = 20;
const emptyFilters = { search: "", date_from: "", date_to: "", meal_period: "all", pickup_time: "", payment_status: "", fulfillment_status: "", invoice_status: "", review_status: "all", work_queue: "all" };
const fulfillmentLabels: Record<string, string> = { pending: "待確認", preparing: "製作中", ready: "待取餐", ready_for_pickup: "待取餐", picked_up: "已取餐", no_show: "逾時未取", cancelled: "已取消" };
const reviewLabels = { pending: "退單待審", approved: "退單已核准", rejected: "退單未核准" };
const workQueues = [
  { key: "pending_cancellation", title: "退單待審", detail: "等待核准或拒絕" },
  { key: "refund_attention", title: "退款待處理", detail: "退款中、晚付款或退款失敗" },
  { key: "invoice_attention", title: "發票待處理", detail: "開票／作廢錯誤或失敗工作" },
  { key: "today_pickup", title: "今日待取餐", detail: "已付款且仍可取餐" },
] as const;

export function AdminMealOrderSearch({ initialSearch = "", onReviewOrder }: { initialSearch?: string; onReviewOrder: (orderNumber: string) => void }) {
  const [draft, setDraft] = useState({ ...emptyFilters, search: initialSearch });
  const [filters, setFilters] = useState({ ...emptyFilters, search: initialSearch });
  const [offset, setOffset] = useState(0);
  const dateError = Boolean(draft.date_from && draft.date_to && draft.date_to < draft.date_from);
  const params = new URLSearchParams({ limit: String(pageSize), offset: String(offset) });
  Object.entries(filters).forEach(([key, value]) => { if (value && value !== "all") params.set(key, value); });
  const summary = useQuery({
    queryKey: ["admin-meal-operations-summary"],
    queryFn: () => apiFetch<MealOperationsSummary>("/v1/admin/meal-operations-summary"),
    refetchInterval: 30000,
  });
  const orders = useQuery({
    queryKey: ["admin-meal-orders", filters, offset],
    queryFn: () => apiFetch<MealAdminOrderPage>(`/v1/admin/meal-orders?${params}`),
    refetchInterval: 30000,
  });
  useEffect(() => {
    if (orders.data && offset > 0 && offset >= orders.data.total) {
      setOffset(orders.data.total === 0 ? 0 : Math.floor((orders.data.total - 1) / pageSize) * pageSize);
    }
  }, [orders.data, offset]);
  const apply = (next: typeof emptyFilters) => { setDraft(next); setFilters(next); setOffset(0); };
  const update = (key: keyof typeof emptyFilters, value: string) => setDraft((current) => ({ ...current, [key]: value }));

  return <section className="meal-order-management" aria-labelledby="meal-order-search-heading">
    <header className="meal-order-search-heading"><div><h2 id="meal-order-search-heading">全部便當訂單</h2></div><button className="button button-quiet" type="button" disabled={orders.isFetching || summary.isFetching} onClick={() => { void orders.refetch(); void summary.refetch(); }}>重新整理</button></header>
    <div className="meal-operation-tasks" aria-label="全部便當營運待辦">
      {workQueues.map((queue) => <button key={queue.key} type="button" aria-pressed={filters.work_queue === queue.key} onClick={() => apply({ ...emptyFilters, work_queue: queue.key })}>
        <span>{queue.title}</span><strong>{summary.data ? summary.data[queue.key] : "—"}</strong>
      </button>)}
    </div>
    <div className="admin-quick-buttons" role="group" aria-label="快速取餐日期">{[{ label: "全部日期", date: "" }, { label: "今天", date: taipeiDate() }, { label: "明天", date: shiftMealDate(taipeiDate(), 1) }].map(({ label, date }) => <button className="button button-quiet" key={label} type="button" aria-pressed={filters.date_from === date && filters.date_to === date && filters.work_queue === "all"} onClick={() => apply({ ...filters, date_from: date, date_to: date, work_queue: "all" })}>{label}</button>)}</div>
    {summary.isError && <DataState kind="error" title="待辦數量暫時無法更新" detail={summary.error.message} onAction={() => summary.refetch()} />}
    <form className="meal-order-filters" onSubmit={(event) => { event.preventDefault(); if (!dateError) apply({ ...draft, search: draft.search.trim() }); }}>
      <div className="meal-order-primary-filters">
        <label className="field"><span>訂單編號、姓名或 Email</span><input value={draft.search} maxLength={100} onChange={(event) => update("search", event.target.value)} /></label>
        <label className="field"><span>履約狀態</span><select value={draft.fulfillment_status} onChange={(event) => update("fulfillment_status", event.target.value)}><option value="">全部履約狀態</option><option value="pending_confirmation">待製作</option><option value="preparing">製作中</option><option value="ready_for_pickup">待取餐</option><option value="picked_up">已取餐</option><option value="no_show">逾時未取</option><option value="cancelled">已取消</option></select></label>
      </div>
      <details className="meal-order-advanced-filters"><summary>更多篩選</summary><div className="meal-order-filter-fields">
      <label className="field"><span>取餐日期（起）</span><input type="date" value={draft.date_from} onChange={(event) => update("date_from", event.target.value)} /></label>
      <label className="field"><span>取餐日期（迄）</span><input type="date" min={draft.date_from || undefined} value={draft.date_to} aria-invalid={dateError} onChange={(event) => update("date_to", event.target.value)} /></label>
      <label className="field"><span>午餐／晚餐</span><select value={draft.meal_period} onChange={(event) => update("meal_period", event.target.value)}><option value="all">全部餐別</option><option value="lunch">午餐</option><option value="dinner">晚餐</option><option value="unspecified">未標餐別</option></select></label>
      <label className="field"><span>預計取餐時間</span><input type="time" step={60} value={draft.pickup_time} onChange={(event) => update("pickup_time", event.target.value)} /></label>
      <label className="field"><span>付款狀態</span><select value={draft.payment_status} onChange={(event) => update("payment_status", event.target.value)}><option value="">全部付款狀態</option>{["pending", "paid", "refund_pending", "late_paid_refund_required", "refunded", "expired", "failed"].map((status) => <option value={status} key={status}>{paymentStatusLabel(status)}</option>)}</select></label>
      <label className="field"><span>發票狀態</span><select value={draft.invoice_status} onChange={(event) => update("invoice_status", event.target.value)}><option value="">全部發票狀態</option>{["not_eligible", "pending", "issued", "failed", "void_pending", "voided"].map((status) => <option value={status} key={status}>{invoiceStatusLabel(status)}</option>)}</select></label>
      <label className="field"><span>退單審核</span><select value={draft.review_status} onChange={(event) => update("review_status", event.target.value)}><option value="all">全部審核狀態</option><option value="none">沒有退單申請</option><option value="pending">待審核</option><option value="approved">已核准</option><option value="rejected">未核准</option></select></label>
      </div></details>
      <div className="form-actions"><button className="button button-system" type="submit" disabled={dateError}>查詢訂單</button><button className="button button-quiet" type="button" onClick={() => apply(emptyFilters)}>清除篩選</button></div>
      {dateError && <p className="form-error" role="alert">結束日期不能早於開始日期。</p>}
    </form>
    {filters.work_queue !== "all" && <p className="meal-order-active-queue" role="status">目前待辦：{workQueues.find((queue) => queue.key === filters.work_queue)?.title}</p>}
    {orders.isPending && <LoadingLines count={3} />}
    {orders.isError && <DataState kind="error" title="訂單查詢失敗" detail={orders.error.message} onAction={() => orders.refetch()} />}
    {orders.data?.items.length === 0 && <DataState title="沒有符合條件的便當訂單" detail="可調整日期、狀態或搜尋條件。" actionLabel="清除篩選" onAction={() => apply(emptyFilters)} />}
    {orders.data && <>
      <p className="meal-order-result-count" role="status">共 {orders.data.total} 筆{orders.data.total > 0 && `，顯示第 ${Math.min(offset + 1, orders.data.total)}～${Math.min(offset + pageSize, orders.data.total)} 筆`}</p>
      <div className="meal-operation-orders">{orders.data.items.map((item) => <MealOperationOrder key={item.order.id} item={item} onReviewOrder={onReviewOrder} />)}</div>
      <nav className="admin-pagination" aria-label="便當訂單分頁"><button className="button button-quiet" type="button" disabled={offset === 0 || orders.isFetching} onClick={() => setOffset(Math.max(0, offset - pageSize))}>上一頁</button><button className="button button-quiet" type="button" disabled={offset + pageSize >= orders.data.total || orders.isFetching} onClick={() => setOffset(offset + pageSize)}>下一頁</button></nav>
    </>}
  </section>;
}

function MealOperationOrder({ item, onReviewOrder }: { item: MealAdminOrderItem; onReviewOrder: (orderNumber: string) => void }) {
  const { order } = item;
  const client = useQueryClient();
  const update = useMutation({
    mutationFn: (status: string) => apiFetch(`/v1/admin/meal-kitchen/orders/${order.id}/status`, { method: "PATCH", body: JSON.stringify({ status }) }),
    onSuccess: () => Promise.all(["admin-meal-orders", "admin-meal-operations-summary", "admin-meal-event-orders", "meal-kitchen", "meal-kitchen-ticket", "admin-orders", "meal-orders"].map((key) => client.invalidateQueries({ queryKey: [key] }))),
    onError: () => void client.invalidateQueries({ queryKey: ["admin-meal-orders"] }),
  });
  const canPrepare = order.payment_status === "paid" && !order.cancelled_at && Date.parse(order.pickup_end) > Date.now();
  return <article className="meal-operation-order">
    <header><div><h3 className="order-number">{order.order_number}</h3><p>{item.customer_name} · {item.customer_email}</p></div><strong>{formatMoney(order.amount_total)}</strong></header>
    <p><strong>預計取餐：{formatMealDateTime(order.pickup_at || order.pickup_start)}</strong>{!order.pickup_at && "（舊訂單未指定，顯示場次開始時間）"}</p>
    <p>{order.meal_event_title} · {item.meal_period === "lunch" ? "午餐" : item.meal_period === "dinner" ? "晚餐" : "未標餐別"} · {order.venue_name}</p>
    {order.pickup_address && <p>取餐地址：{order.pickup_address}</p>}
    <p>餐具：{order.needs_utensils ? "需要餐具" : "不需要餐具"}</p>
    <p>{order.items.map((meal) => `${meal.meal_name} × ${meal.quantity}`).join("、")}</p>
    <div className="operation-state"><OrderStatusBadge kind="payment" status={order.payment_status} /><OrderStatusBadge kind="fulfillment" status={order.cancelled_at ? "cancelled" : order.fulfillment_status} label={order.cancelled_at ? "已取消" : fulfillmentLabels[order.fulfillment_status] || "狀態待確認"} /><OrderStatusBadge kind="invoice" status={order.invoice_status} /></div>
    {order.cancellation_request && <p className="meal-order-review-status">{reviewLabels[order.cancellation_request.status]}{order.cancellation_request.status === "pending" && "，訂單尚未取消"}</p>}
    <p>付款方式：{order.payment_method === "cash_on_pickup" ? "合作社現場付款" : "線上付款"}</p>
    <AdminCancelOrder order={order} /><AdminCashPayment order={order} />
    <details><summary>查看餐點、發票與退單明細</summary>
      <ul>{order.items.map((meal, index) => <li key={`${meal.offering_id}-${index}`}>{meal.meal_name} × {meal.quantity} · {formatMoney(meal.subtotal)}{meal.selections.length > 0 && <span>（{meal.selections.map((selection) => `${selection.group_name}：${selection.option_name}`).join("、")}）</span>}</li>)}</ul>
      <InvoiceTaxSummary variant="card" items={order.items} memberTaxExempt={order.member_tax_exempt_snapshot} taxAmount={order.tax_amount} />
      {order.invoice_number && <p>發票號碼：{order.invoice_number}{order.invoice_date && ` · ${formatDateTime(order.invoice_date)}`}</p>}
      {order.cancellation_request && <div className="meal-cancellation-reason"><p>退單原因：{order.cancellation_request.reason}</p><p>申請時間：{formatDateTime(order.cancellation_request.requested_at)}</p>{order.cancellation_request.review_reason && <p>審核理由：{order.cancellation_request.review_reason}</p>}</div>}
      <p>下單時間：{formatDateTime(order.created_at)}</p>
    </details>
    <div className="form-actions">{canPrepare && order.fulfillment_status === "pending" && <button className="button button-system" type="button" disabled={update.isPending} onClick={() => update.mutate("preparing")}>{update.isPending ? "更新中…" : "開始製作"}</button>}{canPrepare && order.fulfillment_status === "preparing" && <button className="button button-system" type="button" disabled={update.isPending} onClick={() => update.mutate("ready_for_pickup")}>{update.isPending ? "更新中…" : "標記待取餐"}</button>}{order.cancellation_request?.status === "pending" && <button className="button button-system" type="button" onClick={() => onReviewOrder(order.order_number)}>審核這筆退單</button>}<Link className="button button-quiet" to="/admin" search={{ q: order.order_number }}>處理付款／發票</Link></div>
    {update.isError && <p className="form-error" role="alert">{update.error.message}</p>}
  </article>;
}
