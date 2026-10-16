import { ArrowLeft, BowlFood, Clock, CreditCard, MapPin, QrCode, Receipt, X } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearch } from "@tanstack/react-router";
import { QRCodeSVG } from "qrcode.react";
import { useEffect, useState } from "react";

import { DataState, LoadingLines } from "../components/Shared";
import { InvoiceTaxSummary } from "../components/InvoiceTaxSummary";
import { useAuth } from "../context/AuthContext";
import { openOrderHandoff } from "../lib/checkout-navigation";
import { apiFetch, formatDateTime, formatMoney } from "../lib/api";
import { createOrderPayment } from "../lib/commerce";
import { taxTypeLabel } from "../lib/invoice-tax";
import { formatMealDateTime } from "../lib/meal-time";
import type { MealOrder, MealPickupCredential } from "../lib/types";

export function MealOrdersPage() {
  const { user, openLogin, isAuthReady } = useAuth();
  const search = useSearch({ from: "/meal-orders" });
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState(search.order_id || "");
  const [cancelOpen, setCancelOpen] = useState(false);
  const [reason, setReason] = useState("行程變更");
  const orders = useQuery({
    queryKey: ["meal-orders", user?.id],
    queryFn: () => apiFetch<MealOrder[]>("/v1/meal-orders"),
    enabled: Boolean(user),
    refetchInterval: 5000,
  });
  const selected = orders.data?.find((order) => order.id === selectedId);
  const credentialAvailable = Boolean(selected?.payment_status === "paid" && !selected.cancelled_at && selected.pickup_code && !["picked_up", "no_show", "cancelled"].includes(selected.fulfillment_status));
  const credentialQuery = useQuery({
    queryKey: ["meal-pickup-credential", user?.id, selectedId, selected?.payment_status, selected?.fulfillment_status],
    queryFn: () => apiFetch<MealPickupCredential>(`/v1/meal-orders/${selectedId}/pickup-credential`),
    enabled: Boolean(selectedId && credentialAvailable),
    refetchInterval: credentialAvailable ? 5000 : false,
    retry: false,
  });
  const credential = { ...credentialQuery, data: credentialAvailable && !credentialQuery.isError ? credentialQuery.data : undefined };
  const pay = useMutation({ mutationFn: createOrderPayment, onSuccess: (url, orderId) => openOrderHandoff(url, orderId) });
  const cancel = useMutation({
    mutationFn: (orderId: string) => apiFetch(`/v1/meal-orders/${orderId}/cancellation-requests`, { method: "POST", body: JSON.stringify({ reason }) }),
    onSuccess: () => { setCancelOpen(false); queryClient.removeQueries({ queryKey: ["meal-pickup-credential"] }); queryClient.invalidateQueries({ queryKey: ["meal-orders"] }); },
  });

  useEffect(() => {
    if (search.order_id) setSelectedId(search.order_id);
  }, [search.order_id]);

  useEffect(() => {
    if (!selectedId && orders.data?.[0]) setSelectedId(orders.data[0].id);
  }, [orders.data, selectedId]);

  if (!isAuthReady) return <section className="orders-page meal-orders-page" aria-label="正在恢復登入"><LoadingLines count={3} /></section>;
  if (!user) return <section className="account-gate meal-orders-page"><BowlFood size={45} weight="light" /><h1>我的便當訂單</h1><p>登入後可查看餐點明細、付款狀態與取餐碼。</p><button className="button button-primary" type="button" onClick={openLogin}>登入查看訂單</button><Link className="text-link" to="/shop">先查看明天的餐點</Link></section>;

  return (
    <section className="orders-page meal-orders-page">
      <Link className="text-link" to="/shop"><ArrowLeft size={17} />回到便當預訂</Link>
      <header className="workspace-heading"><div><h1>我的便當訂單</h1><p>選一筆訂單，查看餐點明細、取餐時間及取餐碼。</p></div></header>
      {orders.isPending && <LoadingLines count={4} />}
      {orders.isError && <DataState kind="error" title="便當訂單無法讀取" detail={orders.error.message} onAction={() => orders.refetch()} />}
      {orders.data?.length === 0 && <DataState title="目前沒有便當訂單" detail="可先查看明天的餐點，選餐並完成付款後，訂單會出現在這裡。" actionLabel="我要訂餐" onAction={() => window.location.assign("/shop")} />}
      {orders.data && orders.data.length > 0 && <div className="orders-layout"><div className="order-index" aria-label="選擇便當訂單">{orders.data.map((order) => <button className={order.id === selectedId ? "selected" : ""} aria-pressed={order.id === selectedId} key={order.id} type="button" onClick={() => { setSelectedId(order.id); setCancelOpen(false); cancel.reset(); setReason("行程變更"); }}><span>取餐 {formatMealDateTime(order.pickup_at || order.pickup_start)}</span><strong>{order.meal_event_title}</strong><small className="order-number">訂單編號：{order.order_number}</small><small>{formatMoney(order.amount_total)}</small><i className={`order-state ${order.payment_status}`}>{paymentStatus(order.payment_status)}</i></button>)}</div>{selected ? <article className="order-detail meal-order-detail"><div className="order-detail-head"><div><h2>{selected.meal_event_title}</h2><span className="order-number">訂單編號：{selected.order_number}</span></div><strong>{formatMoney(selected.amount_total)}</strong></div>
      <div className="meal-pickup-info">
        <div><MapPin size={20} /><span>取餐地點</span><strong>{selected.venue_name}</strong></div>
        <div><Clock size={20} /><span>預計取餐時間（台灣時間）</span><strong>{selected.pickup_at ? formatMealDateTime(selected.pickup_at) : "舊訂單未指定時間，請於供餐時段內取餐"}</strong></div>
      </div>
      <section className="meal-order-lines" aria-label="訂單餐點明細"><h3>餐點明細</h3><div className="order-lines">{selected.items.map((item, index) => <div key={item.offering_id}><span>{String(index + 1).padStart(2, "0")}</span><strong>{item.meal_name}</strong><small>{item.quantity} 份 × {formatMoney(item.unit_price)} · {taxTypeLabel(item.tax_type)}{item.selections.length > 0 && <em>{item.selections.map((selection) => `${selection.group_name}：${selection.option_name}`).join(" · ")}</em>}</small><b>{formatMoney(item.subtotal)}</b></div>)}</div></section>
      {selected.promotion && <p className="meal-order-discount">{selected.promotion.name} · 已折抵 {formatMoney(selected.promotion.discount)}</p>}
      <p className="meal-order-payment-status" aria-live="polite">付款狀態：<strong>{paymentStatus(selected.payment_status)}</strong></p>
      <MealCancellationStatus order={selected} />
      {credential.data ? <div className="pickup-ticket"><span className="status-chip passed">{["ready", "ready_for_pickup"].includes(selected.fulfillment_status) ? "可取餐" : "取餐碼已生效"}</span><div className="pickup-qr" aria-label={`訂單 ${selected.order_number} 取餐 QR Code`}><QRCodeSVG value={credential.data.qr_token} size={168} level="H" marginSize={2} /></div><small>六位取餐碼</small><strong>{credential.data.pickup_code}</strong><p>到現場出示 QR 或告知六位取餐碼；取餐完成後，這組取餐碼會失效。</p></div> : credentialAvailable ? <div className="credential-pending"><QrCode size={34} weight="light" /><h3>{credential.isError ? "取餐碼暫時無法讀取" : "正在查詢取餐碼"}</h3><p>{credential.error?.message || "系統正在確認這組取餐碼是否仍可使用。"}</p><button className="button button-quiet" type="button" onClick={() => credential.refetch()}>重新查詢</button></div> : <MealOrderState order={selected} onPay={() => pay.mutate(selected.id)} paying={pay.isPending} />}
      <MealInvoiceStatus order={selected} />
      <InvoiceTaxSummary variant="card" items={selected.items} memberTaxExempt={selected.member_tax_exempt_snapshot} taxAmount={selected.tax_amount} />
      <p className="meal-order-created-at">下單時間：{formatDateTime(selected.created_at)}</p>
      {(pay.isError || cancel.isError) && <p className="form-error">{pay.error?.message || cancel.error?.message}</p>}{selected.available_actions.includes("request_cancellation") && !cancelOpen && <button className="button button-quiet" type="button" onClick={() => setCancelOpen(true)}>申請取消這筆便當</button>}{cancelOpen && <div className="inline-confirm"><button className="icon-button" type="button" aria-label="關閉退單申請" onClick={() => setCancelOpen(false)}><X size={20} /></button><strong>送出後由管理員審核；核准前訂單仍有效，不會立即退款或作廢發票。</strong><label>退單原因<textarea required maxLength={2000} rows={3} value={reason} onChange={(event) => setReason(event.target.value)} /></label><button className="button button-danger" type="button" disabled={!reason.trim() || cancel.isPending} onClick={() => cancel.mutate(selected.id)}>{cancel.isPending ? "送出申請中…" : "送出退單申請"}</button></div>}</article> : <DataState title="找不到這筆便當訂單" detail="請從左方或上方的訂單清單選擇一筆訂單。" />}</div>}
      <div className="orders-footnote"><Link className="button button-quiet" to="/shop">我要訂餐</Link></div>
    </section>
  );
}

