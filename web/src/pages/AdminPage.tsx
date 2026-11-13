import { AdminWalkInSale } from "../components/AdminWalkInSale";
import { AdminInvoiceIssue } from "../components/AdminInvoiceIssue";
import { OrderQuantityAmendment } from "../components/OrderQuantityAmendment";
import { Buildings, Package, Truck } from "@phosphor-icons/react";
import { onlineManager, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearch } from "@tanstack/react-router";
import { useEffect, useState, useSyncExternalStore, type ReactNode } from "react";

import { DataState, LoadingLines, SectionHeading } from "../components/Shared";
import { AdminCashPayment } from "../components/AdminCashPayment";
import { AdminNav } from "../components/AdminNav";
import { AdminOperationsNav } from "../components/AdminOperationsNav";
import { AdminToday } from "../components/AdminToday";
import { AdminOrderSummary } from "../components/AdminOrderSummary";
import { AdminOrderCancellationReview } from "../components/AdminOrderCancellationReview";
import { AdminFailurePanel } from "../components/AdminFailurePanel";
import { AdminInvoiceAdjustment } from "../components/AdminInvoiceAdjustment";
import { AdminCancelOrder } from "../components/AdminCancelOrder";
import { AdminOrderPrint } from "../components/AdminOrderPrint";
import { AdminStatusQuery } from "../components/AdminStatusQuery";
import { OrderStatusBadge } from "../components/OrderStatusBadge";
import { InvoiceTaxSummary } from "../components/InvoiceTaxSummary";
import "./AdminOrderOperations.css";
import { useAuth } from "../context/AuthContext";
import { formatDate, formatMoney } from "../lib/api";
import { useAdminOrderAccess } from "../lib/admin-order-access";
import { shippingChannelLabels } from "../lib/commerce";
import {
  adminPaymentStatusLabel,
  invoiceProviderNames,
  shipmentStatusLabel,
} from "../lib/labels";
import { formatMealDate, formatMealDateTime } from "../lib/meal-time";
import type { Order } from "../lib/types";

type InvoiceFollowupReason = "business_date" | "issuance" | "void";

interface AdminOrder extends Order {
  customer_name: string;
  contact_phone: string | null;
  contact_phone_status: "available" | "missing" | "unavailable";
  invoice_followup_reason?: InvoiceFollowupReason;
}

interface AdminOrderPage {
  items: AdminOrder[];
  total: number;
  limit: number;
  offset: number;
}

import { orderCategoryLabels } from "../lib/order-category";

const orderSearchCategories = {
  all: orderCategoryLabels.all,
  daily: "日常選品",
  group: "共同購買",
  meal: orderCategoryLabels.meal,
} as const;
type OrderSearchCategory = keyof typeof orderSearchCategories;
type OrderSearchPeriod = { basis: "ordered" | "pickup"; start: string; end: string };
const emptyOrderPeriod: OrderSearchPeriod = { basis: "ordered", start: "", end: "" };

const operationTitles = {
  today: "今日待辦",
  orders: "訂單、付款與發票",
  "manual-invoice": "手動開票",
  summary: "訂單彙整",
  issues: "問題回報",
};

const invoiceFollowupCopy = {
  business_date: { label: "會餐發票日期待核對", action: "核對付款日、取餐日與發票日期。" },
  issuance: { label: "開票待核對", action: "使用「查詢發票」確認原發票的開立狀態。" },
  void: { label: "作廢待核對" },
};

