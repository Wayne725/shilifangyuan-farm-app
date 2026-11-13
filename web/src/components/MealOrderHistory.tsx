import { OrderQuantityAmendment } from "./OrderQuantityAmendment";
import { BusinessMealPaymentStatus } from "./BusinessMealPaymentStatus";
import { CancellationReviewDetails } from "./CancellationReviewDetails";
import { ArrowLeft, BowlFood, Clock, MapPin, Money, Receipt } from "@phosphor-icons/react";
import { useMutation } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import { OrderSearch } from "../components/OrderSearch";
import { useOrderHistory } from "../lib/use-order-history";
import { DataState, LoadingLines } from "../components/Shared";
import { InvoiceTaxSummary } from "../components/InvoiceTaxSummary";
import { OrderCancellationContact } from "./OrderCancellationContact";
import { useAuth } from "../context/AuthContext";
import { openOrderHandoff } from "../lib/checkout-navigation";
import { formatDateTime, formatMoney } from "../lib/api";
import { createOrderPayment } from "../lib/commerce";
import { taxTypeLabel } from "../lib/invoice-tax";
import { sessionStore } from "../lib/browser-storage";
import { formatMealDate, formatMealDateTime } from "../lib/meal-time";
import type { MealOrder } from "../lib/types";

export function MealOrderHistory({ orderId, embedded = false }: { orderId?: string; embedded?: boolean }) {
  const { user, openLogin, isAuthReady } = useAuth();
  const [query, setQuery] = useState("");
  const [payment, setPayment] = useState("");
  const [selectedId, setSelectedId] = useState(orderId || "");
  const orders = useOrderHistory<MealOrder>("meal", user?.id, query, payment, selectedId, orderId);
  const visibleOrders = orders.items;
  const selected = visibleOrders.find((order) => order.id === selectedId) || visibleOrders[0];
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 15000);
    return () => window.clearInterval(timer);
  }, []);
  const paymentIssue = user && selected && selected.payment_status !== "paid"
    ? sessionStore.getItem(`slf_meal_payment_issue:${user.id}:${selected.id}`) : null;
  const pay = useMutation({ mutationFn: createOrderPayment, onSuccess: (url, orderId) => {
    if (user) sessionStore.removeItem(`slf_meal_payment_issue:${user.id}:${orderId}`);
    openOrderHandoff(url, orderId);
  } });


  useEffect(() => {
    if (orderId) setSelectedId(orderId);
  }, [orderId]);

  useEffect(() => {
    if (!selectedId && orders.items[0]) setSelectedId(orders.items[0].id);
  }, [orders.items, selectedId]);


  if (!isAuthReady) return <section className="orders-page meal-orders-page" aria-label="正在恢復登入"><LoadingLines count={3} /></section>;
  if (!user) return <section className="account-gate meal-orders-page"><BowlFood size={45} weight="light" /><h1>我的便當訂單</h1><p>登入後可查看餐點明細及付款狀態。</p><button className="button button-primary" type="button" onClick={openLogin}>登入查看訂單</button><Link className="text-link" to="/shop" search={{ tab: "meals" }}>先查看可預訂餐點</Link></section>;

  return (
    <section className={embedded ? "meal-orders-embedded" : "orders-page meal-orders-page"}>
      {!embedded && <><Link className="text-link" to="/shop" search={{ tab: "meals" }}><ArrowLeft size={17} />回到便當預訂</Link>
      <header className="workspace-heading"><div><h1>餐點訂單</h1></div><Link className="text-link" to="/orders">訂單中心</Link></header></>}
      <OrderSearch query={query} payment={payment} onQuery={setQuery} onPayment={setPayment} count={visibleOrders.length} searching={orders.searching} refreshing={orders.isFetching} onRefresh={orders.refresh} />
      {orders.isPending && <LoadingLines count={4} />}
      {orders.isError && visibleOrders.length === 0 && <DataState kind="error" title="便當訂單無法讀取" detail={orders.error.message} onAction={orders.refresh} />}
      {orders.targetPending && <p role="status">正在讀取返回的原餐點訂單…</p>}
      {orders.targetError && <DataState kind="error" title="原餐點訂單暫時無法讀取" detail={orders.targetError.message} onAction={orders.refresh} />}
      {orders.selectedError && <p className="form-error" role="alert">餐點訂單更新失敗：{orders.selectedError.message}</p>}
      {orders.isSuccess && visibleOrders.length === 0 && (query || payment
        ? <DataState title="沒有符合條件的餐點訂單" detail="請調整關鍵字或付款狀態。" />
        : <DataState title="目前沒有便當訂單" detail="可先查看可預訂餐點，選餐並完成付款後，訂單會出現在這裡。" actionLabel="我要訂餐" onAction={() => window.location.assign("/shop")} />)}
      {visibleOrders.length > 0 && <div className="orders-layout"><div className="order-index" aria-label="選擇便當訂單">{visibleOrders.map((order) => <button className={order.id === selected?.id ? "selected" : ""} aria-pressed={order.id === selected?.id} key={order.id} type="button" onClick={() => setSelectedId(order.id)}><span>{order.venue_name}</span><strong>{formatMealDate(order.pickup_at || order.pickup_start)}</strong><small className="order-number">訂單編號：{order.order_number}</small><small>{formatMoney(order.amount_total)}</small><i className={`order-state ${order.payment_status}`}>{paymentStatus(order.payment_status)}</i></button>)}</div>{selected ? <article className="order-detail meal-order-detail"><div className="order-detail-head"><div><h2>{formatMealDate(selected.pickup_at || selected.pickup_start)}</h2><span className="order-number">訂單編號：{selected.order_number}</span></div><strong>{formatMoney(selected.amount_total)}</strong></div>
      <div className="meal-pickup-info">
        <div><MapPin size={20} /><span>取餐點</span><strong>{selected.venue_name}{selected.pickup_address && <small>{selected.pickup_address}</small>}</strong></div>
        <div><Clock size={20} /><span>預計取餐時間（台灣時間）</span><strong>{selected.pickup_at ? formatMealDateTime(selected.pickup_at) : "舊訂單未指定時間，請於供餐時段內取餐"}</strong></div>
      </div>
      <p className="meal-kitchen-note"><strong>餐具：</strong>{selected.needs_utensils ? "需要餐具" : "不需要餐具"}</p>
      <section className="meal-order-lines" aria-label="訂單餐點明細"><h3>餐點明細</h3><div className="order-lines">{selected.items.map((item, index) => <div key={item.order_item_id || `${item.offering_id}-${index}`}><span>{String(index + 1).padStart(2, "0")}</span><strong>{item.meal_name}</strong><small>{item.quantity} 份 × {formatMoney(item.unit_price)} · {taxTypeLabel(item.tax_type)}{item.selections.length > 0 && <em>{item.selections.map((selection) => `${selection.group_name}：${selection.option_name}`).join(" · ")}</em>}</small><b>{formatMoney(item.subtotal)}</b></div>)}</div></section>
      {selected.kitchen_note && <p className="meal-kitchen-note"><strong>餐點備註：</strong>{selected.kitchen_note}</p>}
      {selected.promotion && <p className="meal-order-discount">{selected.promotion.name} · 已折抵 {formatMoney(selected.promotion.discount)}</p>}
      {selected.meal_booking_type === "business" && <section aria-label="會餐發票資料">
        <span className="status-chip">會餐 · 統編報帳</span>
        {selected.invoice_buyer_tax_id && <p>統編：{selected.invoice_buyer_tax_id}</p>}
        {selected.invoice_buyer_name && <p>抬頭：{selected.invoice_buyer_name}</p>}
      </section>}
      <p className="meal-order-payment-status" aria-live="polite">付款狀態：<strong>{paymentStatus(selected.payment_status)}</strong></p>
      <OrderQuantityAmendment key={selected.id} order={selected} disabled={pay.isPending} />
      <MealCancellationStatus order={selected} />
      {paymentIssue && <p className="form-error" role="alert">無法開啟付款頁：{paymentIssue}</p>}
      <MealOrderState order={selected} ended={now >= Date.parse(selected.pickup_end)} onPay={() => pay.mutate(selected.id)} paying={pay.isPending} />
      <MealInvoiceStatus order={selected} />
      <InvoiceTaxSummary variant="card" items={selected.items} memberTaxExempt={selected.member_tax_exempt_snapshot} taxAmount={selected.tax_amount} invoiceTax={selected.invoice_tax} invoiceStatus={selected.invoice_status} />
      <p className="meal-order-created-at">下單時間：{formatDateTime(selected.created_at)}</p>
      {pay.isError && <p className="form-error">{pay.error.message}</p>}
      {!["expired", "failed"].includes(selected.payment_status) && <OrderCancellationContact />}
      </article> : <DataState title="找不到這筆便當訂單" detail="請從左方或上方的訂單清單選擇一筆訂單。" />}</div>}
      {orders.isFetchNextPageError && <p className="form-error" role="alert">更多餐點訂單暫時無法讀取：{orders.error.message}</p>}
      {orders.hasNextPage && <button className="button button-quiet" type="button" disabled={orders.isFetching} onClick={() => void orders.fetchNextPage()}>{orders.isFetchingNextPage ? "載入更多中…" : orders.isFetchNextPageError ? "重試載入更多" : "載入更多訂單"}</button>}
      <div className="orders-footnote"><Link className="button button-quiet" to="/shop" search={{ tab: "meals" }}>我要訂餐</Link></div>
    </section>
  );
}