function MealOrderState({ order, onPay, paying }: { order: MealOrder; onPay: () => void; paying: boolean }) {
  if (order.payment_status === "pending" && order.available_actions.includes("pay")) return <div className="credential-pending"><CreditCard size={34} weight="light" /><h3>付款後顯示取餐憑證</h3><p>付款確認成功後，這裡會顯示 QR 與六位取餐碼。發票狀態會另外更新。</p><button className="button button-primary" type="button" disabled={paying} onClick={onPay}>{paying ? "準備付款頁…" : `前往付款 ${formatMoney(order.amount_total)}`}</button></div>;
  return <div className="credential-pending"><BowlFood size={34} weight="light" /><h3>{fulfillmentStatus(order.fulfillment_status)}</h3><p>為避免重複核銷，這筆訂單目前不顯示取餐憑證。</p></div>;
}

function MealCancellationStatus({ order }: { order: MealOrder }) {
  const request = order.cancellation_request;
  if (!request) return null;
  const labels = { pending: "退單申請待審核", approved: "退單申請已核准", rejected: "退單申請未核准" };
  return <section className="meal-cancellation-card" aria-label="退單審核進度">
    <h3>{labels[request.status]}</h3>
    <p>申請原因：{request.reason}</p>
    <p>申請時間：{formatDateTime(request.requested_at)}</p>
    {request.review_reason && <p>管理員回覆：{request.review_reason}</p>}
    {request.reviewed_at && <p>審核時間：{formatDateTime(request.reviewed_at)}</p>}
    <p>{request.status === "pending" ? "申請尚未核准，訂單仍依原安排處理。"
      : request.status === "approved" ? "訂單已取消；退款與發票進度請查看本頁各自的狀態。"
      : order.available_actions.includes("request_cancellation") ? "原訂單仍依目前狀態處理；若需要補充理由，可再提出申請。" : "原訂單仍依目前狀態處理；需要協助請洽管理員。"}</p>
  </section>;
}

