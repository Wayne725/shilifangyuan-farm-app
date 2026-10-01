import { ArrowLeft, BowlFood, Clock, CreditCard, MapPin, QrCode, Receipt, X } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearch } from "@tanstack/react-router";
import { QRCodeSVG } from "qrcode.react";
import { useEffect, useState } from "react";

import { DataState, LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import { openOrderHandoff } from "../lib/checkout-navigation";
import { apiFetch, formatDateTime, formatMoney } from "../lib/api";
import { createOrderPayment } from "../lib/commerce";
import { formatMealDateTime } from "../lib/meal-time";
import type { MealOrder, MealPickupCredential } from "../lib/types";

export function MealOrdersPage() {
  const { user, openLogin } = useAuth();
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
    mutationFn: (orderId: string) => apiFetch(`/v1/meal-orders/${orderId}/cancel`, { method: "POST", body: JSON.stringify({ reason }) }),
    onSuccess: () => { setCancelOpen(false); queryClient.removeQueries({ queryKey: ["meal-pickup-credential"] }); queryClient.invalidateQueries({ queryKey: ["meal-orders"] }); },
  });

  useEffect(() => {
    if (search.order_id) setSelectedId(search.order_id);
  }, [search.order_id]);

  useEffect(() => {
    if (!selectedId && orders.data?.[0]) setSelectedId(orders.data[0].id);
  }, [orders.data, selectedId]);

  if (!user) return <section className="account-gate"><BowlFood size={45} weight="light" /><p className="eyebrow">MEAL ORDERS</p><h1>登入後查看取餐憑證。</h1><p>付款完成後，QR 與六位取餐碼會安全保存在這裡。</p><button className="button button-primary" type="button" onClick={openLogin}>帳號登入</button></section>;

  return (
    <section className="orders-page meal-orders-page">
      <Link className="text-link" to="/orders" search={{ order_id: undefined, result: undefined, message: undefined, payment: undefined, logistics: undefined }}><ArrowLeft size={17} />一般訂單中心</Link>
      <header className="workspace-heading"><div><p className="eyebrow">MEAL ORDERS / 便當取餐</p><h1>一張憑證，<br />完成現場交付。</h1></div><p>付款狀態、取餐時間與核銷憑證集中顯示。</p></header>
      {orders.isPending && <LoadingLines count={4} />}
      {orders.isError && <DataState kind="error" title="便當訂單無法讀取" detail={orders.error.message} />}
      {orders.data?.length === 0 && <DataState title="目前沒有便當預購" detail="有開放場次時，可從生活消費頁預訂。" />}
      {orders.data && orders.data.length > 0 && <div className="orders-layout"><div className="order-index">{orders.data.map((order) => <button className={order.id === selectedId ? "selected" : ""} key={order.id} type="button" onClick={() => { setSelectedId(order.id); setCancelOpen(false); }}><span>{formatDateTime(order.created_at)}</span><strong className="order-number">{order.order_number}</strong><small>{order.meal_event_title} · {formatMoney(order.amount_total)}</small><i className={`order-state ${order.payment_status}`}>{paymentStatus(order.payment_status)}</i></button>)}</div>{selected && <article className="order-detail meal-order-detail"><div className="order-detail-head"><div><p className="eyebrow">PICKUP CREDENTIAL</p><h2>{selected.meal_event_title}</h2><span className="order-number">{selected.order_number}</span></div><strong>{formatMoney(selected.amount_total)}</strong></div>{credential.data ? <div className="pickup-ticket"><span className="status-chip passed">{["ready", "ready_for_pickup"].includes(selected.fulfillment_status) ? "可取餐" : "憑證已生效"}</span><div className="pickup-qr" aria-label={`訂單 ${selected.order_number} 取餐 QR Code`}><QRCodeSVG value={credential.data.qr_token} size={168} level="H" marginSize={2} /></div><small>六位取餐碼</small><strong>{credential.data.pickup_code}</strong><p>現場出示本頁 QR 或六位取餐碼，核銷後憑證會失效。</p></div> : credentialAvailable ? <div className="credential-pending"><QrCode size={34} weight="light" /><h3>正在查詢取餐憑證</h3><p>{credential.error?.message || "系統正在確認這張憑證是否仍可使用。"}</p><button className="button button-quiet" type="button" onClick={() => credential.refetch()}>重新查詢</button></div> : <MealOrderState order={selected} onPay={() => pay.mutate(selected.id)} paying={pay.isPending} />}
      <MealInvoiceStatus order={selected} />
      <div className="meal-pickup-info">
        <div><MapPin size={20} /><span>取餐地點</span><strong>{selected.venue_name}</strong></div>
        <div><Clock size={20} /><span>預計取餐時間（台灣時間）</span><strong>{selected.pickup_at ? formatMealDateTime(selected.pickup_at) : "舊訂單未指定時間，請於供餐時段內取餐"}</strong></div>
        <div><Clock size={20} /><span>供餐時段</span><strong>{formatMealDateTime(selected.pickup_start)} 至 {formatMealDateTime(selected.pickup_end)}</strong></div>
      </div><div className="order-lines">{selected.items.map((item, index) => <div key={item.offering_id}><span>{String(index + 1).padStart(2, "0")}</span><strong>{item.meal_name}</strong><small>{item.quantity} 份 × {formatMoney(item.unit_price)}{item.selections.length > 0 && <em>{item.selections.map((selection) => `${selection.group_name}：${selection.option_name}`).join(" · ")}</em>}</small><b>{formatMoney(item.subtotal)}</b></div>)}</div>{(pay.isError || cancel.isError) && <p className="form-error">{pay.error?.message || cancel.error?.message}</p>}{selected.available_actions.includes("cancel") && !cancelOpen && <button className="button button-quiet" type="button" onClick={() => setCancelOpen(true)}>取消這筆便當預購</button>}{cancelOpen && <div className="inline-confirm"><button className="icon-button" type="button" aria-label="關閉取消確認" onClick={() => setCancelOpen(false)}><X size={16} /></button><strong>取消後會釋放餐點容量；已付款訂單會建立退款紀錄。</strong><label>取消原因<input value={reason} onChange={(event) => setReason(event.target.value)} /></label><button className="button button-danger" type="button" disabled={!reason.trim() || cancel.isPending} onClick={() => cancel.mutate(selected.id)}>確認取消</button></div>}</article>}</div>}
      <div className="orders-footnote"><BowlFood size={18} weight="light" /><span>一般商品、團購與物流訂單仍保留在原訂單中心。</span><Link className="text-link" to="/shop">查看開放場次</Link></div>
    </section>
  );
}

