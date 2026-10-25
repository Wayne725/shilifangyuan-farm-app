import { campaignTimestamp } from "../lib/group-campaigns";
import { useDeadlineClock } from "../lib/use-deadline-clock";
import { OrderQuantityAmendment } from "../components/OrderQuantityAmendment";
import {
  ArrowClockwise,
  Basket,
  CheckCircle,
  Clock,
  CreditCard,
  FileText,
  MapPin,
  Package,
  Truck,
} from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";

import * as Tabs from "@radix-ui/react-tabs";
import { MealOrderHistory } from "../components/MealOrderHistory";
import { OrderSearch } from "../components/OrderSearch";
import { filterOrders } from "../lib/order-search";
import { DataState, LoadingLines } from "../components/Shared";
import { InvoiceTaxSummary } from "../components/InvoiceTaxSummary";
import { OrderCancellationContact } from "../components/OrderCancellationContact";
import { useAuth } from "../context/AuthContext";
import { openOrderHandoff } from "../lib/checkout-navigation";
import { ApiError, apiFetch, formatDate, formatMoney } from "../lib/api";
import { taxTypeLabel } from "../lib/invoice-tax";
import {
  createOrderPayment,
  refreshPaymentAttempt,
  reissueLogisticsSelection,
  shippingChannelLabels,
  temperatureLabels,
} from "../lib/commerce";
import {
  fulfillmentStatusLabel,
  invoiceStatusLabel,
  paymentStatusLabel,
} from "../lib/labels";
import type { MealOrder, Order, PaymentAttemptStatus } from "../lib/types";

const shipmentLabels: Record<string, string> = {
  draft: "待選擇物流",
  selection_pending: "待選門市／地址",
  ready_to_create: "物流已確認",
  created: "已建立物流單",
  in_transit: "配送中",
  delivered: "已送達",
  exception: "物流異常",
  cancelled: "物流已取消",
};