function MealInvoiceStatus({ order }: { order: MealOrder }) {
  const labels: Record<string, string> = { not_eligible: "尚未開立", pending: "開立處理中", issued: "已開立", failed: "開立待處理", void_pending: "作廢處理中", voided: "已作廢" };
  return <section className="invoice-summary-card" aria-label="電子發票"><Receipt size={27} weight="light" /><div><small>電子發票</small><strong>{labels[order.invoice_status] || "狀態確認中"}</strong>{["pending", "failed"].includes(order.invoice_status) && <span>發票正由系統處理，不影響已付款訂單的取餐資格；請勿為了發票重新付款。</span>}</div><dl>{order.invoice_number && <div><dt>發票號碼</dt><dd className="order-number">{order.invoice_number}</dd></div>}{order.invoice_date && <div><dt>開立時間</dt><dd>{formatDateTime(order.invoice_date)}</dd></div>}</dl></section>;
}

function paymentStatus(status: string) { return { pending: "待付款", paid: "已付款", refunded: "已退款", refund_pending: "退款中", late_paid_refund_required: "付款晚到，退款處理中", expired: "已逾期", failed: "付款失敗" }[status] || status; }
function fulfillmentStatus(status: string) { return { pending: "取餐憑證尚未生效", preparing: "餐點製作中", picked_up: "已完成取餐", no_show: "逾時未取", cancelled: "訂單已取消", ready: "取餐憑證目前無法使用", ready_for_pickup: "取餐憑證目前無法使用" }[status] || status; }
