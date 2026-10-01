import {
  ArrowRight,
  Buildings,
  CreditCard,
  FileText,
  MapPin,
  Package,
  SealCheck,
  Truck,
  UsersThree,
} from "@phosphor-icons/react";
import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState, type ReactNode } from "react";

import { DataState, LoadingLines, SectionHeading } from "../components/Shared";
import { AdminNav } from "../components/AdminNav";
import { AdminFailurePanel } from "../components/AdminFailurePanel";
import { AdminInvoiceAdjustment } from "../components/AdminInvoiceAdjustment";
import { useAuth } from "../context/AuthContext";
import { apiFetch, formatDate, formatMoney } from "../lib/api";
import { shippingChannelLabels, temperatureLabels } from "../lib/commerce";
import {
  adminPaymentStatusLabel,
  fulfillmentStatusLabel,
  invoiceStatusLabel,
} from "../lib/labels";
import type {
  AdminInvoiceQueryResult,
  Order,
  PickupLocation,
  Product,
  ShippingRate,
  Supplier,
} from "../lib/types";

interface MemberRecord {
  id: string;
  status?: string;
}

interface AdminOrderPage {
  items: Order[];
  total: number;
  limit: number;
  offset: number;
}

export function AdminPage() {
  const { user, openLogin } = useAuth();
  const isAdmin = user?.user_role === "admin";
  const queryClient = useQueryClient();
  const [orderSearchInput, setOrderSearchInput] = useState("");
  const [orderSearch, setOrderSearch] = useState("");
  const [orderOffset, setOrderOffset] = useState(0);
  const [supplierQuery, locationQuery, productQuery, memberQuery, orderQuery, rateQuery] = useQueries({
    queries: [
      {
        queryKey: ["admin-suppliers"],
        queryFn: () => apiFetch<Supplier[]>("/v1/admin/suppliers"),
        enabled: isAdmin,
      },
      {
        queryKey: ["admin-pickup-locations"],
        queryFn: () => apiFetch<PickupLocation[]>("/v1/admin/pickup-locations"),
        enabled: isAdmin,
      },
      {
        queryKey: ["admin-products"],
        queryFn: () => apiFetch<Product[]>("/v1/products"),
        enabled: isAdmin,
      },
      {
        queryKey: ["admin-members"],
        queryFn: () => apiFetch<MemberRecord[]>("/v1/admin/members"),
        enabled: isAdmin,
      },
      {
        queryKey: ["admin-orders", orderSearch, orderOffset],
        queryFn: () => apiFetch<AdminOrderPage>(`/v1/admin/order-search?q=${encodeURIComponent(orderSearch)}&offset=${orderOffset}&limit=12`),
        enabled: isAdmin,
      },
      {
        queryKey: ["admin-shipping-rates"],
        queryFn: () => apiFetch<ShippingRate[]>("/v1/admin/shipping-rates?include_inactive=true"),
        enabled: isAdmin,
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
  const refundOrder = useMutation({
    mutationFn: ({ orderId, reason }: { orderId: string; reason: string }) => apiFetch<Order>(`/v1/orders/${orderId}/admin/refund`, { method: "POST", body: JSON.stringify({ reason }) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-orders"] }),
  });
  const queryInvoice = useMutation({
    mutationFn: ({ orderId, reason }: { orderId: string; reason: string }) =>
      apiFetch<AdminInvoiceQueryResult>(`/v1/admin/orders/${orderId}/invoice/query`, {
        method: "POST",
        body: JSON.stringify({ reason }),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-orders"] }),
  });

  if (!isAdmin) {
    return (
      <section className="admin-gate">
        <div className="admin-gate-mark"><Buildings size={38} weight="light" /></div>
        <p className="eyebrow">ROLE-GATED WORKBENCH</p>
        <h1>管理工作台只向管理者開放。</h1>
        <p>請使用管理者帳號登入。</p>
        {!user && <button className="button button-primary" type="button" onClick={openLogin}>管理者登入</button>}
        {user && <Link className="button button-quiet" to="/social">返回社務系統</Link>}
      </section>
    );
  }

  const activeSuppliers = supplierQuery.data?.filter((supplier) => supplier.is_active).length;
  const activeProducts = productQuery.data?.filter((product) => product.is_active).length;
  const activeLocations = locationQuery.data?.filter((location) => location.is_active).length;
  const activeMembers = memberQuery.data?.filter((member) => member.status === "active").length;

  return (
    <>
      <section className="admin-intro">
        <div>
          <p className="eyebrow">OPERATIONS / 管理工作台</p>
          <h1>把真實營運資料，<br />整理成可行動的工作。</h1>
        </div>
        <div className="admin-intro-meta">
          <span>登入身分</span>
          <strong>{user.display_name}</strong>
          <small>{user.email}</small>
        </div>
      </section>

      <AdminNav />
      <AdminFailurePanel />

      <section className="admin-metrics">
        <Metric icon={Package} label="上架商品" value={activeProducts} />
        <Metric icon={SealCheck} label="有效供應者" value={activeSuppliers} />
        <Metric icon={MapPin} label="啟用取貨點" value={activeLocations} />
        <Metric icon={UsersThree} label="有效社員" value={activeMembers} />
      </section>

      <section className="admin-layout content-section">
        <div className="admin-table-panel">
          <SectionHeading
            eyebrow="SUPPLIER ACCREDITATION"
            title="供應者與審核狀態"
            description="個人與帳務資料僅在授權操作中顯示。"
          />
          {supplierQuery.isPending && <LoadingLines count={4} />}
          {supplierQuery.isError && (
            <DataState kind="error" title="供應者資料無法讀取" detail={supplierQuery.error.message} />
          )}
          <div className="data-table" role="table" aria-label="供應者清單">
            <div className="data-row data-head" role="row">
              <span>供應者</span><span>編號</span><span>認可日期</span><span>狀態</span>
            </div>
            {supplierQuery.data?.map((supplier) => (
              <div className="data-row" role="row" key={supplier.id}>
                <strong>{supplier.business_name}</strong>
                <span>{supplier.supplier_number || "待審核"}</span>
                <span>{supplier.accredited_on ? formatDate(supplier.accredited_on) : "—"}</span>
                <span className={supplier.is_active ? "status-active" : "status-pending"}>
                  {supplier.is_active ? "有效" : "未啟用"}
                </span>
              </div>
            ))}
          </div>
        </div>

        <aside className="admin-side-panel">
          <p className="eyebrow">PICKUP NETWORK</p>
          <h2>取貨點維護</h2>
          {locationQuery.isPending && <LoadingLines count={3} />}
          <div className="location-list">
            {locationQuery.data?.map((location) => (
              <article key={location.id}>
                <MapPin size={19} weight="light" />
                <div><strong>{location.name}</strong><small>{location.code}</small></div>
                <span>{location.is_active ? "啟用" : "停用"}</span>
              </article>
            ))}
          </div>
          <div className="admin-next">
            <span>社務管理</span>
            <p>處理入社、活動、社員提案、願望與會議。</p>
            <Link to="/admin/social">進入管理 <ArrowRight size={21} weight="light" /></Link>
          </div>
        </aside>
      </section>

      <section className="admin-commerce content-section">
        <div className="admin-orders-panel">
          <SectionHeading
            eyebrow="PAYMENT & LOGISTICS"
            title="訂單、付款與配送"
            description="依序處理付款、備貨、物流建立與配送進度。"
          />
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
                  {order.shipment && <span className="system-state"><Truck size={16} />{shippingChannelLabels[order.shipment.channel]} · {order.shipment.status}</span>}
                </div>
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
            <p className="form-error">
              {(updateFulfillment.error || createLogistics.error || advanceShipment.error || refundOrder.error || queryInvoice.error)?.message}
            </p>
          )}
        </div>

        <aside className="shipping-rate-panel">
          <p className="eyebrow">RATE TABLE</p>
          <h2>現行運費</h2>
          {rateQuery.isPending && <LoadingLines count={4} />}
          <ShippingRateManager rates={rateQuery.data || []} onDone={() => queryClient.invalidateQueries({ queryKey: ["admin-shipping-rates"] })} />
        </aside>
      </section>
    </>
  );
}

function AdminPaymentQuery({ orderId }: { orderId: string }) {
  const [reason, setReason] = useState("");
  const queryClient = useQueryClient();
  const query = useMutation({
    mutationFn: () => apiFetch<{ message: string }>(`/v1/admin/orders/${orderId}/payment/query`, {
      method: "POST", body: JSON.stringify({ reason: reason.trim() }),
    }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-orders"] }),
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

function ShippingRateManager({ rates, onDone }: { rates: ShippingRate[]; onDone: () => void }) {
  const [channel, setChannel] = useState<ShippingRate["channel"]>("home_delivery");
  const [temperature, setTemperature] = useState<ShippingRate["temperature"]>("ambient");
  const [fee, setFee] = useState(160);
  const [threshold, setThreshold] = useState(1500);
  const [effectiveFrom, setEffectiveFrom] = useState(new Date().toISOString().slice(0, 10));
  const [editingId, setEditingId] = useState("");
  const create = useMutation({ mutationFn: () => apiFetch<ShippingRate>("/v1/admin/shipping-rates", { method: "POST", body: JSON.stringify({ channel, temperature, fee, free_shipping_threshold: threshold, effective_from: effectiveFrom }) }), onSuccess: onDone });
  const update = useMutation({ mutationFn: (rate: ShippingRate) => apiFetch<ShippingRate>(`/v1/admin/shipping-rates/${rate.id}`, { method: "PATCH", body: JSON.stringify({ fee, free_shipping_threshold: threshold, is_active: true }) }), onSuccess: () => { setEditingId(""); onDone(); } });
  const deactivate = useMutation({ mutationFn: (rateId: string) => apiFetch<void>(`/v1/admin/shipping-rates/${rateId}`, { method: "DELETE" }), onSuccess: onDone });
  const beginEdit = (rate: ShippingRate) => { setEditingId(rate.id); setFee(rate.fee); setThreshold(rate.free_shipping_threshold); };
  return <><div className="rate-list">{rates.map((rate) => <article className={rate.is_active ? "" : "inactive"} key={rate.id}><div><strong>{shippingChannelLabels[rate.channel]}</strong><small>{temperatureLabels[rate.temperature]} · {rate.is_active ? "啟用" : "停用"}</small></div>{editingId === rate.id ? <div className="rate-inline-edit"><input aria-label="運費" min={0} type="number" value={fee} onChange={(event) => setFee(Number(event.target.value))} /><input aria-label="免運門檻" min={0} type="number" value={threshold} onChange={(event) => setThreshold(Number(event.target.value))} /><button type="button" disabled={update.isPending} onClick={() => update.mutate(rate)}>儲存</button></div> : <><span>{formatMoney(rate.fee)}</span><small>滿 {formatMoney(rate.free_shipping_threshold)} 免運</small><div className="rate-actions"><button type="button" onClick={() => beginEdit(rate)}>編輯</button>{rate.is_active && <button type="button" disabled={deactivate.isPending} onClick={() => deactivate.mutate(rate.id)}>停用</button>}</div></>}</article>)}</div><form className="rate-create" onSubmit={(event) => { event.preventDefault(); create.mutate(); }}><strong>新增運費</strong><select value={channel} onChange={(event) => setChannel(event.target.value as typeof channel)}>{Object.entries(shippingChannelLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><select value={temperature} onChange={(event) => setTemperature(event.target.value as typeof temperature)}>{Object.entries(temperatureLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><input aria-label="運費" min={0} type="number" value={fee} onChange={(event) => setFee(Number(event.target.value))} /><input aria-label="免運門檻" min={0} type="number" value={threshold} onChange={(event) => setThreshold(Number(event.target.value))} /><input aria-label="生效日期" type="date" value={effectiveFrom} onChange={(event) => setEffectiveFrom(event.target.value)} /><button className="button button-system" disabled={create.isPending}>新增費率</button></form>{(create.isError || update.isError || deactivate.isError) && <p className="form-error">{create.error?.message || update.error?.message || deactivate.error?.message}</p>}<p className="rate-note">結帳時依通路、溫層與生效日期套用費率。</p></>;
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
