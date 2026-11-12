import * as Dialog from "@radix-ui/react-dialog";
import { ArrowClockwise, Buildings, Printer, X } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";

import { AdminNav } from "../components/AdminNav";
import { CloudPrintPanel, cloudPrinterConfigurationQueryKey } from "../components/CloudPrintPanel";
import { DataState, LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import { useAdminPanelAccess } from "../lib/admin-panel-access";
import { adminPlatformQueryKey, useAdminPlatformSettings } from "../lib/admin-platform-settings";
import { financeDateTime, taipeiDate } from "../lib/meal-finance";
import { shiftMealDate } from "../lib/meal-time";
import type { KitchenList, KitchenOrder } from "../lib/meal-kitchen";
import "./AdminKitchenPage.css";

const kitchenStates = { pending_confirmation: "待製作", preparing: "製作中", ready_for_pickup: "待取餐" };
const pageSize = 50;
const privateQueryKeys = [["meal-kitchen"], ["meal-kitchen-ticket"], adminPlatformQueryKey, ["cloud-printer"], cloudPrinterConfigurationQueryKey, ["print-jobs"], ["print-job"]];
const printerConnections = {
  unknown: "尚未確定",
  browser: "瀏覽器列印",
  usb: "USB",
  network: "網路",
  bluetooth: "藍牙",
};

function playKitchenTone(audio: AudioContext, duration: number) {
  const tone = audio.createOscillator(), gain = audio.createGain();
  tone.connect(gain);
  gain.connect(audio.destination);
  tone.frequency.value = 880;
  gain.gain.value = .08;
  tone.start();
  tone.stop(audio.currentTime + duration);
  return tone;
}

export function AdminKitchenPage() {
  const { user, openLogin } = useAuth();
  const access = useAdminPanelAccess(privateQueryKeys);
  const isAdmin = user?.user_role === "admin";
  const [serviceDate, setServiceDate] = useState(taipeiDate());
  const [mealPeriod, setMealPeriod] = useState("all");
  const [search, setSearch] = useState("");
  const [searchDraft, setSearchDraft] = useState("");
  const [fulfillmentStatus, setFulfillmentStatus] = useState("all");
  const [offset, setOffset] = useState(0);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [reprint, setReprint] = useState(false);
  const [printRequested, setPrintRequested] = useState(false);
  const [printSnapshot, setPrintSnapshot] = useState<KitchenOrder | null>(null);
  const queryClient = useQueryClient();
  const audio = useRef<AudioContext | null>(null);
  const observed = useRef<{key: string; ids: Set<string>} | null>(null);
  const [soundEnabled, setSoundEnabled] = useState(false);
  const [soundError, setSoundError] = useState("");
  const [testSoundStatus, setTestSoundStatus] = useState("");
  const retryRead = (count: number) => !access.isDenied() && count < 1;
  const { settings, draft, edit, save, busy, canSave, changed, conflict, versionReady, reload, submit } = useAdminPlatformSettings(access, isAdmin, "管理員更新廚房出單與提示音");
  const kitchenSound = settings.data?.kitchen_sound;
  const parameters = new URLSearchParams({ service_date: serviceDate, meal_period: mealPeriod, search, fulfillment_status: fulfillmentStatus, offset: String(offset), limit: String(pageSize) });
  const list = useQuery({ queryKey: ["meal-kitchen", serviceDate, mealPeriod, search, fulfillmentStatus, offset], queryFn: ({ signal }) => access.request<KitchenList>(`/v1/admin/meal-kitchen?${parameters}`, { signal }), enabled: isAdmin && !access.denied && Boolean(serviceDate), refetchInterval: access.denied ? false : 15_000, retry: retryRead });
  const update = useMutation({
    mutationFn: ({ orderId, status }: { orderId: string; status: string }) => access.request<KitchenOrder>(`/v1/admin/meal-kitchen/orders/${orderId}/status`, { method: "PATCH", body: JSON.stringify({ status }) }),
    retry: false,
    onSuccess: () => { if (!access.isDenied()) return Promise.all(["meal-kitchen", "meal-kitchen-ticket", "admin-meal-orders", "admin-meal-operations-summary", "admin-orders", "meal-orders"].map((key) => queryClient.invalidateQueries({ queryKey: [key] }))); },
    onError: () => { if (!access.isDenied()) void queryClient.invalidateQueries({ queryKey: ["meal-kitchen"] }); },
  });
  const ticket = useQuery({ queryKey: ["meal-kitchen-ticket", previewId], queryFn: ({ signal }) => access.request<KitchenOrder>(`/v1/admin/meal-kitchen/orders/${previewId}/ticket`, { signal }), enabled: isAdmin && !access.denied && previewId !== null, refetchInterval: access.denied ? false : 15_000, retry: retryRead });
  const previewOrder = printSnapshot?.id === previewId ? printSnapshot : ticket.data;
  const print = useMutation({
    mutationFn: (id: string) => access.request<KitchenOrder>(`/v1/admin/meal-kitchen/orders/${id}/ticket`),
    retry: false,
    onSuccess: (fresh) => { if (!access.isDenied()) { queryClient.setQueryData(["meal-kitchen-ticket", fresh.id], fresh); setPrintSnapshot(fresh); } },
  });
  useEffect(() => {
    if (!access.isDenied() && printSnapshot && printSnapshot.id === previewId) {
      window.print();
      setPrintRequested(true);
      setPrintSnapshot(null);
    }
  }, [previewId, printSnapshot, access.isDenied]);
  useEffect(() => {
    if (list.data && offset > 0 && offset >= list.data.total) setOffset(Math.max(0, Math.floor((list.data.total - 1) / pageSize) * pageSize));
  }, [offset, list.data]);
  useEffect(() => {
    if (access.isDenied() || !list.data) return;
    const key = `${serviceDate}:${mealPeriod}:${search}:${fulfillmentStatus}:${offset}`;
    const ids = new Set(list.data.items.map(order => order.id));
    if (soundEnabled && kitchenSound && audio.current?.state === "running" && observed.current?.key === key && [...ids].some(id => !observed.current?.ids.has(id))) playKitchenTone(audio.current, .3);
    observed.current = {key, ids};
  }, [list.data, serviceDate, mealPeriod, search, fulfillmentStatus, offset, soundEnabled, kitchenSound, access.isDenied]);
  useEffect(() => {
    if (!access.denied) return;
    setPreviewId(null); setPrintSnapshot(null); setPrintRequested(false); setSoundEnabled(false);
    observed.current = null;
    void audio.current?.close(); audio.current = null;
  }, [access.denied]);
  useEffect(() => () => { void audio.current?.close(); }, []);
  async function enableSound() {
    if (access.isDenied()) return;
    setSoundError("");
    try {
      const context = audio.current ||= new AudioContext();
      await context.resume();
      if (!access.isDenied()) setSoundEnabled(context.state === "running");
    } catch {
      if (!access.isDenied()) setSoundError("瀏覽器未允許播放提示音");
    }
  }
  async function testSound() {
    let context: AudioContext | undefined;
    try {
      const previewAudio = context = new AudioContext();
      await previewAudio.resume();
      if (access.isDenied()) {
        void previewAudio.close();
        return;
      }
      playKitchenTone(previewAudio, .25).onended = () => void previewAudio.close();
      setTestSoundStatus("已播放測試提示音");
    } catch {
      void context?.close();
      if (!access.isDenied()) setTestSoundStatus("瀏覽器未允許播放提示音");
    }
  }
  const choosePreview = (order: KitchenOrder) => { setPreviewId(order.id); setReprint(false); setPrintRequested(false); setPrintSnapshot(null); print.reset(); };
  const closePreview = () => { setPreviewId(null); setPrintRequested(false); setPrintSnapshot(null); print.reset(); };

  if (!isAdmin) return <section className="admin-gate"><div className="admin-gate-mark"><Buildings size={38} weight="light" /></div><h1>廚房清單只向管理員開放。</h1>{!user ? <button className="button button-primary" type="button" onClick={openLogin}>管理員登入</button> : <Link className="button button-quiet" to="/">返回首頁</Link>}</section>;
  if (access.denied) return <section className="admin-gate"><h1>備餐與出單</h1><p role="alert">管理權限已失效或已變更，請重新驗證後再操作。</p><button className="button button-system" type="button" onClick={() => window.location.reload()}>重新驗證管理權限</button></section>;

  return <section className="admin-module-page meal-kitchen-page">
    <header className="workspace-heading"><div><p className="eyebrow">KITCHEN / 廚房</p><h1>備餐與出單</h1></div></header>
    <AdminNav />
    <details className="kitchen-device-settings">
      <summary>印表機與提示音</summary>
      {settings.isPending ? <LoadingLines count={2} /> : settings.isError ? <DataState title="出單設定無法讀取" detail={settings.error.message} onAction={() => void settings.refetch()} /> :
        <form aria-label="出單設定" onSubmit={event => { event.preventDefault(); submit(); }}>
          <fieldset disabled={busy} aria-label="印表機與提示音設定">
            <div className="field-grid two-columns">
              <label className="field"><span>印表機品牌</span><input maxLength={80} value={draft.printer_brand} onChange={event => edit({ printer_brand: event.target.value })} /></label>
              <label className="field"><span>印表機型號</span><input maxLength={80} value={draft.printer_model} onChange={event => edit({ printer_model: event.target.value })} /></label>
              <label className="field"><span id="kitchen-printer-connection-label">連接方式</span><select aria-labelledby="kitchen-printer-connection-label" value={draft.printer_connection} onChange={event => edit({ printer_connection: event.target.value as keyof typeof printerConnections })}>
                {Object.entries(printerConnections).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select></label>
            </div>
            <p>80mm 備餐單可從瀏覽器列印</p>
            <label className="check-field"><input type="checkbox" checked={draft.kitchen_sound} onChange={event => edit({ kitchen_sound: event.target.checked })} />啟用廚房新訂單提示音</label>
            {testSoundStatus && <p role="status">{testSoundStatus}</p>}
            {changed && <p className="form-error" role="alert">設定已更新。重新讀取會捨棄未儲存變更。</p>}
            {!versionReady && <p className="form-error" role="alert">設定版本尚未確認，請重新讀取後再儲存。</p>}
            {save.isError && <p className="form-error" role="alert">{save.error.message}</p>}
            {save.isSuccess && <p className="form-success" role="status">出單設定已儲存</p>}
            <div className="form-actions">
              <button className="button button-primary" disabled={!canSave}>{save.isPending ? "儲存中…" : "儲存出單設定"}</button>
              <button className="button button-quiet" type="button" onClick={() => void testSound()}>測試提示音</button>
              {(changed || conflict || !versionReady) && <button type="button" className="button button-quiet" disabled={busy} onClick={() => void reload()}>重新讀取最新設定</button>}
            </div>
          </fieldset>
        </form>}
    </details>
    <CloudPrintPanel key={user.id} access={access} adminId={user.id} />
    <form className="meal-kitchen-search" aria-label="備餐訂單查詢" onSubmit={event => {event.preventDefault(); setSearch(searchDraft.trim()); setOffset(0); closePreview();}}>
      <label className="field"><span>查詢便當訂單</span><input type="search" maxLength={100} placeholder="訂單編號、姓名、餐點、取餐地點" value={searchDraft} onChange={event => setSearchDraft(event.target.value)} /></label>
      <label className="field"><span>備餐狀態</span><select aria-label="備餐狀態" value={fulfillmentStatus} onChange={event => {setFulfillmentStatus(event.target.value); setOffset(0); closePreview();}}><option value="all">全部備餐狀態</option>{Object.entries(kitchenStates).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <div className="meal-kitchen-search-actions"><button className="button button-primary" type="submit">查詢</button><button className="button button-quiet" type="button" onClick={() => {setSearch(""); setSearchDraft(""); setFulfillmentStatus("all"); setOffset(0); closePreview();}}>清除</button></div>
    </form>
    {kitchenSound && <button className="button button-quiet kitchen-sound-button" type="button" onClick={() => void enableSound()} disabled={soundEnabled}>{soundEnabled ? "新訂單提示音已啟用" : "開啟本頁新訂單提示音"}</button>}
    {kitchenSound && soundError && <p role="alert">{soundError}</p>}
    <div className="meal-kitchen-filters">
      <div className="admin-quick-buttons" role="group" aria-label="快速備餐日期">{[{ label: "今天", date: taipeiDate() }, { label: "明天", date: shiftMealDate(taipeiDate(), 1) }].map(({ label, date }) => <button className="button button-quiet" key={label} type="button" aria-pressed={serviceDate === date} onClick={() => { setServiceDate(date); setOffset(0); closePreview(); }}>{label}</button>)}</div>
      <label className="field"><span>取餐日期</span><input type="date" required value={serviceDate} onChange={(event) => { setServiceDate(event.target.value); setOffset(0); closePreview(); }} /></label>
      <label className="field"><span>備餐時段</span><select value={mealPeriod} onChange={(event) => { setMealPeriod(event.target.value); setOffset(0); closePreview(); }}><option value="all">全部時段</option><option value="lunch">11:00–13:00</option><option value="dinner">16:30–18:00</option><option value="unspecified">舊場次</option></select></label>
      <button className="button button-quiet" type="button" disabled={list.isFetching || !serviceDate} onClick={() => void list.refetch()}><ArrowClockwise size={18} />更新清單</button>
    </div>
    {!serviceDate ? <DataState title="請選擇取餐日期" detail="選好日期後會顯示廚房備餐清單。" /> : list.isPending ? <LoadingLines count={5} /> : list.isError ? <DataState kind="error" title="廚房清單無法讀取" detail={list.error.message} onAction={() => void list.refetch()} /> : list.data && <>
      <div className="meal-kitchen-summary"><strong>{list.data.total} 筆待處理訂單 · {list.data.meal_quantity} 份</strong><ul>{list.data.products.map((product) => <li key={`${product.name}-${product.unit}-${product.meal_booking_type}-${product.payment_status}`}>{product.name}{product.meal_booking_type === "business" ? `（會餐／${product.payment_status === "paid" ? "已付款" : "未付款"}）` : ""}：{product.quantity} {product.unit}</li>)}</ul></div>
      {update.isError && <p className="form-error" role="alert">{update.error.message}</p>}
      {list.data.total === 0 ? <DataState title="這段日期與餐別沒有待備餐訂單" detail="可切換日期或餐別。已完成取餐的歷史訂單請到便當管理查詢。" /> : <div className="meal-kitchen-orders">{list.data.items.map((order) => <article key={order.id} className="meal-kitchen-order"><header><div><p className="meal-kitchen-pickup">{financeDateTime(order.pickup_at)}{!order.pickup_time_confirmed && "（原場次開始時間）"}</p><h2>{order.order_number}</h2><p>{order.customer_name} · {order.location}</p>{order.contact_phone && <p>電話：{order.contact_phone}</p>}{order.meal_booking_type === "business" && <p className="status-chip">會餐 · {order.payment_status === "paid" ? "已付款" : "未付款"}</p>}</div><span className="status-chip">{kitchenStates[order.fulfillment_status]}</span></header>{order.pending_cancellation && <div className="meal-kitchen-warning"><strong>退單申請待審核</strong>{order.pending_cancellation.reason && <p>{order.pending_cancellation.reason}</p>}<Link to="/admin/meals" search={{ tab: "cancellations", q: order.order_number, order_id: order.id }}>審核這筆退單</Link></div>}<ul className="meal-kitchen-meals">{order.items.map((item, index) => <li key={`${item.name}-${index}`}><strong>{item.name} × {item.quantity} {item.unit}</strong>{item.options.length > 0 && <p>客製：{item.options.map((option) => `${option.group_name}／${option.name}`).join("、")}</p>}</li>)}</ul><p className="meal-kitchen-note"><strong>餐具：</strong>{order.needs_utensils ? "需要餐具" : "不需要餐具"}</p>{order.pickup_address && <p>取餐地址：{order.pickup_address}</p>}{order.kitchen_note && <p className="meal-kitchen-note"><strong>餐點備註：</strong>{order.kitchen_note}</p>}<div className="meal-kitchen-actions">{order.fulfillment_status === "pending_confirmation" && <button className="button button-primary" type="button" disabled={update.isPending} onClick={() => update.mutate({ orderId: order.id, status: "preparing" })}>開始製作</button>}{order.fulfillment_status === "preparing" && <button className="button button-primary" type="button" disabled={update.isPending} onClick={() => update.mutate({ orderId: order.id, status: "ready_for_pickup" })}>標記待取餐</button>}<button className="button button-quiet" type="button" onClick={() => choosePreview(order)}><Printer size={18} />預覽備餐單／補印</button></div></article>)}</div>}
      {list.data.total > 0 && <nav className="admin-pagination" aria-label="廚房清單分頁"><button className="button button-quiet" disabled={offset === 0 || list.isFetching} onClick={() => { setOffset(Math.max(0, offset - pageSize)); closePreview(); }}>上一頁</button><span>第 {Math.floor(offset / pageSize) + 1}／{Math.ceil(list.data.total / pageSize)} 頁</span><button className="button button-quiet" disabled={offset + pageSize >= list.data.total || list.isFetching} onClick={() => { setOffset(offset + pageSize); closePreview(); }}>下一頁</button></nav>}
    </>}

    <Dialog.Root open={previewId !== null} onOpenChange={(open) => { if (!open) closePreview(); }}><Dialog.Portal><Dialog.Overlay className="dialog-overlay" /><Dialog.Content className="meal-kitchen-print-dialog"><div className="meal-kitchen-print-controls"><Dialog.Close className="icon-button" aria-label="關閉備餐單預覽"><X size={20} /></Dialog.Close><Dialog.Title>備餐單預覽</Dialog.Title><Dialog.Description>預覽資料隨清單更新。列印對話框關閉後，請自行確認是否已印出。</Dialog.Description>{ticket.isPending ? <LoadingLines count={2} /> : ticket.isError ? <p role="alert">{ticket.error.message}，請關閉預覽並更新清單。</p> : previewOrder ? <><label className="meal-kitchen-reprint"><input type="checkbox" checked={reprint} onChange={(event) => setReprint(event.target.checked)} />這次是補印（紙上會標示「補印」）</label><button className="button button-primary" type="button" disabled={print.isPending || ticket.isFetching || ticket.isError} onClick={() => { if (previewId) print.mutate(previewId); }}>{print.isPending ? "核對訂單中…" : "開啟列印對話框"}</button>{print.isError && <p className="form-error" role="alert">{print.error.message}，請更新清單。</p>}{printRequested && <p role="status">已要求瀏覽器開啟列印對話框，請確認印表機與紙本結果；系統未記錄為列印成功。</p>}</> : <p role="alert">訂單已移出目前廚房清單，請關閉預覽並重新查詢。</p>}</div>{previewOrder && !ticket.isError && <KitchenTicket order={previewOrder} reprint={reprint} />}</Dialog.Content></Dialog.Portal></Dialog.Root>
  </section>;
}

function KitchenTicket({ order, reprint }: { order: KitchenOrder; reprint: boolean }) {
  return <div className="meal-kitchen-ticket"><h2>十里方圓 · 備餐單</h2>{reprint && <strong className="meal-kitchen-ticket-reprint">補印</strong>}<p className="meal-kitchen-ticket-order">{order.order_number}</p><p>取餐：{financeDateTime(order.pickup_at)}{!order.pickup_time_confirmed && "（場次開始時間）"}</p><p className="meal-kitchen-ticket-customer">訂購者：{order.customer_name}</p><p>{order.location}</p>{order.contact_phone && <p>電話：{order.contact_phone}</p>}{order.meal_booking_type === "business" && <p className="status-chip">會餐 · {order.payment_status === "paid" ? "已付款" : "未付款"}</p>}<p>狀態：{kitchenStates[order.fulfillment_status]}</p>{order.meal_booking_type === "business" && <p>統編：{order.invoice_buyer_tax_id} · 抬頭：{order.invoice_buyer_name}</p>}{order.pending_cancellation && <div className="meal-kitchen-ticket-alert"><strong>注意：退單申請待審</strong>{order.pending_cancellation.reason && <p>{order.pending_cancellation.reason}</p>}</div>}<ul>{order.items.map((item, index) => <li key={index}><strong>{item.name} × {item.quantity} {item.unit}</strong>{item.options.length > 0 && <p>客製：{item.options.map((option) => `${option.group_name}／${option.name}`).join("、")}</p>}</li>)}</ul><p className="meal-kitchen-note"><strong>餐具：</strong>{order.needs_utensils ? "需要餐具" : "不需要餐具"}</p>{order.pickup_address && <p>取餐地址：{order.pickup_address}</p>}{order.kitchen_note && <p className="meal-kitchen-note"><strong>餐點備註：</strong>{order.kitchen_note}</p>}<p>共 {order.items.reduce((quantity, item) => quantity + item.quantity, 0)} 份</p></div>;
}