function MealOrderState({ order, ended, onPay, paying }: { order: MealOrder; ended: boolean; onPay: () => void; paying: boolean }) {
  if (order.cancelled_at || order.fulfillment_status === "cancelled") return <p>訂單已取消</p>;
  if (order.meal_booking_type === "business" && (order.payment_method === "cash_on_pickup" || ["pending", "failed", "expired"].includes(order.payment_status))) {
    return <>{ended && <p>取餐時段已結束</p>}<BusinessMealPaymentStatus order={order} onPay={onPay} paying={paying} /></>;
  }
  if (ended) return <p>取餐時段已結束</p>;
  if (order.payment_method === "cash_on_pickup" && order.payment_status === "pending") {
    return <div className="credential-pending cash-payment-pending"><Money size={34} weight="light" /><h3>預訂成功・待現場付款</h3><p>應付金額 {formatMoney(order.amount_total)}。</p></div>;
  }
  if (order.available_actions.includes("pay")) {
    return <button className="button button-primary" type="button" disabled={paying} onClick={onPay}>
      {paying ? "準備付款頁…" : `前往付款 ${formatMoney(order.amount_total)}`}
    </button>;
  }
  return null;
}

function MealCancellationStatus({ order }: { order: MealOrder }) {
  const request = order.cancellation_request;
  if (!request || (request.status === "pending" && ["expired", "failed"].includes(order.payment_status))) return null;
  const labels = { pending: "退單申請待審核", approved: "退單申請已核准", rejected: "退單申請未核准" };
  return <section className="meal-cancellation-card" aria-label="退單審核進度">
    <h3>{labels[request.status]}</h3>
    {request.reason && <p>{request.initiated_by_admin ? "退單原因" : "申請原因"}：{request.reason}</p>}
    <p>申請時間：{formatDateTime(request.requested_at)}</p>
    {request.review_reason && !request.initiated_by_admin && <p>管理員回覆：{request.review_reason}</p>}
    <CancellationReviewDetails request={request} />
    <p>{request.status === "pending" ? "申請尚未核准，訂單仍依原安排處理。"
      : request.status === "approved" ? "訂單已取消；退款與發票進度請查看本頁各自的狀態。"
      : "原訂單仍依目前狀態處理。"}</p>
  </section>;
}

