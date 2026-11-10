import { AdminWalkInSale } from "../components/AdminWalkInSale";
import { AdminInvoiceIssue } from "../components/AdminInvoiceIssue";
import { OrderQuantityAmendment } from "../components/OrderQuantityAmendment";
import {
  Buildings,
  MapPin,
  Package,
  Truck,
} from "@phosphor-icons/react";
import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query";
import { Link, useSearch } from "@tanstack/react-router";
import { useEffect, useState, type ReactNode } from "react";

import { DataState, LoadingLines, SectionHeading } from "../components/Shared";
import { AdminCashPayment } from "../components/AdminCashPayment";
import { AdminNav } from "../components/AdminNav";
import { AdminOperationsNav } from "../components/AdminOperationsNav";
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
import { apiFetch, formatDate, formatMoney } from "../lib/api";
import { useAdminOrderAccess } from "../lib/admin-order-access";
import { shippingChannelLabels } from "../lib/commerce";
import {
  adminPaymentStatusLabel,
} from "../lib/labels";
import type {
  Order,
  PickupLocation,
  Supplier,
} from "../lib/types";

interface AdminOrder extends Order {
  customer_name: string;
  contact_phone: string | null;
  contact_phone_status: "available" | "missing" | "unavailable";
}

interface AdminOrderPage {
  items: AdminOrder[];
  total: number;
  limit: number;
  offset: number;
}

import { OrderCategoryFilter } from "../components/OrderCategoryFilter";
import { orderCategoryLabels, type OrderCategory } from "../lib/order-category";

type OperationSection = "orders" | "manual-invoice" | "summary" | "issues" | "suppliers" | "locations";