export function OrdersPage() {
  const { user, isAuthReady, openLogin } = useAuth();
  const search = useSearch({ from: "/orders" });
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const confirmationStartedAt = useRef(Date.now());
  const [orderType, setOrderType] = useState(search.order_id ? "regular" : "meal");
  const [query, setQuery] = useState("");
  const [payment, setPayment] = useState("");
  const [selectedId, setSelectedId] = useState(search.order_id || "");
  const orders = useQuery({
    queryKey: ["orders", user?.id],
    queryFn: () => apiFetch<Order[]>("/v1/orders"),
    enabled: Boolean(user),
    refetchInterval: 5000,
  });
  const paymentConfirmation = useQuery({
    queryKey: ["payment-attempt-refresh", search.attempt_id],
    queryFn: () => refreshPaymentAttempt(search.attempt_id || ""),
    enabled: Boolean(
      user && search.payment === "confirming" && search.attempt_id,
    ),
    refetchInterval: (query) => {
      const status = (query.state.data as PaymentAttemptStatus | undefined)
        ?.status;
      const elapsed = Date.now() - confirmationStartedAt.current;
      if (status && status !== "pending" && status !== "confirming") return false;
      if (elapsed >= 120_000) return false;
      return elapsed < 30_000 ? 5000 : 15_000;
    },
    refetchIntervalInBackground: false,
  });
  const refreshedPaymentStatus = paymentConfirmation.data?.status;
  const openPayment = useMutation({
    mutationFn: createOrderPayment,
    onSuccess: (url, orderId) => openOrderHandoff(url, orderId),
  });
  const reopenLogistics = useMutation({
    mutationFn: reissueLogisticsSelection,
    onSuccess: (url, orderId) => openOrderHandoff(url, orderId),
  });
  useEffect(() => {
    confirmationStartedAt.current = Date.now();
  }, [search.attempt_id]);

  useEffect(() => {
    if (search.order_id) { setSelectedId(search.order_id); setOrderType("regular"); }
  }, [search.order_id]);

  useEffect(() => {
    if (!selectedId && orders.data?.[0]) setSelectedId(orders.data[0].id);
  }, [orders.data, selectedId]);

  useEffect(() => {
    if (
      refreshedPaymentStatus
      && !["pending", "confirming"].includes(refreshedPaymentStatus)
    ) {
      queryClient.invalidateQueries({ queryKey: ["orders"] });
    }
  }, [queryClient, refreshedPaymentStatus]);

  const generalOrders = (orders.data || []).filter((order) => order.sales_channel !== "meal_preorder");
  const visibleOrders = filterOrders(generalOrders, query, payment);
  const selected = visibleOrders.find((order) => order.id === selectedId) || visibleOrders[0];
  const now = useDeadlineClock(selected?.group_campaign_deadline);
  const groupPaymentClosed = Boolean(selected?.group_campaign_deadline && campaignTimestamp(selected.group_campaign_deadline) <= now);
  const returnedOrder = orders.data?.find((order) => order.id === search.order_id);
  const returnedMeal = useQuery({
    queryKey: ["returned-meal-order", user?.id, search.order_id],
    queryFn: async () => {
      try {
        return await apiFetch<MealOrder>(`/v1/meal-orders/${search.order_id}`);
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }
    },
    enabled: Boolean(user && search.order_id && returnedOrder?.sales_channel === "meal_preorder"),
    retry: false,
  });
  const storedPaymentStatus =
    returnedOrder
      ? returnedOrder.payment_status
      : undefined;
  const returnResult =
    refreshedPaymentStatus
      && !["pending", "confirming"].includes(refreshedPaymentStatus)
      ? refreshedPaymentStatus
      : search.payment === "confirming"
        && storedPaymentStatus
        && storedPaymentStatus !== "pending"
        ? storedPaymentStatus
        : search.result || search.payment || search.logistics;
  const selectedPaymentIsConfirming = Boolean(
    returnedOrder
      && returnResult === "confirming",
  );
  const actionError =
    openPayment.error || reopenLogistics.error;

  useEffect(() => {
    if (returnedMeal.isSuccess && !returnedMeal.isFetching && returnedMeal.data && returnedMeal.data.id === search.order_id && !selectedPaymentIsConfirming) {
      void navigate({ to: "/meal-orders", search: { order_id: returnedMeal.data.id }, replace: true });
    }
  }, [navigate, returnedMeal.data, returnedMeal.isFetching, returnedMeal.isSuccess, search.order_id, selectedPaymentIsConfirming]);

  if (!isAuthReady) {
    return (
      <section className="account-gate" aria-live="polite">
        <LoadingLines count={3} />
        <p>正在恢復登入狀態與付款結果…</p>
      </section>
    );
  }

  if (!user) {
    return (
      <section className="account-gate">
        <Basket size={45} weight="light" />
        <p className="eyebrow">ORDER HISTORY / 訂單紀錄</p>
        <h1>登入後查看訂單進度。</h1>
        <p>物流選店、線上付款或配送中斷時，都能從這裡安全地繼續。</p>
        <button className="button button-primary" type="button" onClick={openLogin}>帳號登入</button>
      </section>
    );
  }

  return (
    <section className="orders-page">
      <header className="workspace-heading">
        <div>
          <p className="eyebrow">ORDERS / 訂單中心</p>
          <h1>訂單中心</h1>
        </div>
      </header>

      {returnResult && (
        <div className={`return-banner ${returnResult}`}>
          {returnResult === "resume" ? <ArrowClockwise size={22} /> : <CheckCircle size={22} />}
          <div>
            <strong>{returnTitle(returnResult)}</strong>
            <span>{search.message || returnMessage(returnResult)}</span>
          </div>
        </div>
      )}

      {returnedOrder?.sales_channel === "meal_preorder" && selectedPaymentIsConfirming ? <DataState title="正在確認餐點付款結果" detail="確認完成後將開啟這筆餐點訂單。" /> : <Tabs.Root value={orderType} onValueChange={setOrderType} className="order-center-tabs">
        <Tabs.List className="tab-list" aria-label="訂單分類"><Tabs.Trigger value="meal">餐點訂單</Tabs.Trigger><Tabs.Trigger value="regular">一般訂單</Tabs.Trigger></Tabs.List>
        <Tabs.Content value="meal"><MealOrderHistory embedded /></Tabs.Content>
        <Tabs.Content value="regular">
      <OrderSearch query={query} payment={payment} onQuery={setQuery} onPayment={setPayment} count={visibleOrders.length} />
      {orders.data && generalOrders.length > 0 && visibleOrders.length === 0 && <DataState title="沒有符合條件的一般訂單" detail="請調整關鍵字或付款狀態。" />}
      {orders.isPending && <LoadingLines count={4} />}
      {returnedMeal.isError && <DataState kind="error" title="便當取餐頁暫時無法開啟" detail={returnedMeal.error.message} />}
      {orders.isError && (
        <DataState kind="error" title="訂單暫時無法讀取" detail={orders.error.message} />
      )}
      {orders.isSuccess && generalOrders.length === 0 && (
        <DataState title="目前還沒有一般訂單" detail="從生活消費選擇商品，第一筆交易會出現在這裡。" />
      )}

      {visibleOrders.length > 0 && (
        <div className="orders-layout">
          <div className="order-index" aria-label="訂單清單">
            {visibleOrders.map((order) => (
              <button
                key={order.id}
                type="button"
                aria-pressed={selected?.id === order.id}
                className={selected?.id === order.id ? "selected" : ""}
                onClick={() => setSelectedId(order.id)}
              >
                <span>{formatDate(order.created_at)}</span>
                <strong className="order-number">{order.order_number}</strong>
                <small>{salesChannelLabel(order.sales_channel)} · {formatMoney(order.amount_total)}</small>
                <i className={`order-state ${order.payment_status}`}>
                  {paymentStatusLabel(order.payment_status)}
                </i>
              </button>
            ))}
          </div>

          {selected && (
            <article className="order-detail">
              <div className="order-detail-head">
                <div>
                  <p className="eyebrow">{selected.sales_channel === "meal_preorder" ? "MEAL PREORDER" : selected.sales_channel === "group" ? "GROUP ORDER" : "DAILY PROVISIONS"} / {salesChannelLabel(selected.sales_channel)}</p>
                  <h2 className="order-number order-number-large">{selected.order_number}</h2>
                  <span>{formatDate(selected.created_at)} 建立</span>
                </div>
                <strong>{formatMoney(selected.amount_total)}</strong>
              </div>

              <div className="order-status-grid">
                <StatusBlock icon={CreditCard} label="付款" value={paymentStatusLabel(selected.payment_status)} />
                <StatusBlock icon={Package} label="履約" value={fulfillmentStatusLabel(selected.fulfillment?.status || selected.fulfillment_status)} />
                <StatusBlock icon={FileText} label="發票" value={invoiceStatusLabel(selected.invoice_status)} />
                <StatusBlock
                  icon={selected.fulfillment_method === "ecpay_logistics" ? Truck : MapPin}
                  label={selected.sales_channel === "meal_preorder" ? "取餐點" : "取貨方式"}
                  value={selected.fulfillment_method === "ecpay_logistics" ? "綠界物流" : selected.fulfillment?.pickup_location || "合作社取貨"}
                />
              </div>

              {selected.sales_channel === "meal_preorder" && selected.fulfillment && <div className="meal-pickup-info">
                {selected.fulfillment.pickup_address && <p><strong>取餐地址：</strong>{selected.fulfillment.pickup_address}</p>}
                <p><strong>餐具：</strong>{selected.fulfillment.needs_utensils ? "需要餐具" : "不需要餐具"}</p>
              </div>}

              <InvoiceTaxSummary variant="card" items={selected.items} memberTaxExempt={selected.member_tax_exempt_snapshot} shippingFee={selected.shipment?.shipping_fee} taxAmount={selected.tax_amount} />

              {selected.invoice_status !== "not_eligible" && (
                <div className="invoice-summary-card">
                  <FileText size={25} weight="light" />
                  <div>
                    <small>CLOUD INVOICE</small>
                    <strong>{selected.invoice?.invoice_number || invoiceStatusLabel(selected.invoice_status)}</strong>
                    <span>雲端交付，不寄送紙本發票</span>
                  </div>
                  <dl>
                    <div>
                      <dt>開立日期</dt>
                      <dd>{selected.invoice?.invoice_date ? formatDate(selected.invoice.invoice_date) : "尚未開立"}</dd>
                    </div>
                    <div>
                      <dt>交付方式</dt>
                      <dd>{invoiceDeliveryLabel(selected)}</dd>
                    </div>
                  </dl>
                </div>
              )}

              {selected.shipment && (
                <div className="shipment-card">
                  <Truck size={25} weight="light" />
                  <div>
                    <small>LOGISTICS</small>
                    <strong>{shippingChannelLabels[selected.shipment.channel]} · {temperatureLabels[selected.shipment.temperature]}</strong>
                    <span>{shipmentLabels[selected.shipment.status] || selected.shipment.status}</span>
                  </div>
                  <dl>
                    <div><dt>運費</dt><dd>{formatMoney(selected.shipment.shipping_fee)}</dd></div>
                    <div><dt>追蹤號碼</dt><dd>{selected.shipment.tracking_number || "尚未產生"}</dd></div>
                  </dl>
                </div>
              )}

              <div className="order-lines">
                {selected.items.map((item, index) => (
                  <div key={item.id || `${item.product_name}-${index}`}>
                    <span>{String(index + 1).padStart(2, "0")}</span>
                    <strong>{item.product_name}</strong>
                    <small>{item.quantity} {item.unit_label} × {formatMoney(item.unit_price)} · {taxTypeLabel(item.tax_type)}</small>
                    <b>{formatMoney(item.subtotal)}</b>
                  </div>
                ))}
              </div>

              <OrderQuantityAmendment key={selected.id} order={selected} disabled={openPayment.isPending || reopenLogistics.isPending || selectedPaymentIsConfirming} />
              {selected.cancellation_request && <section className="order-cancellation-status" aria-label="退單申請狀態">
                <strong>{selected.cancellation_request.status === "pending" ? "退單待管理員審核，訂單尚未取消" : selected.cancellation_request.status === "approved" ? "退單已核准" : "退單未核准"}</strong>
                <p>申請原因：{selected.cancellation_request.reason}</p>
                {selected.cancellation_request.review_reason && <p>審核原因：{selected.cancellation_request.review_reason}</p>}
              </section>}
              {actionError && <p className="form-error">{actionError.message}</p>}
              <div className="order-actions">
                {selectedPaymentIsConfirming && (
                  <small className="no-action">
                    正在向金流確認，請勿重複付款。
                  </small>
                )}
                {selected.shipment?.status === "selection_pending" && (
                  <button
                    className="button button-system"
                    type="button"
                    onClick={() => reopenLogistics.mutate(selected.id)}
                    disabled={reopenLogistics.isPending}
                  >
                    <Truck size={18} />
                    {reopenLogistics.isPending ? "準備物流頁…" : "繼續選擇物流"}
                  </button>
                )}
                {!groupPaymentClosed && !selectedPaymentIsConfirming && selected.available_actions.includes("pay") && (
                  <button
                    className="button button-primary"
                    type="button"
                    onClick={() => openPayment.mutate(selected.id)}
                    disabled={openPayment.isPending}
                  >
                    <CreditCard size={18} />
                    {openPayment.isPending ? "準備付款頁…" : "前往線上付款"}
                  </button>
                )}
              </div>

              <OrderCancellationContact />
            </article>
          )}
        </div>
      )}

      <div className="orders-footnote">
        <Clock size={18} weight="light" />
        <span>付款或物流回傳可能需要數秒；本頁會自動更新，也可稍後再回來查看。</span>
        <Link className="text-link" to="/meal-orders">便當取餐憑證</Link>
        <Link className="text-link" to="/shop">繼續選購</Link>
      </div>
        </Tabs.Content>
      </Tabs.Root>}
    </section>
  );
}

