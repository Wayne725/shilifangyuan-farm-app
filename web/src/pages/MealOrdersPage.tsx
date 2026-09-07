import { ArrowLeft, BowlFood, Clock, CreditCard, MapPin, QrCode, X } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { QRCodeSVG } from "qrcode.react";
import { useEffect, useState } from "react";

import { DataState, LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import { openOrderHandoff } from "../lib/checkout-navigation";
import { apiFetch, formatDateTime, formatMoney } from "../lib/api";
import { createOrderPayment } from "../lib/commerce";
import type { MealOrder, MealPickupCredential } from "../lib/types";

export function MealOrdersPage() {
  const { user, openLogin } = useAuth();
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState("");
  const [cancelOpen, setCancelOpen] = useState(false);
  const [reason, setReason] = useState("行程變更");
  const orders = useQuery({
    queryKey: ["meal-orders", user?.id],
    queryFn: () => apiFetch<MealOrder[]>("/v1/meal-orders"),
    enabled: Boolean(user),
    refetchInterval: 5000,
  });
  const selected = orders.data?.find((order) => order.id === selectedId);
  const credential = useQuery({
    queryKey: ["meal-pickup-credential", selectedId],
    queryFn: () => apiFetch<MealPickupCredential>(`/v1/meal-orders/${selectedId}/pickup-credential`),
    enabled: Boolean(selectedId && selected?.payment_status === "paid" && !["picked_up", "no_show", "cancelled"].includes(selected.fulfillment_status)),
    retry: false,
  });
  const pay = useMutation({ mutationFn: createOrderPayment, onSuccess: (url, orderId) => openOrderHandoff(url, orderId) });
  const cancel = useMutation({
    mutationFn: (orderId: string) => apiFetch(`/v1/meal-orders/${orderId}/cancel`, { method: "POST", body: JSON.stringify({ reason }) }),
    onSuccess: () => { setCancelOpen(false); queryClient.invalidateQueries({ queryKey: ["meal-orders"] }); },
  });

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
      {orders.data && orders.data.length > 0 && <div className="orders-layout"><div className="order-index">{orders.data.map((order) => <button className={order.id === selectedId ? "selected" : ""} key={order.id} type="button" onClick={() => { setSelectedId(order.id); setCancelOpen(false); }}><span>{formatDateTime(order.created_at)}</span><strong className="order-number">{order.order_number}</strong><small>{order.meal_event_title} · {formatMoney(order.amount_total)}</small><i className={`order-state ${order.payment_status}`}>{paymentStatus(order.payment_status)}</i></button>)}</div>{selected && <article className="order-detail meal-order-detail"><div className="order-detail-head"><div><p className="eyebrow">PICKUP CREDENTIAL</p><h2>{selected.meal_event_title}</h2><span className="order-number">{selected.order_number}</span></div><strong>{formatMoney(selected.amount_total)}</strong></div>{credential.data ? <div className="pickup-ticket"><span className="status-chip passed">{selected.fulfillment_status === "ready_for_pickup" ? "可取餐" : "憑證已生效"}</span><div className="pickup-qr" aria-label={`訂單 ${selected.order_number} 取餐 QR Code`}><QRCodeSVG value={credential.data.qr_token} size={168} level="H" marginSize={2} /></div><small>六位取餐碼</small><strong>{credential.data.pickup_code}</strong><p>現場出示本頁 QR 或六位取餐碼，核銷後憑證會失效。</p></div> : selected.payment_status === "paid" && !["picked_up", "no_show", "cancelled"].includes(selected.fulfillment_status) ? <div className="credential-pending"><QrCode size={34} weight="light" /><h3>取餐憑證建立中</h3><p>{credential.error?.message || "系統正在確認付款並產生安全憑證。"}</p><button className="button button-quiet" type="button" onClick={() => credential.refetch()}>重新查詢</button></div> : <MealOrderState order={selected} onPay={() => pay.mutate(selected.id)} paying={pay.isPending} />}
      <div className="meal-pickup-info"><div><MapPin size={20} /><span>取餐地點</span><strong>{selected.venue_name}</strong></div><div><Clock size={20} /><span>取餐時間</span><strong>{formatDateTime(selected.pickup_start)} 至 {formatDateTime(selected.pickup_end)}</strong></div></div><div className="order-lines">{selected.items.map((item, index) => <div key={item.offering_id}><span>{String(index + 1).padStart(2, "0")}</span><strong>{item.meal_name}</strong><small>{item.quantity} 份 × {formatMoney(item.unit_price)}{item.selections.length > 0 && <em>{item.selections.map((selection) => `${selection.group_name}：${selection.option_name}`).join(" · ")}</em>}</small><b>{formatMoney(item.subtotal)}</b></div>)}</div>{(pay.isError || cancel.isError) && <p className="form-error">{pay.error?.message || cancel.error?.message}</p>}{selected.available_actions.includes("cancel") && !cancelOpen && <button className="button button-quiet" type="button" onClick={() => setCancelOpen(true)}>取消這筆便當預購</button>}{cancelOpen && <div className="inline-confirm"><button className="icon-button" type="button" aria-label="關閉取消確認" onClick={() => setCancelOpen(false)}><X size={16} /></button><strong>取消後會釋放餐點容量；已付款訂單會建立退款紀錄。</strong><label>取消原因<input value={reason} onChange={(event) => setReason(event.target.value)} /></label><button className="button button-danger" type="button" disabled={!reason.trim() || cancel.isPending} onClick={() => cancel.mutate(selected.id)}>確認取消</button></div>}</article>}</div>}
      <div className="orders-footnote"><BowlFood size={18} weight="light" /><span>一般商品、團購與物流訂單仍保留在原訂單中心。</span><Link className="text-link" to="/shop">查看開放場次</Link></div>
    </section>
  );
}

function MealOrderState({ order, onPay, paying }: { order: MealOrder; onPay: () => void; paying: boolean }) {
  if (order.payment_status === "pending") return <div className="credential-pending"><CreditCard size={34} weight="light" /><h3>付款後顯示取餐憑證</h3><p>完成測試金流後，系統會產生 QR 與六位取餐碼。</p><button className="button button-primary" type="button" disabled={paying} onClick={onPay}>{paying ? "準備付款頁…" : `前往付款 ${formatMoney(order.amount_total)}`}</button></div>;
  return <div className="credential-pending"><BowlFood size={34} weight="light" /><h3>{fulfillmentStatus(order.fulfillment_status)}</h3><p>為避免重複核銷，這筆訂單目前不顯示取餐憑證。</p></div>;
}

function paymentStatus(status: string) { return { pending: "待付款", paid: "已付款", refunded: "已退款", refund_pending: "退款中", expired: "已逾期", failed: "付款失敗" }[status] || status; }
function fulfillmentStatus(status: string) { return { picked_up: "已完成取餐", no_show: "逾時未取", cancelled: "訂單已取消", ready_for_pickup: "可取餐" }[status] || status; }
