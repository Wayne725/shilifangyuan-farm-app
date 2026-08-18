import {
  ArrowRight,
  Buildings,
  CreditCard,
  MapPin,
  Package,
  SealCheck,
  Truck,
  UsersThree,
} from "@phosphor-icons/react";
import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";

import { DataState, LoadingLines, SectionHeading } from "../components/Shared";
import { AdminNav } from "../components/AdminNav";
import { useAuth } from "../context/AuthContext";
import { apiFetch, formatDate, formatMoney } from "../lib/api";
import { shippingChannelLabels, temperatureLabels } from "../lib/commerce";
import type { Order, PickupLocation, Product, ShippingRate, Supplier } from "../lib/types";

interface MemberRecord {
  id: string;
  status?: string;
}

export function AdminPage() {
  const { user, openLogin } = useAuth();
  const isAdmin = user?.user_role === "admin";
  const queryClient = useQueryClient();
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
        queryKey: ["admin-orders"],
        queryFn: () => apiFetch<Order[]>("/v1/orders"),
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
          <div className="operations-list">
            {orderQuery.data?.slice(0, 12).map((order) => (
              <article key={order.id}>
                <div className="operation-title">
                  <span>{formatDate(order.created_at)}</span>
                  <strong>{order.order_number}</strong>
                  <small>{formatMoney(order.amount_total)}</small>
                </div>
                <div className="operation-state">
                  <span><CreditCard size={16} />{adminPaymentLabel(order.payment_status)}</span>
                  <span><Package size={16} />{adminFulfillmentLabel(order.fulfillment_status)}</span>
                  {order.shipment && <span className="system-state"><Truck size={16} />{shippingChannelLabels[order.shipment.channel]} · {order.shipment.status}</span>}
                </div>
                <AdminOrderAction
                  order={order}
                  busy={updateFulfillment.isPending || createLogistics.isPending || advanceShipment.isPending || refundOrder.isPending}
                  onFulfillment={(status) => updateFulfillment.mutate({ orderId: order.id, status })}
                  onCreateLogistics={() => createLogistics.mutate(order.id)}
                  onShipment={(status) => advanceShipment.mutate({ orderId: order.id, status })}
                  onRefund={(reason) => refundOrder.mutate({ orderId: order.id, reason })}
                />
              </article>
            ))}
          </div>
          {(updateFulfillment.isError || createLogistics.isError || advanceShipment.isError || refundOrder.isError) && (
            <p className="form-error">
              {(updateFulfillment.error || createLogistics.error || advanceShipment.error || refundOrder.error)?.message}
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

function AdminOrderAction({
  order,
  busy,
  onFulfillment,
  onCreateLogistics,
  onShipment,
  onRefund,
}: {
  order: Order;
  busy: boolean;
  onFulfillment: (status: string) => void;
  onCreateLogistics: () => void;
  onShipment: (status: string) => void;
  onRefund: (reason: string) => void;
}) {
  const [refundOpen, setRefundOpen] = useState(false);
  const [refundReason, setRefundReason] = useState("買家申請退款");
  const refundControl = order.available_actions.includes("refund") && (refundOpen ? <div className="operation-refund"><input aria-label="退款原因" value={refundReason} onChange={(event) => setRefundReason(event.target.value)} /><button type="button" disabled={busy || !refundReason.trim()} onClick={() => onRefund(refundReason)}>確認退款</button><button type="button" onClick={() => setRefundOpen(false)}>取消</button></div> : <button type="button" disabled={busy} onClick={() => setRefundOpen(true)}>建立退款</button>);
  if (order.payment_status !== "paid") return <small className="no-action">等待買家付款</small>;
  if (order.fulfillment_status === "pending_confirmation") {
    return <div className="operation-actions"><button type="button" disabled={busy} onClick={() => onFulfillment("preparing")}>進入備貨</button>{refundControl}</div>;
  }
  if (order.fulfillment_method === "ecpay_logistics") {
    if (order.shipment?.status === "ready_to_create") {
      return <div className="operation-actions"><button type="button" disabled={busy} onClick={onCreateLogistics}>建立綠界物流單</button>{refundControl}</div>;
    }
    if (order.shipment?.status === "created") {
      return <div className="operation-actions"><button type="button" disabled={busy} onClick={() => onShipment("in_transit")}>模擬配送中</button>{refundControl}</div>;
    }
    if (order.shipment?.status === "in_transit") {
      return <div className="operation-actions"><button type="button" disabled={busy} onClick={() => onShipment("delivered")}>模擬已送達</button>{refundControl}</div>;
    }
    return <small className="no-action">物流狀態由綠界或 webhook 推進</small>;
  }
  if (order.fulfillment_status === "preparing") {
    return <div className="operation-actions"><button type="button" disabled={busy} onClick={() => onFulfillment("ready_for_pickup")}>標記可領取</button>{refundControl}</div>;
  }
  if (order.fulfillment_status === "ready_for_pickup") {
    return <div className="operation-actions"><button type="button" disabled={busy} onClick={() => onFulfillment("picked_up")}>完成取貨</button>{refundControl}</div>;
  }
  return refundControl || <small className="no-action">本筆已完成</small>;
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

function adminPaymentLabel(status: string): string {
  return { pending: "待付款", paid: "已付款", refunded: "已退款", expired: "已取消" }[status] || status;
}

function adminFulfillmentLabel(status: string): string {
  return { pending_confirmation: "待確認", preparing: "備貨中", ready_for_pickup: "可領取", picked_up: "已完成", cancelled: "已取消" }[status] || status;
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
