import * as Dialog from "@radix-ui/react-dialog";
import { ArrowClockwise, Buildings, Printer, X } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import { AdminNav } from "../components/AdminNav";
import { DataState, LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import { apiFetch } from "../lib/api";
import { financeDateTime, taipeiDate } from "../lib/meal-finance";
import { shiftMealDate } from "../lib/meal-time";
import type { KitchenList, KitchenOrder } from "../lib/meal-kitchen";
import "./AdminKitchenPage.css";

const kitchenStates = { pending_confirmation: "待製作", preparing: "製作中", ready_for_pickup: "待取餐" };
const pageSize = 50;

export function AdminKitchenPage() {
  const { user, openLogin } = useAuth();
  const isAdmin = user?.user_role === "admin";
  const [serviceDate, setServiceDate] = useState(taipeiDate());
  const [mealPeriod, setMealPeriod] = useState("all");
  const [offset, setOffset] = useState(0);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [reprint, setReprint] = useState(false);
  const [printRequested, setPrintRequested] = useState(false);
  const [printSnapshot, setPrintSnapshot] = useState<KitchenOrder | null>(null);
  const queryClient = useQueryClient();
  const parameters = new URLSearchParams({ service_date: serviceDate, meal_period: mealPeriod, offset: String(offset), limit: String(pageSize) });
  const list = useQuery({ queryKey: ["meal-kitchen", serviceDate, mealPeriod, offset], queryFn: () => apiFetch<KitchenList>(`/v1/admin/meal-kitchen?${parameters}`), enabled: isAdmin && Boolean(serviceDate), refetchInterval: 15_000 });
  const update = useMutation({
    mutationFn: ({ orderId, status }: { orderId: string; status: string }) => apiFetch<KitchenOrder>(`/v1/admin/meal-kitchen/orders/${orderId}/status`, { method: "PATCH", body: JSON.stringify({ status }) }),
    onSuccess: () => Promise.all(["meal-kitchen", "meal-kitchen-ticket", "admin-meal-orders", "admin-meal-operations-summary", "admin-orders", "meal-orders"].map((key) => queryClient.invalidateQueries({ queryKey: [key] }))),
    onError: () => void queryClient.invalidateQueries({ queryKey: ["meal-kitchen"] }),
  });
  const ticket = useQuery({ queryKey: ["meal-kitchen-ticket", previewId], queryFn: () => apiFetch<KitchenOrder>(`/v1/admin/meal-kitchen/orders/${previewId}/ticket`), enabled: isAdmin && previewId !== null, refetchInterval: 15_000 });
  const previewOrder = printSnapshot?.id === previewId ? printSnapshot : ticket.data;
  const print = useMutation({
    mutationFn: (id: string) => apiFetch<KitchenOrder>(`/v1/admin/meal-kitchen/orders/${id}/ticket`),
    onSuccess: (fresh) => { queryClient.setQueryData(["meal-kitchen-ticket", fresh.id], fresh); setPrintSnapshot(fresh); },
  });
  useEffect(() => {
    if (printSnapshot && printSnapshot.id === previewId) {
      window.print();
      setPrintRequested(true);
      setPrintSnapshot(null);
    }
  }, [previewId, printSnapshot]);
  useEffect(() => {
    if (list.data && offset > 0 && offset >= list.data.total) setOffset(Math.max(0, Math.floor((list.data.total - 1) / pageSize) * pageSize));
  }, [offset, list.data]);
  const choosePreview = (order: KitchenOrder) => { setPreviewId(order.id); setReprint(false); setPrintRequested(false); setPrintSnapshot(null); print.reset(); };
  const closePreview = () => { setPreviewId(null); setPrintRequested(false); setPrintSnapshot(null); print.reset(); };

  if (!isAdmin) return <section className="admin-gate"><div className="admin-gate-mark"><Buildings size={38} weight="light" /></div><h1>廚房清單只向管理員開放。</h1>{!user ? <button className="button button-primary" type="button" onClick={openLogin}>管理員登入</button> : <Link className="button button-quiet" to="/">返回首頁</Link>}</section>;

  return <section className="admin-module-page meal-kitchen-page">
    <header className="workspace-heading"><div><p className="eyebrow">KITCHEN / 廚房</p><h1>備餐與出單</h1></div></header>
    <AdminNav />
    <div className="meal-kitchen-filters"><div className="admin-quick-buttons" role="group" aria-label="快速備餐日期">{[{ label: "今天", date: taipeiDate() }, { label: "明天", date: shiftMealDate(taipeiDate(), 1) }].map(({ label, date }) => <button className="button button-quiet" key={label} type="button" aria-pressed={serviceDate === date} onClick={() => { setServiceDate(date); setOffset(0); closePreview(); }}>{label}</button>)}</div><label className="field"><span>取餐日期</span><input type="date" required value={serviceDate} onChange={(event) => { setServiceDate(event.target.value); setOffset(0); closePreview(); }} /></label><button className="button button-quiet" type="button" disabled={list.isFetching || !serviceDate} onClick={() => void list.refetch()}><ArrowClockwise size={18} />更新清單</button></div>
    <div className="admin-quick-buttons" role="group" aria-label="備餐時段">{[{ value: "all", label: "全部時段" }, { value: "lunch", label: "11:00–13:00" }, { value: "dinner", label: "16:30–18:00" }, { value: "unspecified", label: "舊場次" }].map(({ value, label }) => <button className="button button-quiet" key={value} type="button" aria-pressed={mealPeriod === value} onClick={() => { setMealPeriod(value); setOffset(0); closePreview(); }}>{label}</button>)}</div>
    {!serviceDate ? <DataState title="請選擇取餐日期" detail="選好日期後會顯示廚房備餐清單。" /> : list.isPending ? <LoadingLines count={5} /> : list.isError ? <DataState kind="error" title="廚房清單無法讀取" detail={list.error.message} onAction={() => void list.refetch()} /> : list.data && <>
      <div className="meal-kitchen-summary"><strong>{list.data.total} 筆待處理訂單 · {list.data.meal_quantity} 份</strong><ul>{list.data.products.map((product) => <li key={`${product.name}-${product.unit}`}>{product.name}：{product.quantity} {product.unit}</li>)}</ul></div>
      {update.isError && <p className="form-error" role="alert">{update.error.message}</p>}
      {list.data.total === 0 ? <DataState title="這段日期與餐別沒有待備餐訂單" detail="可切換日期或餐別。已完成取餐的歷史訂單請到便當管理查詢。" /> : <div className="meal-kitchen-orders">{list.data.items.map((order) => <article key={order.id} className="meal-kitchen-order"><header><div><p className="meal-kitchen-pickup">{financeDateTime(order.pickup_at)}{!order.pickup_time_confirmed && "（原場次開始時間）"}</p><h2>{order.order_number}</h2><p>{order.customer_name} · {order.location}</p></div><span className="status-chip">{kitchenStates[order.fulfillment_status]}</span></header>{order.pending_cancellation && <div className="meal-kitchen-warning"><strong>退單申請待審核</strong><p>{order.pending_cancellation.reason}</p><Link to="/admin/meals" search={{ tab: "cancellations", q: order.order_number }}>審核這筆退單</Link></div>}<ul className="meal-kitchen-meals">{order.items.map((item, index) => <li key={`${item.name}-${index}`}><strong>{item.name} × {item.quantity} {item.unit}</strong>{item.options.length > 0 && <p>客製：{item.options.map((option) => `${option.group_name}／${option.name}`).join("、")}</p>}</li>)}</ul><div className="meal-kitchen-actions">{order.fulfillment_status === "pending_confirmation" && <button className="button button-primary" type="button" disabled={update.isPending} onClick={() => update.mutate({ orderId: order.id, status: "preparing" })}>開始製作</button>}{order.fulfillment_status === "preparing" && <button className="button button-primary" type="button" disabled={update.isPending} onClick={() => update.mutate({ orderId: order.id, status: "ready_for_pickup" })}>標記待取餐</button>}<button className="button button-quiet" type="button" onClick={() => choosePreview(order)}><Printer size={18} />預覽備餐單／補印</button></div></article>)}</div>}
      {list.data.total > 0 && <nav className="admin-pagination" aria-label="廚房清單分頁"><button className="button button-quiet" disabled={offset === 0 || list.isFetching} onClick={() => { setOffset(Math.max(0, offset - pageSize)); closePreview(); }}>上一頁</button><span>第 {Math.floor(offset / pageSize) + 1}／{Math.ceil(list.data.total / pageSize)} 頁</span><button className="button button-quiet" disabled={offset + pageSize >= list.data.total || list.isFetching} onClick={() => { setOffset(offset + pageSize); closePreview(); }}>下一頁</button></nav>}
    </>}
    <p className="meal-kitchen-printer-note">目前提供 80mm 備餐單與瀏覽器列印。出單機型號確定後再串接自動出單；請在列印設定選擇紙張尺寸並確認紙本結果。</p>
    <Dialog.Root open={previewId !== null} onOpenChange={(open) => { if (!open) closePreview(); }}><Dialog.Portal><Dialog.Overlay className="dialog-overlay" /><Dialog.Content className="meal-kitchen-print-dialog"><div className="meal-kitchen-print-controls"><Dialog.Close className="icon-button" aria-label="關閉備餐單預覽"><X size={20} /></Dialog.Close><Dialog.Title>備餐單預覽</Dialog.Title><Dialog.Description>預覽資料隨清單更新。列印對話框關閉後，請自行確認是否已印出。</Dialog.Description>{ticket.isPending ? <LoadingLines count={2} /> : ticket.isError ? <p role="alert">{ticket.error.message}，請關閉預覽並更新清單。</p> : previewOrder ? <><label className="meal-kitchen-reprint"><input type="checkbox" checked={reprint} onChange={(event) => setReprint(event.target.checked)} />這次是補印（紙上會標示「補印」）</label><button className="button button-primary" type="button" disabled={print.isPending || ticket.isFetching || ticket.isError} onClick={() => { if (previewId) print.mutate(previewId); }}>{print.isPending ? "核對訂單中…" : "開啟列印對話框"}</button>{print.isError && <p className="form-error" role="alert">{print.error.message}，請更新清單。</p>}{printRequested && <p role="status">已要求瀏覽器開啟列印對話框，請確認印表機與紙本結果；系統未記錄為列印成功。</p>}</> : <p role="alert">訂單已移出目前廚房清單，請關閉預覽並重新查詢。</p>}</div>{previewOrder && !ticket.isError && <KitchenTicket order={previewOrder} reprint={reprint} />}</Dialog.Content></Dialog.Portal></Dialog.Root>
  </section>;
}

function KitchenTicket({ order, reprint }: { order: KitchenOrder; reprint: boolean }) {
  return <div className="meal-kitchen-ticket"><h2>十里方圓 · 備餐單</h2>{reprint && <strong className="meal-kitchen-ticket-reprint">補印</strong>}<p className="meal-kitchen-ticket-order">{order.order_number}</p><p>取餐：{financeDateTime(order.pickup_at)}{!order.pickup_time_confirmed && "（場次開始時間）"}</p><p>{order.customer_name} · {order.location}</p><p>狀態：{kitchenStates[order.fulfillment_status]}</p>{order.pending_cancellation && <div className="meal-kitchen-ticket-alert"><strong>注意：退單申請待審</strong><p>{order.pending_cancellation.reason}</p></div>}<ul>{order.items.map((item, index) => <li key={index}><strong>{item.name} × {item.quantity} {item.unit}</strong>{item.options.length > 0 && <p>客製：{item.options.map((option) => `${option.group_name}／${option.name}`).join("、")}</p>}</li>)}</ul><p>共 {order.items.reduce((quantity, item) => quantity + item.quantity, 0)} 份</p></div>;
}