function MealInvoiceStatus({ order }: { order: MealOrder }) {
  const labels: Record<string, string> = { external: "合作社現場開票", not_eligible: "尚未開立", pending: "開立處理中", issued: "已開立", failed: "開立待處理", void_pending: "作廢處理中", voided: "已作廢" };
  return <section className="invoice-summary-card" aria-label="電子發票"><Receipt size={27} weight="light" /><div><small>電子發票</small><strong>{order.invoice_followup_required ? "發票待合作社處理" : labels[order.invoice_status] || "狀態確認中"}</strong>{order.invoice_followup_required ? <span>請聯絡合作社核對取餐日發票。</span> : ["pending", "failed"].includes(order.invoice_status) && <span>發票正由系統處理，不影響已付款訂單的取餐資格；請勿為了發票重新付款。</span>}</div><dl>{order.invoice_number && <div><dt>發票號碼</dt><dd className="order-number">{order.invoice_number}</dd></div>}{order.invoice_date && <div><dt>開立時間</dt><dd>{formatDateTime(order.invoice_date)}</dd></div>}</dl></section>;
}

function paymentStatus(status: string) { return { pending: "待付款", paid: "已付款", refunded: "已退款", refund_pending: "退款中", late_paid_refund_required: "付款晚到，退款處理中", expired: "付款逾期", failed: "付款失敗" }[status] || status; }