export function AdminPage() {
  const { user, openLogin } = useAuth();
  const isAdmin = user?.user_role === "admin";
  const queryClient = useQueryClient();
  const orderAccess = useAdminOrderAccess();
  const routeSearch = useSearch({ from: "/admin" });
  const section: OperationSection = routeSearch.section || "orders";
  const [orderSearchInput, setOrderSearchInput] = useState(routeSearch.q || "");
  const [orderSearch, setOrderSearch] = useState(routeSearch.q || "");
  const [orderOffset, setOrderOffset] = useState(0);
  const [orderCategory, setOrderCategory] = useState<OrderCategory>("all");
  const [orderPayment, setOrderPayment] = useState("");
  const [cancellationPending, setCancellationPending] = useState(false);
  useEffect(() => {
    setOrderSearchInput(routeSearch.q || "");
    setOrderSearch(routeSearch.q || "");
    setOrderOffset(0);
    setOrderCategory("all"); setOrderPayment(""); setCancellationPending(false);
  }, [routeSearch.q]);
  const [supplierQuery, locationQuery, orderQuery] = useQueries({
    queries: [
      {
        queryKey: ["admin-suppliers"],
        queryFn: () => apiFetch<Supplier[]>("/v1/admin/suppliers"),
        enabled: isAdmin && section === "suppliers",
      },
      {
        queryKey: ["admin-pickup-locations"],
        queryFn: () => apiFetch<PickupLocation[]>("/v1/admin/pickup-locations"),
        enabled: isAdmin && section === "locations",
      },
      {
        queryKey: ["admin-orders", user?.id, orderSearch, orderOffset, cancellationPending, orderCategory, orderPayment],
        queryFn: ({ signal }) => orderAccess.request<AdminOrderPage>(`/v1/admin/order-search?q=${encodeURIComponent(orderSearch)}&offset=${orderOffset}&limit=12&cancellation_pending=${cancellationPending}&order_category=${orderCategory}${orderPayment ? `&payment_status=${orderPayment}` : ""}`, { signal }),
        enabled: isAdmin && !orderAccess.denied && section === "orders",
        retry: (failureCount) => !orderAccess.isDenied() && failureCount < 2,
      },
    ],
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
    mutationFn: (orderId: string) =>
      orderAccess.request(`/v1/admin/orders/${orderId}/logistics/create`, { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-orders"] }),
  });
  const advanceShipment = useMutation({
    mutationFn: ({ orderId, status }: { orderId: string; status: string }) =>
      orderAccess.request(`/v1/admin/orders/${orderId}/logistics/sandbox-status`, {
        method: "POST",
        body: JSON.stringify({ status, reason: "後台模擬物流狀態更新" }),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-orders"] }),
  });

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

  const orderContent = orderAccess.denied
    ? <DataState kind="error" title="管理權限待重新驗證" detail="請重新整理頁面並確認管理者登入狀態。" />
    : <section className="admin-orders-panel">
    <OrderCategoryFilter value={orderCategory} onChange={(value) => { setOrderCategory(value); setOrderOffset(0); }} />
    {orderQuery.isPending && <LoadingLines count={4} />}
    {orderQuery.isError && <DataState kind="error" title="訂單資料無法讀取" detail={orderQuery.error.message} />}
    <form className="admin-order-search" onSubmit={(event) => { event.preventDefault(); setOrderOffset(0); setOrderSearch(orderSearchInput.trim()); }}>
      <label>搜尋訂單<input value={orderSearchInput} onChange={(event) => setOrderSearchInput(event.target.value)} type="search" placeholder="訂單編號、姓名、Email、商品或統編" maxLength={120} /></label>
      <label className="field"><span>付款狀態</span><select value={orderPayment} onChange={(event) => { setOrderPayment(event.target.value); setOrderOffset(0); }}><option value="">全部狀態</option><option value="pending">待付款</option><option value="paid">已付款</option><option value="refund_pending">退款中</option><option value="refunded">已退款</option><option value="failed">付款失敗</option><option value="expired">已逾期</option><option value="late_paid_refund_required">付款晚到待退款</option></select></label>
      <label className="check-field"><input type="checkbox" checked={cancellationPending} onChange={(event) => { setCancellationPending(event.target.checked); setOrderOffset(0); }} />只看退單待審</label>
      <button className="button button-quiet" type="submit">搜尋</button>
      <button className="button button-quiet" type="button" onClick={() => { setOrderSearchInput(""); setOrderSearch(""); setOrderPayment(""); setCancellationPending(false); setOrderOffset(0); }}>清除查詢</button>
    </form>
    <div className="operations-list">
      {!orderQuery.isError && orderQuery.data?.items.map((order) => (
        <article key={order.id}>
          <div className="operation-title">
            <span>{orderCategoryLabels[order.sales_channel === "meal_preorder" ? "meal" : "general"]} · {formatDate(order.created_at)}</span>
            <strong>{order.order_number}</strong>
            <small>{formatMoney(order.amount_total)}</small>
          </div>
          <dl className="admin-order-contact" aria-label="訂購者資訊">
            <div><dt>訂購者</dt><dd>{order.customer_name || "未提供"}</dd></div>
            <div><dt>聯絡電話</dt><dd>{order.contact_phone_status === "available" && order.contact_phone ? <a href={`tel:${order.contact_phone}`}>{order.contact_phone}</a> : order.contact_phone_status === "unavailable" ? "暫時無法讀取" : "未提供"}</dd></div>
            <div><dt>Email</dt><dd>{order.contact_email ? <a href={`mailto:${order.contact_email}`}>{order.contact_email}</a> : "未提供"}</dd></div>
          </dl>
          <div className="operation-state">
            <OrderStatusBadge kind="payment" status={order.payment_status} />
            <OrderStatusBadge kind="fulfillment" status={order.fulfillment_status} />
            <OrderStatusBadge kind="invoice" status={order.invoice_status} />
            {order.sales_channel !== "meal_preorder" && order.shipment && <span className="system-state"><Truck size={16} />{shippingChannelLabels[order.shipment.channel]} · {order.shipment.status}</span>}
          </div>
          <InvoiceTaxSummary variant="card" items={order.items} memberTaxExempt={order.member_tax_exempt_snapshot} shippingFee={order.sales_channel === "meal_preorder" ? undefined : order.shipment?.shipping_fee} taxAmount={order.tax_amount} invoiceTax={order.invoice_tax} invoiceStatus={order.invoice_status} />
          <AdminOrderAction
            order={order}
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
    {!orderQuery.isError && orderQuery.data?.total === 0 && <p>沒有符合條件的訂單。</p>}
    {(updateFulfillment.isError || createLogistics.isError || advanceShipment.isError) && (
      <p className="form-error">{(updateFulfillment.error || createLogistics.error || advanceShipment.error)?.message}</p>
    )}
  </section>;

  const supplierContent = <section className="admin-table-panel">
    <div className="admin-operation-heading"><SectionHeading eyebrow="SUPPLIERS / 供應者" title="供應者與審核狀態" /><Link className="button button-quiet" to="/admin/catalog" search={{ tab: "suppliers" }}>管理供應者</Link></div>
    {supplierQuery.isPending && <LoadingLines count={4} />}
    {supplierQuery.isError && <DataState kind="error" title="供應者資料無法讀取" detail={supplierQuery.error.message} />}
    <div className="data-table" role="table" aria-label="供應者清單">
      <div className="data-row data-head" role="row"><span>供應者</span><span>編號</span><span>認可日期</span><span>狀態</span></div>
      {supplierQuery.data?.map((supplier) => (
        <div className="data-row" role="row" key={supplier.id}>
          <strong>{supplier.business_name}</strong><span>{supplier.supplier_number || "待審核"}</span>
          <span>{supplier.accredited_on ? formatDate(supplier.accredited_on) : "—"}</span>
          <span className={supplier.is_active ? "status-active" : "status-pending"}>{supplier.is_active ? "有效" : "未啟用"}</span>
        </div>
      ))}
    </div>
  </section>;

  const locationContent = <section className="admin-table-panel">
    <div className="admin-operation-heading"><SectionHeading eyebrow="PICKUP NETWORK / 取貨網絡" title="取貨點維護" /><Link className="button button-quiet" to="/admin/catalog" search={{ tab: "locations" }}>管理取貨點</Link></div>
    {locationQuery.isPending && <LoadingLines count={3} />}
    {locationQuery.isError && <DataState kind="error" title="取貨點資料無法讀取" detail={locationQuery.error.message} />}
    <div className="location-list">
      {locationQuery.data?.map((location) => (
        <article key={location.id}>
          <MapPin size={19} weight="light" /><div><strong>{location.name}</strong><small>{location.code}</small></div>
          <span>{location.is_active ? "啟用" : "停用"}</span>
        </article>
      ))}
    </div>
  </section>;

  const content = section === "orders" ? orderContent
    : section === "manual-invoice" ? <AdminWalkInSale key={user.id} />
    : section === "summary" ? <AdminOrderSummary />
    : section === "issues" ? <section className="admin-issues-panel"><SectionHeading eyebrow="SYSTEM STATUS / 系統狀態" title="問題回報" /><AdminFailurePanel /></section>
      : section === "suppliers" ? supplierContent : locationContent;

  return <>
    <section className="admin-intro admin-operations-intro">
      <div><p className="eyebrow">ORDER OPERATIONS / 訂單營運</p><h1>{section === "manual-invoice" ? "手動開票" : section === "summary" ? "訂單彙整" : section === "issues" ? "問題回報" : section === "suppliers" ? "供應者與審核狀態" : section === "locations" ? "取貨點維護" : "訂單、付款與發票"}</h1></div>
      <div className="admin-intro-meta"><span>管理者</span><strong>{user.display_name}</strong></div>
    </section>
    <AdminNav />
    <AdminOperationsNav section={section} />
    <section className="admin-operation-page content-section">{content}</section>
  </>;
}

function AdminOrderAction({
  order,
  busy,
  onFulfillment,
  onCreateLogistics,
  onShipment,
}: {
  order: Order;
  busy: boolean;
  onFulfillment: (status: string) => void;
  onCreateLogistics: () => void;
  onShipment: (status: string) => void;
}) {
  const invoiceControl = <AdminStatusQuery orderId={order.id} kind="invoice" disabled={busy} />;
  if (order.sales_channel === "meal_preorder") {
    const canManageMeal = order.payment_status !== "refunded" && order.fulfillment_status !== "cancelled" && !order.cancelled_at;
    return <div className="operation-actions">{canManageMeal && <Link className="button button-quiet" to="/admin/meals" search={{ tab: "orders", q: order.order_number }}>便當取餐與退單</Link>}{invoiceControl}</div>;
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
  if (order.fulfillment_status === "pending_confirmation") {
    fulfillmentControl = <button type="button" disabled={busy} onClick={() => onFulfillment("preparing")}>進入備貨</button>;
  } else if (order.fulfillment_method === "ecpay_logistics") {
    if (order.shipment?.status === "ready_to_create") {
      fulfillmentControl = <button type="button" disabled={busy} onClick={onCreateLogistics}>建立綠界物流單</button>;
    } else if (order.shipment?.status === "created") {
      fulfillmentControl = <button type="button" disabled={busy} onClick={() => onShipment("in_transit")}>模擬配送中</button>;
    } else if (order.shipment?.status === "in_transit") {
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