export function AdminPage() {
  const { user, openLogin } = useAuth();
  const isAdmin = user?.user_role === "admin";
  const orderAccess = useAdminOrderAccess();
  const routeSearch = useSearch({ from: "/admin" });
  const { q, queue, order_id: orderId } = routeSearch;
  const section = routeSearch.section === "suppliers" || routeSearch.section === "locations"
    ? "today" : routeSearch.section ?? (q || queue || orderId ? "orders" : "today");
  if (!isAdmin) {
    return (
      <section className="admin-gate">
        <div className="admin-gate-mark"><Buildings size={38} weight="light" /></div>
        <h1>管理工作台</h1>
        {!user && <button className="button button-primary" type="button" onClick={openLogin}>管理者登入</button>}
        {user && <Link className="button button-quiet" to="/social">返回社務系統</Link>}
      </section>
    );
  }

  const content = section === "today" ? <AdminToday adminId={user.id} />
    : section === "orders" ? <AdminOrdersPanel key={JSON.stringify([user.id, q, queue, orderId])} adminId={user.id} q={q} queue={queue} initialOrderId={orderId} orderAccess={orderAccess} />
    : section === "manual-invoice" ? <AdminWalkInSale key={user.id} />
    : section === "summary" ? <AdminOrderSummary />
    : <section className="admin-issues-panel"><SectionHeading eyebrow="SYSTEM STATUS / 系統狀態" title="問題回報" /><AdminFailurePanel /></section>;

  return <>
    <section className="admin-intro admin-operations-intro">
      <div><p className="eyebrow">{section === "today" ? "管理工作台" : "ORDER OPERATIONS / 訂單營運"}</p><h1>{operationTitles[section]}</h1></div>
      <div className="admin-intro-meta"><span>管理者</span><strong>{user.display_name}</strong></div>
    </section>
    <AdminNav />
    {section !== "today" && <AdminOperationsNav section={section} />}
    <section className="admin-operation-page content-section">{content}</section>
  </>;
}

const subscribeOrderConnection = (listener: () => void) => onlineManager.subscribe(listener);
const hasOrderConnection = () => onlineManager.isOnline();