function MealOrderState({ order, onPay, paying }: { order: MealOrder; onPay: () => void; paying: boolean }) {
  if (order.payment_status === "pending" && order.available_actions.includes("pay")) return <div className="credential-pending"><CreditCard size={34} weight="light" /><h3>付款後顯示取餐憑證</h3><p>付款確認成功後，這裡會顯示 QR 與六位取餐碼。發票狀態會另外更新。</p><button className="button button-primary" type="button" disabled={paying} onClick={onPay}>{paying ? "準備付款頁…" : `前往付款 ${formatMoney(order.amount_total)}`}</button></div>;
  return <div className="credential-pending"><BowlFood size={34} weight="light" /><h3>{fulfillmentStatus(order.fulfillment_status)}</h3><p>為避免重複核銷，這筆訂單目前不顯示取餐憑證。</p></div>;
}

function MealInvoiceStatus({ order }: { order: MealOrder }) {
  const labels: Record<string, string> = { not_eligible: "尚未開立", pending: "開立處理中", issued: "已開立", failed: "開立待處理", void_pending: "作廢處理中", voided: "已作廢" };
  return <section className="invoice-summary-card" aria-label="電子發票"><Receipt size={27} weight="light" /><div><small>電子發票</small><strong>{labels[order.invoice_status] || "狀態確認中"}</strong>{["pending", "failed"].includes(order.invoice_status) && <span>發票正由系統處理，不影響已付款訂單的取餐資格；請勿為了發票重新付款。</span>}</div><dl>{order.invoice_number && <div><dt>發票號碼</dt><dd className="order-number">{order.invoice_number}</dd></div>}{order.invoice_date && <div><dt>開立時間</dt><dd>{formatDateTime(order.invoice_date)}</dd></div>}</dl></section>;
}

function paymentStatus(status: string) { return { pending: "待付款", paid: "已付款", refunded: "已退款", refund_pending: "退款中", expired: "已逾期", failed: "付款失敗" }[status] || status; }
function fulfillmentStatus(status: string) { return { pending: "取餐憑證尚未生效", picked_up: "已完成取餐", no_show: "逾時未取", cancelled: "訂單已取消", ready: "取餐憑證目前無法使用", ready_for_pickup: "取餐憑證目前無法使用" }[status] || status; }