function StatusBlock({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Package;
  label: string;
  value: string;
}) {
  return (
    <div>
      <Icon size={21} weight="light" />
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function salesChannelLabel(channel: Order["sales_channel"]): string {
  return {
    regular: "一般訂單",
    group: "共同購買",
    meal_preorder: "共煮預訂",
  }[channel];
}

function invoiceDeliveryLabel(order: Order): string {
  if (order.invoice_buyer_type === "company") {
    return order.invoice_buyer_email || order.contact_email;
  }
  if (order.invoice_carrier_type === "mobile_barcode") return "手機條碼載具";
  return "Email 通知";
}

function returnTitle(result: string): string {
  return {
    resume: "訂單已保存，請從這裡繼續",
    paid: "線上付款已完成",
    succeeded: "線上付款已完成",
    confirming: "付款結果確認中",
    expired: "付款時間已結束",
    selected: "物流資料已確認",
    failed: "這次操作尚未完成",
  }[result] || "訂單狀態已更新";
}

function returnMessage(result: string): string {
  return {
    resume: "付款或物流服務暫時未開啟，訂單不會重複建立。",
    paid: "付款已由金流查詢確認，發票將接續處理。",
    succeeded: "付款已由金流查詢確認，發票將接續處理。",
    confirming: "系統正在向金流查詢，請勿重複付款；離開頁面後仍會在背景補查。",
    expired: "尚未確認付款；若已扣款，系統仍會繼續補查。",
    selected: "現在可以建立付款頁，完成這筆交易。",
    failed: "請查看下方狀態並重新操作。",
  }[result] || "訂單狀態已更新。";
}
