import {
  Buildings,
  CreditCard,
  FileText,
  MapPin,
  Package,
  Truck,
} from "@phosphor-icons/react";
import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query";
import { Link, useSearch } from "@tanstack/react-router";
import { useEffect, useState, type ReactNode } from "react";

import { DataState, LoadingLines, SectionHeading } from "../components/Shared";
import { AdminNav } from "../components/AdminNav";
import { AdminOperationsNav } from "../components/AdminOperationsNav";
import { AdminFailurePanel } from "../components/AdminFailurePanel";
import { AdminInvoiceAdjustment } from "../components/AdminInvoiceAdjustment";
import { InvoiceTaxSummary } from "../components/InvoiceTaxSummary";
import "./AdminOrderOperations.css";
import { useAuth } from "../context/AuthContext";
import { apiFetch, formatDate, formatMoney } from "../lib/api";
import { shippingChannelLabels } from "../lib/commerce";
import {
  adminPaymentStatusLabel,
  fulfillmentStatusLabel,
  invoiceStatusLabel,
} from "../lib/labels";
import type {
  AdminInvoiceQueryResult,
  Order,
  PickupLocation,
  Supplier,
} from "../lib/types";

interface AdminOrderPage {
  items: Order[];
  total: number;
  limit: number;
  offset: number;
}

type OperationSection = "orders" | "issues" | "suppliers" | "locations";

