import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { MembershipEducationSettings } from "../components/MembershipEducationSettings";
import { AdminNav } from "../components/AdminNav";
import { DataState, LoadingLines } from "../components/Shared";
import type { StorefrontStatus } from "../components/StorefrontStatus";
import { useAuth } from "../context/AuthContext";
import { ApiError, apiFetch } from "../lib/api";
import "./AdminSettingsPage.css";

interface PlatformSettings extends StorefrontStatus { version?: number; printer_brand: string; printer_model: string; printer_connection: "unknown" | "browser" | "usb" | "network" | "bluetooth"; kitchen_sound: boolean; invoice_provider?: string; payment_provider?: string; }
const defaults: PlatformSettings = { orders_open: true, support_phone: "", closed_message: "目前暫停接受新訂單", printer_brand: "", printer_model: "", printer_connection: "unknown", kitchen_sound: false };
const editableKeys = ["orders_open", "support_phone", "closed_message", "printer_brand", "printer_model", "printer_connection", "kitchen_sound"] as const;
type SettingsDraft = Pick<PlatformSettings, typeof editableKeys[number]>;
const asDraft = (value: PlatformSettings): SettingsDraft => Object.fromEntries(editableKeys.map(key => [key, value[key] ?? defaults[key]])) as SettingsDraft;
export function AdminSettingsPage() {
  const { user, openLogin } = useAuth(), client = useQueryClient();
  const isAdmin = user?.user_role === "admin";
  const settings = useQuery({ queryKey: ["admin-platform"], queryFn: () => apiFetch<PlatformSettings>("/v1/admin/platform"), enabled: isAdmin });
  const [draft, setDraft] = useState(() => asDraft(defaults));
  const [baseline, setBaseline] = useState(() => ({ draft: asDraft(defaults), version: 0 }));
  const [reloading, setReloading] = useState(false);
  const [soundStatus, setSoundStatus] = useState("");
  const dirty = JSON.stringify(draft) !== JSON.stringify(baseline.draft);
  const versionReady = Number.isInteger(settings.data?.version) && (settings.data?.version ?? -1) >= 0;
  const save = useMutation({
    mutationFn: (value: { draft: SettingsDraft; version: number }) => apiFetch<PlatformSettings>("/v1/admin/platform", { method: "PUT", body: JSON.stringify({ ...value.draft, expected_version: value.version, reason: "管理員更新平台接單與設備設定" }) }),
    onSuccess: (value) => {
      client.setQueryData(["admin-platform"], value);
      setDraft(asDraft(value));
      setBaseline({ draft: asDraft(value), version: value.version ?? 0 });
      void client.invalidateQueries({ queryKey: ["public-platform"] });
    },
  });
  useEffect(() => {
    if (settings.data && !dirty && !save.isPending) {
      setDraft(asDraft(settings.data));
      setBaseline({ draft: asDraft(settings.data), version: settings.data.version ?? 0 });
    }
  }, [settings.data, dirty, save.isPending]);
  const conflict = save.error instanceof ApiError && save.error.status === 409;
  const changed = dirty && versionReady && settings.data?.version !== baseline.version;
  const busy = save.isPending || reloading;
  async function reload() {
    setReloading(true);
    try {
      const result = await settings.refetch();
      if (result.data && !result.isError) {
        setDraft(asDraft(result.data));
        setBaseline({ draft: asDraft(result.data), version: result.data.version ?? 0 });
        save.reset();
      }
    } finally { setReloading(false); }
  }
  async function sound() {
    const audio = new AudioContext();
    try { await audio.resume(); const oscillator = audio.createOscillator(), gain = audio.createGain(); oscillator.connect(gain); gain.connect(audio.destination); oscillator.frequency.value = 880; gain.gain.value = .08; oscillator.start(); oscillator.stop(audio.currentTime + .25); oscillator.onended = () => void audio.close(); setSoundStatus("已播放測試提示音"); }
    catch { await audio.close(); setSoundStatus("瀏覽器未允許播放提示音"); }
  }
  if (!isAdmin) return <section className="admin-gate"><h1>平台設定</h1>{!user && <button className="button button-primary" type="button" onClick={openLogin}>管理員登入</button>}</section>;
  return <section className="admin-module-page admin-settings-page"><header className="workspace-heading"><div><p className="eyebrow">PLATFORM / 平台設定</p><h1>平台與設備</h1></div></header><AdminNav />
    {settings.isPending ? <LoadingLines count={5} /> : settings.isError ? <DataState title="設定無法讀取" detail={settings.error.message} onAction={() => void settings.refetch()} /> : <form onChange={() => { if (!conflict) save.reset(); }} onSubmit={event => { event.preventDefault(); if (!busy && !conflict && !changed && versionReady) save.mutate({ draft, version: baseline.version }); }}><fieldset className="admin-settings-fields" disabled={busy} aria-label="平台設定欄位">
      <section className="admin-settings-section"><p className="eyebrow">ORDERING / 接單</p><h2>平台接單開關</h2><p className="status-chip">目前：{settings.data?.orders_open ? "開放接單" : "暫停新訂單"}</p><label className="check-field"><input type="checkbox" checked={draft.orders_open} onChange={event => setDraft({ ...draft, orders_open: event.target.checked })} />接受新訂單</label><label className="field"><span>暫停接單顯示文字</span><input required maxLength={200} value={draft.closed_message} onChange={event => setDraft({ ...draft, closed_message: event.target.value })} /></label><dl><div><dt>銷售範圍</dt><dd>{settings.data?.sales_scope ? ({ all: "全品項", pickup: "一般商品、共同購買、便當自取", meals_only: "僅便當" }[settings.data.sales_scope]) : "尚未提供範圍資訊"}</dd></div><div><dt>配送政策</dt><dd>{settings.data?.shipping_enabled == null ? "依結帳頁可用方式" : settings.data.shipping_enabled ? "已開放" : "未開放"}</dd></div><div><dt>隔日便當截單</dt><dd>每天晚上 20:00（台灣時間）</dd></div></dl></section>
      <section className="admin-settings-section"><p className="eyebrow">CONTACT / 聯絡資訊</p><h2>客服與退單電話</h2><label className="field"><span>客服電話（退單聯絡）</span><input type="tel" autoComplete="tel" maxLength={40} value={draft.support_phone || ""} onChange={event => setDraft({ ...draft, support_phone: event.target.value })} /></label></section>
      <section className="admin-settings-section"><p className="eyebrow">DEVICES / 設備管理</p><h2>出單與提示音</h2><div className="field-grid two-columns"><label className="field"><span>印表機品牌</span><input maxLength={80} value={draft.printer_brand} onChange={event => setDraft({ ...draft, printer_brand: event.target.value })} /></label><label className="field"><span>印表機型號</span><input maxLength={80} value={draft.printer_model} onChange={event => setDraft({ ...draft, printer_model: event.target.value })} /></label><label className="field"><span>連接方式</span><select value={draft.printer_connection} onChange={event => setDraft({ ...draft, printer_connection: event.target.value as PlatformSettings["printer_connection"] })}><option value="unknown">尚未確定</option><option value="browser">瀏覽器列印</option><option value="usb">USB</option><option value="network">網路</option><option value="bluetooth">藍牙</option></select></label></div><dl><div><dt>80mm 備餐單</dt><dd>瀏覽器列印可用</dd></div></dl><label className="check-field"><input type="checkbox" checked={draft.kitchen_sound} onChange={event => setDraft({ ...draft, kitchen_sound: event.target.checked })} />啟用廚房新訂單提示音</label><div className="form-actions"><button type="button" className="button button-quiet" onClick={() => void sound()}>測試提示音</button><Link className="button button-quiet" to="/admin/kitchen">廚房出單與補印</Link></div>{soundStatus && <p role="status">{soundStatus}</p>}</section>
      <section className="admin-settings-section"><p className="eyebrow">INVOICE / 電子發票</p><h2>發票設定</h2><dl><div><dt>發票供應商</dt><dd>{settings.data?.invoice_provider === "fanyu" ? "汎宇雲端發票" : settings.data?.invoice_provider || "未設定"}</dd></div><div><dt>開立規則</dt><dd>確認付款成功後開立</dd></div><div><dt>字軌與配號</dt><dd>由發票供應商管理</dd></div></dl><Link className="button button-quiet" to="/admin/invoices">發票開立與查詢</Link></section>
      {changed && <p className="form-error" role="alert">設定已更新。重新讀取會捨棄未儲存變更。</p>}
      {!versionReady && <p className="form-error" role="alert">設定版本尚未確認，請重新讀取後再儲存。</p>}
      {save.isError && <p className="form-error" role="alert">{save.error.message}</p>}{save.isSuccess && <p className="form-success" role="status">設定已儲存</p>}<div className="form-actions"><button className="button button-primary" disabled={busy || conflict || changed || !versionReady}>{save.isPending ? "儲存中…" : "儲存平台設定"}</button>{(changed || conflict || !versionReady) && <button type="button" className="button button-quiet" disabled={busy} onClick={() => void reload()}>重新讀取最新設定</button>}</div>
    </fieldset></form>}
    <MembershipEducationSettings />
  </section>;
}