function AdminOrdersPanel({ adminId, q, queue, initialOrderId, orderAccess }: {
  adminId: string;
  q?: string;
  queue?: "refunds" | "business-unpaid" | "invoice";
  initialOrderId?: string;
  orderAccess: ReturnType<typeof useAdminOrderAccess>;
}) {
  const queryClient = useQueryClient();
  const isOnline = useSyncExternalStore(subscribeOrderConnection, hasOrderConnection);
  const [orderSearchInput, setOrderSearchInput] = useState(q || "");
  const [orderSearch, setOrderSearch] = useState(q || "");
  const [orderId, setOrderId] = useState(initialOrderId);
  const [orderOffset, setOrderOffset] = useState(0);
  const [orderCategory, setOrderCategory] = useState<OrderSearchCategory>(queue === "business-unpaid" ? "meal" : "all");
  const [orderPayment, setOrderPayment] = useState("");
  const [periodInput, setPeriodInput] = useState(emptyOrderPeriod);
  const [period, setPeriod] = useState(emptyOrderPeriod);
  const [cancellationPending, setCancellationPending] = useState(queue === "refunds");
  const [businessUnpaid, setBusinessUnpaid] = useState(queue === "business-unpaid");
  const [invoiceFollowup, setInvoiceFollowup] = useState(queue === "invoice");
  const params = new URLSearchParams({
    q: orderSearch, offset: String(orderOffset), limit: "12",
    cancellation_pending: String(cancellationPending), order_category: orderCategory,
  });
  if (orderId) params.set("order_id", orderId);
  if (orderPayment === "refunds") params.set("refunds_only", "true");
  else if (orderPayment) params.set("payment_status", orderPayment);
  if (period.start || period.end) {
    params.set("date_basis", period.basis);
    if (period.start) params.set("starts_on", period.start);
    if (period.end) params.set("ends_on", period.end);
  }
  if (businessUnpaid) params.set("business_unpaid", "true");
  if (invoiceFollowup) params.set("invoice_followup", "true");
  const orderQuery = useQuery({
    queryKey: ["admin-orders", adminId, params.toString()],
    queryFn: async ({ signal }) => {
      const result = await orderAccess.request<AdminOrderPage>(`/v1/admin/order-search?${params}`, { signal });
      if (!orderId) return result;
      // Older APIs may ignore order_id. Keep all order actions bound to the selected record.
      const items = result.items.filter(order => order.id === orderId);
      return { ...result, items, total: items.length };
    },
    enabled: !orderAccess.denied,
    staleTime: 0,
    retry: (failureCount) => !orderAccess.isDenied() && failureCount < 2,
  });
  useEffect(() => {
    if (orderQuery.data && orderOffset > 0 && orderOffset >= orderQuery.data.total) setOrderOffset(Math.max(0, Math.floor((orderQuery.data.total - 1) / 12) * 12));
  }, [orderOffset, orderQuery.data]);
  const updateFulfillment = useMutation({
    mutationFn: ({ orderId, status }: { orderId: string; status: string }) =>
      orderAccess.request<Order>(`/v1/orders/${orderId}/admin/fulfillment`, {
        method: "PATCH",
        body: JSON.stringify({ status }),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-orders"] }),
  });
  const createLogistics = useMutation({
    networkMode: "always",
    mutationFn: (orderId: string) =>
      orderAccess.request(`/v1/admin/orders/${orderId}/logistics/create`, { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-orders"] }),
  });
  const advanceShipment = useMutation({
    networkMode: "always",
    mutationFn: ({ orderId, status }: { orderId: string; status: string }) =>
      orderAccess.request(`/v1/admin/orders/${orderId}/logistics/sandbox-status`, {
        method: "POST",
        body: JSON.stringify({ status, reason: "後台模擬物流狀態更新" }),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-orders"] }),
  });

  return orderAccess.denied
    ? <DataState kind="error" title="管理權限待重新驗證" detail="請重新整理頁面並確認管理者登入狀態。" />
    : <section className="admin-orders-panel">
    <label className="field order-category-filter"><span>訂單分類</span>
      <select value={orderCategory} onChange={(event) => {
        const category = event.target.value as OrderSearchCategory;
        setOrderCategory(category);
        setOrderOffset(0);
        if (category === "daily" || category === "group") {
          setBusinessUnpaid(false);
          setPeriodInput(current => ({ ...current, basis: "ordered" }));
          setPeriod(current => ({ ...current, basis: "ordered" }));
        }
      }}>
        {Object.entries(orderSearchCategories).map(([category, label]) => <option key={category} value={category}>{label}</option>)}
      </select>
    </label>
    {orderQuery.isPending && <LoadingLines count={4} />}
    {orderQuery.isError && <DataState kind="error" title="訂單資料無法讀取" detail={orderQuery.error.message} />}
    <form className="admin-order-search" onSubmit={(event) => {
      event.preventDefault();
      const term = orderSearchInput.trim();
      if (term !== orderSearch) setOrderId(undefined);
      setOrderSearch(term);
      setPeriod(periodInput);
      setOrderOffset(0);
    }}>
      <label>搜尋訂單<input value={orderSearchInput} onChange={(event) => setOrderSearchInput(event.target.value)} type="search" placeholder="訂單編號、姓名、Email、商品或統編" maxLength={120} /></label>
      <label className="field"><span>付款狀態</span><select aria-label="付款狀態" value={orderPayment} onChange={(event) => { setOrderPayment(event.target.value); setOrderOffset(0); }}><option value="">全部狀態</option><option value="paid">已付款</option><option value="refunds">退款</option><option value="pending">待付款</option></select></label>
      <fieldset className="admin-order-period">
        <legend>日期篩選</legend>
        <label className="field"><span>日期類型</span><select aria-label="日期類型" value={periodInput.basis} onChange={event => setPeriodInput({ ...periodInput, basis: event.target.value === "pickup" ? "pickup" : "ordered" })}>
          <option value="ordered">下單日期</option>
          <option value="pickup" disabled={orderCategory === "daily" || orderCategory === "group"}>取餐日期（便當）</option>
        </select></label>
        <label className="field"><span>開始日期</span><input type="date" value={periodInput.start} max={periodInput.end || undefined} onChange={event => setPeriodInput({ ...periodInput, start: event.target.value })} /></label>
        <label className="field"><span>結束日期</span><input type="date" value={periodInput.end} min={periodInput.start || undefined} onChange={event => setPeriodInput({ ...periodInput, end: event.target.value })} /></label>
      </fieldset>
      {cancellationPending && <button className="button button-quiet" type="button" onClick={() => {
        setCancellationPending(false);
        setOrderOffset(0);
      }}>清除退單待審篩選</button>}
      <label className="check-field"><input type="checkbox" checked={businessUnpaid} onChange={event => { setBusinessUnpaid(event.target.checked); setOrderOffset(0); if (event.target.checked) { setOrderCategory("meal"); setOrderPayment(""); setInvoiceFollowup(false); } }} />會餐未付款</label>
      {invoiceFollowup && <label className="check-field"><input type="checkbox" checked onChange={() => { setInvoiceFollowup(false); setOrderOffset(0); }} />發票待合作社處理</label>}
      <button className="button button-quiet" type="submit">搜尋</button>
      <button className="button button-quiet" type="button" onClick={() => { setOrderSearchInput(""); setOrderSearch(""); setOrderId(undefined); setOrderPayment(""); setPeriodInput(emptyOrderPeriod); setPeriod(emptyOrderPeriod); setCancellationPending(false); setBusinessUnpaid(false); setInvoiceFollowup(false); setOrderOffset(0); }}>清除查詢</button>
    </form>
    <div className="operations-list">
      {!orderQuery.isError && orderQuery.data?.items.map((order) => (
        <article key={order.id}>
          <div className="operation-title">
            <span>{order.sales_channel === "meal_preorder"
              ? `${orderCategoryLabels.meal} · 下單日 ${formatMealDate(order.created_at)}`
              : `${orderSearchCategories[order.sales_channel === "group" ? "group" : "daily"]} · ${formatDate(order.created_at)}`}</span>
            <strong>{order.order_number}</strong>
            <small>{formatMoney(order.amount_total)}</small>
            {order.sales_channel === "meal_preorder" && <MealPickupInfo order={order} />}
          </div>
          <dl className="admin-order-contact" aria-label="訂購者資訊">
            <div><dt>訂購者</dt><dd>{order.customer_name || "未提供"}</dd></div>
            <div><dt>聯絡電話</dt><dd>{order.contact_phone_status === "available" && order.contact_phone ? <a href={`tel:${order.contact_phone}`}>{order.contact_phone}</a> : order.contact_phone_status === "unavailable" ? "暫時無法讀取" : "未提供"}</dd></div>
            <div><dt>Email</dt><dd>{order.contact_email ? <a href={`mailto:${order.contact_email}`}>{order.contact_email}</a> : "未提供"}</dd></div>
            {order.meal_booking_type === "business" && <>
              <div><dt>統一編號</dt><dd>{order.invoice_buyer_tax_id || "未提供"}</dd></div>
              <div><dt>公司名稱</dt><dd>{order.invoice_buyer_name || "未提供"}</dd></div>
            </>}
          </dl>
          <div className="operation-state">
            {order.meal_booking_type === "business" && <span className="status-chip">會餐{order.payment_followup_required ? " · 未付款待追蹤" : ""}</span>}
            {(order.invoice_followup_reason || order.invoice_followup_required) && <span className="status-chip">
              {order.invoice_followup_reason ? invoiceFollowupCopy[order.invoice_followup_reason].label : "發票待合作社處理"}
            </span>}
            <OrderStatusBadge kind="payment" status={order.payment_status} label={order.payment_status === "expired" && !order.cancelled_at ? "付款逾期" : undefined} />
            {order.sales_channel === "meal_preorder"
              ? mealServiceEnded(order) && <span className="status-chip">取餐時段已結束</span>
              : <OrderStatusBadge kind="fulfillment" status={order.fulfillment_status} label={pickupFulfillmentLabel(order)} />}
            <OrderStatusBadge kind="invoice" status={order.invoice_status} />
            {order.sales_channel !== "meal_preorder" && order.shipment && <span className="system-state"><Truck size={16} />{shippingChannelLabels[order.shipment.channel]} · {shipmentStatusLabel(order.shipment.status)}</span>}
          </div>
          {order.invoice_followup_reason && <InvoiceFollowupAction reason={order.invoice_followup_reason} invoice={order.invoice} />}
          <InvoiceTaxSummary variant="card" items={order.items} memberTaxExempt={order.member_tax_exempt_snapshot} shippingFee={order.sales_channel === "meal_preorder" ? undefined : order.shipment?.shipping_fee} taxAmount={order.tax_amount} invoiceTax={order.invoice_tax} invoiceStatus={order.invoice_status} />
          <AdminOrderAction
            order={order}
            logisticsReady={isOnline && orderQuery.isSuccess && orderQuery.isFetchedAfterMount
              && orderQuery.fetchStatus === "idle"}
            busy={updateFulfillment.isPending || createLogistics.isPending || advanceShipment.isPending}
            onFulfillment={(status) => updateFulfillment.mutate({ orderId: order.id, status })}
            onCreateLogistics={() => createLogistics.mutate(order.id)}
            onShipment={(status) => advanceShipment.mutate({ orderId: order.id, status })}
          />
          <AdminInvoiceIssue key={order.id + order.invoice_status + (order.invoice_handling || "")} order={order} />
          <OrderQuantityAmendment order={order} disabled={updateFulfillment.isPending || createLogistics.isPending || advanceShipment.isPending} />
          <AdminOrderPrint order={order} />
          <AdminCancelOrder order={order} />
          <AdminOrderCancellationReview order={order} />
          <AdminInvoiceAdjustment order={order} />
          <AdminCashPayment order={order} />
          {(!order.payment_method || order.payment_method === "online") && <AdminStatusQuery orderId={order.id} kind="payment" />}
        </article>
      ))}
    </div>
    {!orderQuery.isError && orderQuery.data && <nav className="admin-pagination" aria-label="訂單分頁">
      <button className="button button-quiet" type="button" disabled={orderOffset === 0 || orderQuery.isFetching} onClick={() => setOrderOffset(Math.max(0, orderOffset - 12))}>上一頁</button>
      <span>共 {orderQuery.data.total} 筆 · 第 {Math.floor(orderOffset / 12) + 1} 頁</span>
      <button className="button button-quiet" type="button" disabled={orderOffset + 12 >= orderQuery.data.total || orderQuery.isFetching} onClick={() => setOrderOffset(orderOffset + 12)}>下一頁</button>
    </nav>}
    {!orderQuery.isError && orderQuery.data?.total === 0 && <p>{orderId ? "找不到這筆訂單。" : "沒有符合條件的訂單。"}</p>}
    {(updateFulfillment.isError || createLogistics.isError || advanceShipment.isError) && (
      <p className="form-error">{(updateFulfillment.error || createLogistics.error || advanceShipment.error)?.message}</p>
    )}
  </section>;
}

function InvoiceFollowupAction({ reason, invoice }: { reason: InvoiceFollowupReason; invoice: Order["invoice"] }) {
  if (reason !== "void") return <small className="no-action">{invoiceFollowupCopy[reason].action}</small>;
  const provider = invoice?.provider;
  const platform = (provider && invoiceProviderNames[provider]) || "原開票平台";
  const action = provider === "fanyu"
    ? "使用「發票作廢處理」核對汎宇結果。"
    : `請至${platform}核對並處理作廢。`;
  const reference = invoice?.invoice_number ? `發票號碼 ${invoice.invoice_number}`
    : invoice?.relate_number ? `銷貨單號 ${invoice.relate_number}` : null;
  return <small className="no-action">{action}{reference && <> {reference}</>}</small>;
}

function MealPickupInfo({ order }: { order: Order }) {
  const selected = order.fulfillment?.pickup_at;
  if (selected) return <span>取餐日 <time dateTime={selected}>{formatMealDateTime(selected)}</time></span>;
  const start = order.fulfillment?.pickup_starts_at ?? order.meal_event?.pickup_starts_at;
  return <span>取餐日 {start ? <><time dateTime={start}>{formatMealDate(start)}</time> · 未指定取餐時段</> : "未提供"}</span>;
}

function mealServiceEnded(order: Order) {
  if (order.cancelled_at || order.fulfillment_status === "cancelled") return false;
  return order.fulfillment?.status === "no_show" ||
    (order.fulfillment?.pickup_ends_at && Date.parse(order.fulfillment.pickup_ends_at) <= Date.now());
}

function pickupFulfillmentLabel(order: Order) {
  if (order.fulfillment_method !== "cooperative_pickup" || order.payment_status !== "paid") return;
  if (order.fulfillment_status === "pending_confirmation" || order.fulfillment_status === "preparing") return "準備中";
  if (order.fulfillment_status === "ready_for_pickup") return "可領取";
}

function AdminOrderAction({
  order,
  logisticsReady,
  busy,
  onFulfillment,
  onCreateLogistics,
  onShipment,
}: {
  order: Order;
  logisticsReady: boolean;
  busy: boolean;
  onFulfillment: (status: string) => void;
  onCreateLogistics: () => void;
  onShipment: (status: string) => void;
}) {
  const invoiceControl = <AdminStatusQuery orderId={order.id} kind="invoice" disabled={busy} />;
  if (order.sales_channel === "meal_preorder") {
    const paymentFailed = ["expired", "failed"].includes(order.payment_status);
    const pendingCancellation = !paymentFailed && order.cancellation_request?.status === "pending";
    const canManageMeal = order.payment_status !== "refunded" && order.fulfillment_status !== "cancelled" && !order.cancelled_at;
    return <div className="operation-actions">
      {(pendingCancellation || canManageMeal) && <Link
        className="button button-quiet"
        to="/admin/meals"
        search={{ tab: pendingCancellation ? "cancellations" : "orders", q: order.order_number, order_id: pendingCancellation ? order.id : undefined }}
      >{pendingCancellation ? "審核便當退單" : paymentFailed ? "查看便當訂單" : "便當取餐與退單"}</Link>}
      {invoiceControl}
    </div>;
  }
  if (order.payment_status !== "paid") {
    return (
      <div className="operation-actions">
        <small className="no-action">{order.payment_status === "pending" ? "等待買家付款" : adminPaymentStatusLabel(order.payment_status)}</small>
        {order.invoice && invoiceControl}
      </div>
    );
  }
  let fulfillmentControl: ReactNode = null;
  const logisticsActions = logisticsReady && Array.isArray(order.available_actions) ? order.available_actions : [];
  if (order.fulfillment_status === "pending_confirmation") {
    fulfillmentControl = <button type="button" disabled={busy} onClick={() => onFulfillment("preparing")}>進入備貨</button>;
  } else if (order.fulfillment_method === "ecpay_logistics") {
    if (logisticsActions.includes("admin_create_logistics")) {
      fulfillmentControl = <button type="button" disabled={busy} onClick={onCreateLogistics}>建立綠界物流單</button>;
    } else if (logisticsActions.includes("admin_simulate_shipment_in_transit")) {
      fulfillmentControl = <button type="button" disabled={busy} onClick={() => onShipment("in_transit")}>模擬配送中</button>;
    } else if (logisticsActions.includes("admin_simulate_shipment_delivered")) {
      fulfillmentControl = <button type="button" disabled={busy} onClick={() => onShipment("delivered")}>模擬已送達</button>;
    }
  } else if (order.fulfillment_status === "preparing") {
    fulfillmentControl = <button type="button" disabled={busy} onClick={() => onFulfillment("ready_for_pickup")}>標記可領取</button>;
  } else if (order.fulfillment_status === "ready_for_pickup") {
    fulfillmentControl = <button type="button" disabled={busy} onClick={() => onFulfillment("picked_up")}>完成取貨</button>;
  }
  return (
    <div className="operation-actions">
      {fulfillmentControl}
      {invoiceControl}
    </div>
  );
}

function Metric({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Package;
  label: string;
  value?: number;
}) {
  return (
    <article>
      <Icon size={23} weight="light" />
      <span>{label}</span>
      <strong>{value ?? "—"}</strong>
    </article>
  );
}