export function AdminPage() {
  const { user, openLogin } = useAuth();
  const isAdmin = user?.user_role === "admin";
  const queryClient = useQueryClient();
  const routeSearch = useSearch({ from: "/admin" });
  const section: OperationSection = routeSearch.section || "orders";
  const [orderSearchInput, setOrderSearchInput] = useState(routeSearch.q || "");
  const [orderSearch, setOrderSearch] = useState(routeSearch.q || "");
  const [orderOffset, setOrderOffset] = useState(0);
  useEffect(() => {
    setOrderSearchInput(routeSearch.q || "");
    setOrderSearch(routeSearch.q || "");
    setOrderOffset(0);
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
        queryKey: ["admin-orders", orderSearch, orderOffset],
        queryFn: () => apiFetch<AdminOrderPage>(`/v1/admin/order-search?q=${encodeURIComponent(orderSearch)}&offset=${orderOffset}&limit=12`),
        enabled: isAdmin && section === "orders",
      },
    ],
  });
  const updateFulfillment = useMutation({
    mutationFn: ({ orderId, status }: { orderId: string; status: string }) =>
      apiFetch<Order>(`/v1/orders/${orderId}/admin/fulfillment`, {
        method: "PATCH",
        body: JSON.stringify({ status }),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-orders"] }),
  });
  const createLogistics = useMutation({
    mutationFn: (orderId: string) =>
      apiFetch(`/v1/admin/orders/${orderId}/logistics/create`, { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-orders"] }),
  });
  const advanceShipment = useMutation({
    mutationFn: ({ orderId, status }: { orderId: string; status: string }) =>
      apiFetch(`/v1/admin/orders/${orderId}/logistics/sandbox-status`, {
        method: "POST",
        body: JSON.stringify({ status, reason: "後台模擬物流狀態更新" }),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-orders"] }),
  });
  const refreshFinancialOrders = () => Promise.all(["admin-orders", "admin-meal-orders", "admin-meal-operations-summary", "meal-sales-report"].map((key) => queryClient.invalidateQueries({ queryKey: [key] })));
  const refundOrder = useMutation({
    mutationFn: ({ orderId, reason }: { orderId: string; reason: string }) => apiFetch<Order>(`/v1/orders/${orderId}/admin/refund`, { method: "POST", body: JSON.stringify({ reason }) }),
    onSuccess: refreshFinancialOrders,
  });
  const queryInvoice = useMutation({
    mutationFn: ({ orderId, reason }: { orderId: string; reason: string }) =>
      apiFetch<AdminInvoiceQueryResult>(`/v1/admin/orders/${orderId}/invoice/query`, {
        method: "POST",
        body: JSON.stringify({ reason }),
      }),
    onSuccess: refreshFinancialOrders,
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

  const orderContent = <section className="admin-orders-panel">
    <SectionHeading eyebrow="訂單營運" title="訂單、付款與發票" />
    {orderQuery.isPending && <LoadingLines count={4} />}
    {orderQuery.isError && <DataState kind="error" title="訂單資料無法讀取" detail={orderQuery.error.message} />}
    <form className="admin-order-search" onSubmit={(event) => { event.preventDefault(); setOrderOffset(0); setOrderSearch(orderSearchInput.trim()); }}>
      <label>搜尋訂單<input value={orderSearchInput} onChange={(event) => setOrderSearchInput(event.target.value)} placeholder="訂單編號、Email 或統編" maxLength={120} /></label>
      <button className="button button-quiet" type="submit">搜尋</button>
    </form>
    <div className="operations-list">
      {orderQuery.data?.items.map((order) => (
        <article key={order.id}>
          <div className="operation-title">
            <span>{formatDate(order.created_at)}</span>
            <strong>{order.order_number}</strong>
            <small>{formatMoney(order.amount_total)}</small>
          </div>
          <div className="operation-state">
            <span><CreditCard size={16} />{adminPaymentStatusLabel(order.payment_status)}</span>
            <span><Package size={16} />{fulfillmentStatusLabel(order.fulfillment_status)}</span>
            <span><FileText size={16} />{invoiceStatusLabel(order.invoice_status)}</span>
            {order.sales_channel !== "meal_preorder" && order.shipment && <span className="system-state"><Truck size={16} />{shippingChannelLabels[order.shipment.channel]} · {order.shipment.status}</span>}
          </div>
          <InvoiceTaxSummary variant="card" items={order.items} memberTaxExempt={order.member_tax_exempt_snapshot} shippingFee={order.sales_channel === "meal_preorder" ? undefined : order.shipment?.shipping_fee} taxAmount={order.tax_amount} />
          <AdminOrderAction
            order={order}
            busy={updateFulfillment.isPending || createLogistics.isPending || advanceShipment.isPending || refundOrder.isPending || queryInvoice.isPending}
            onFulfillment={(status) => updateFulfillment.mutate({ orderId: order.id, status })}
            onCreateLogistics={() => createLogistics.mutate(order.id)}
            onShipment={(status) => advanceShipment.mutate({ orderId: order.id, status })}
            onRefund={(reason) => refundOrder.mutate({ orderId: order.id, reason })}
            onInvoiceQuery={(reason) => queryInvoice.mutate({ orderId: order.id, reason })}
          />
          <AdminInvoiceAdjustment order={order} />
          <AdminPaymentQuery orderId={order.id} />
        </article>
      ))}
    </div>
    {orderQuery.data && <nav className="admin-pagination" aria-label="訂單分頁">
      <button className="button button-quiet" type="button" disabled={orderOffset === 0 || orderQuery.isFetching} onClick={() => setOrderOffset(Math.max(0, orderOffset - 12))}>上一頁</button>
      <span>共 {orderQuery.data.total} 筆 · 第 {Math.floor(orderOffset / 12) + 1} 頁</span>
      <button className="button button-quiet" type="button" disabled={orderOffset + 12 >= orderQuery.data.total || orderQuery.isFetching} onClick={() => setOrderOffset(orderOffset + 12)}>下一頁</button>
    </nav>}
    {orderQuery.data?.total === 0 && <p>沒有符合條件的訂單。</p>}
    {queryInvoice.isSuccess && <p className="form-success">{queryInvoice.data.message}</p>}
    {(updateFulfillment.isError || createLogistics.isError || advanceShipment.isError || refundOrder.isError || queryInvoice.isError) && (
      <p className="form-error">{(updateFulfillment.error || createLogistics.error || advanceShipment.error || refundOrder.error || queryInvoice.error)?.message}</p>
    )}
  </section>;

  const supplierContent = <section className="admin-table-panel">
    <div className="admin-operation-heading"><SectionHeading eyebrow="供應者" title="供應者與審核狀態" /><Link className="button button-quiet" to="/admin/catalog" search={{ tab: "suppliers" }}>管理供應者</Link></div>
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
    <div className="admin-operation-heading"><SectionHeading eyebrow="取貨網絡" title="取貨點維護" /><Link className="button button-quiet" to="/admin/catalog" search={{ tab: "locations" }}>管理取貨點</Link></div>
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
    : section === "issues" ? <section className="admin-issues-panel"><SectionHeading eyebrow="系統狀態" title="問題回報" /><AdminFailurePanel /></section>
      : section === "suppliers" ? supplierContent : locationContent;

  return <>
    <section className="admin-intro admin-operations-intro">
      <div><p className="eyebrow">訂單營運</p><h1>{section === "issues" ? "問題回報" : section === "suppliers" ? "供應者與審核狀態" : section === "locations" ? "取貨點維護" : "訂單、付款與發票"}</h1></div>
      <div className="admin-intro-meta"><span>管理者</span><strong>{user.display_name}</strong></div>
    </section>
    <AdminNav />
    <AdminOperationsNav section={section} />
    <section className="admin-operation-page content-section">{content}</section>
  </>;
}

function AdminPaymentQuery({ orderId }: { orderId: string }) {
  const [reason, setReason] = useState("");
  const queryClient = useQueryClient();
  const query = useMutation({
    mutationFn: () => apiFetch<{ message: string }>(`/v1/admin/orders/${orderId}/payment/query`, {
      method: "POST", body: JSON.stringify({ reason: reason.trim() }),
    }),
    onSuccess: () => Promise.all(["admin-orders", "admin-meal-orders", "admin-meal-operations-summary", "meal-sales-report"].map((key) => queryClient.invalidateQueries({ queryKey: [key] }))),
  });
  return <form className="operation-invoice-query" onSubmit={(event) => { event.preventDefault(); if (reason.trim()) query.mutate(); }}>
    <label>金流查詢原因<input required maxLength={1000} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
    <button type="submit" disabled={query.isPending || !reason.trim()}>重新查詢雷門付款</button>
    {query.isError && <p className="form-error">{query.error.message}</p>}
    {query.isSuccess && <p className="form-success">{query.data.message}</p>}
  </form>;
}

function AdminOrderAction({
  order,
  busy,
  onFulfillment,
  onCreateLogistics,
  onShipment,
  onRefund,
  onInvoiceQuery,
}: {
  order: Order;
  busy: boolean;
  onFulfillment: (status: string) => void;
  onCreateLogistics: () => void;
  onShipment: (status: string) => void;
  onRefund: (reason: string) => void;
  onInvoiceQuery: (reason: string) => void;
}) {
  const [refundOpen, setRefundOpen] = useState(false);
  const [refundReason, setRefundReason] = useState("");
  const [invoiceReason, setInvoiceReason] = useState("");
  const refundControl = order.available_actions.includes("refund") && (refundOpen ? <div className="operation-refund"><input aria-label="退款原因" value={refundReason} onChange={(event) => setRefundReason(event.target.value)} /><button type="button" disabled={busy || !refundReason.trim()} onClick={() => onRefund(refundReason)}>確認退款</button><button type="button" onClick={() => setRefundOpen(false)}>取消</button></div> : <button type="button" disabled={busy} onClick={() => setRefundOpen(true)}>建立退款</button>);
  const invoiceControl = (
    <div className="operation-invoice-query">
      <label>
        發票查詢原因
        <input
          aria-label="發票查詢原因"
          onChange={(event) => setInvoiceReason(event.target.value)}
          placeholder="例如：客服核對逾時訂單"
          value={invoiceReason}
        />
      </label>
      <button
        type="button"
        disabled={busy || !invoiceReason.trim()}
        onClick={() => onInvoiceQuery(invoiceReason.trim())}
      >
        重新查詢電子發票
      </button>
    </div>
  );
  if (order.sales_channel === "meal_preorder") {
    return <div className="operation-actions"><Link className="button button-quiet" to="/admin/meals">便當取餐與退單審核</Link>{invoiceControl}</div>;
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
      {refundControl}
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
