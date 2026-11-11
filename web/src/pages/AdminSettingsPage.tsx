import { Link } from "@tanstack/react-router";
import { AdminNav } from "../components/AdminNav";
import { PublicInformationFields } from "../components/PublicInformationFields";
import { DataState, LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import { useAdminPanelAccess } from "../lib/admin-panel-access";
import { adminPlatformQueryKey, useAdminPlatformSettings } from "../lib/admin-platform-settings";
import { invoiceProviderNames } from "../lib/labels";
import "./AdminSettingsPage.css";

const privateQueryKeys = [adminPlatformQueryKey];

export function AdminSettingsPage() {
  const { user, openLogin } = useAuth();
  const isAdmin = user?.user_role === "admin";
  const access = useAdminPanelAccess(privateQueryKeys);
  const { settings, draft, edit, save, busy, canSave, changed, conflict, versionReady, reload, submit } = useAdminPlatformSettings(access, isAdmin, "管理員更新平台設定");

  if (!isAdmin) return <section className="admin-gate"><h1>平台設定</h1>{!user && <button className="button button-primary" type="button" onClick={openLogin}>管理員登入</button>}</section>;
  if (access.denied) return <section className="admin-gate"><h1>平台設定</h1><p role="alert">管理權限已失效或已變更，請重新驗證後再操作。</p><button className="button button-system" type="button" onClick={() => window.location.reload()}>重新驗證管理權限</button></section>;

  const invoiceProvider = settings.data?.invoice_provider;
  return <section className="admin-module-page admin-settings-page">
    <header className="workspace-heading"><div><p className="eyebrow">PLATFORM / 平台</p><h1>平台設定</h1></div></header>
    <AdminNav />
    {settings.isPending ? <LoadingLines count={5} /> : settings.isError ? <DataState title="設定無法讀取" detail={settings.error.message} onAction={() => void settings.refetch()} /> :
      <form onSubmit={event => { event.preventDefault(); submit(); }}>
        <fieldset className="admin-settings-fields" disabled={busy} aria-label="平台設定欄位">
          <section className="admin-settings-section">
            <p className="eyebrow">ORDERING / 接單</p><h2>平台接單開關</h2>
            <p className="status-chip">目前：{settings.data.orders_open ? "開放接單" : "暫停新訂單"}</p>
            <label className="check-field"><input type="checkbox" checked={draft.orders_open} onChange={event => edit({ orders_open: event.target.checked })} />接受新訂單</label>
            <label className="field"><span>暫停接單顯示文字</span><input required maxLength={200} value={draft.closed_message} onChange={event => edit({ closed_message: event.target.value })} /></label>
            <dl>
              <div><dt>銷售範圍</dt><dd>{settings.data.sales_scope ? ({ all: "全品項", pickup: "一般商品、共同購買、便當自取", meals_only: "僅便當" }[settings.data.sales_scope]) : "尚未提供範圍資訊"}</dd></div>
              <div><dt>配送政策</dt><dd>{settings.data.shipping_enabled == null ? "依結帳頁可用方式" : settings.data.shipping_enabled ? "已開放" : "未開放"}</dd></div>
              <div><dt>隔日便當截單</dt><dd>每天晚上 20:00（台灣時間）</dd></div>
            </dl>
          </section>
          <section className="admin-settings-section">
            <p className="eyebrow">CONTACT / 聯絡資訊</p><h2>客服與退單電話</h2>
            <label className="field"><span>客服電話（退單聯絡）</span><input type="tel" autoComplete="tel" maxLength={40} value={draft.support_phone} onChange={event => edit({ support_phone: event.target.value })} /></label>
          </section>
          <PublicInformationFields value={draft.public_information} onChange={value => edit({ public_information: value })} />
          <section className="admin-settings-section">
            <p className="eyebrow">INVOICE / 電子發票</p><h2>發票設定</h2>
            <dl>
              <div><dt>發票供應商</dt><dd>{invoiceProvider ? invoiceProviderNames[invoiceProvider] ?? "未識別的供應商" : "未設定"}</dd></div>
              <div><dt>開立規則</dt><dd>確認付款成功後開立</dd></div>
              <div><dt>字軌與配號</dt><dd>由發票供應商管理</dd></div>
            </dl>
            <Link className="button button-quiet" to="/admin/invoices">發票開立與查詢</Link>
          </section>
          {changed && <p className="form-error" role="alert">設定已更新。重新讀取會捨棄未儲存變更。</p>}
          {!versionReady && <p className="form-error" role="alert">設定版本尚未確認，請重新讀取後再儲存。</p>}
          {save.isError && <p className="form-error" role="alert">{save.error.message}</p>}
          {save.isSuccess && <p className="form-success" role="status">設定已儲存</p>}
          <div className="form-actions">
            <button className="button button-primary" disabled={!canSave}>{save.isPending ? "儲存中…" : "儲存平台設定"}</button>
            {(changed || conflict || !versionReady) && <button type="button" className="button button-quiet" disabled={busy} onClick={() => void reload()}>重新讀取最新設定</button>}
          </div>
        </fieldset>
      </form>}
  </section>;
}
