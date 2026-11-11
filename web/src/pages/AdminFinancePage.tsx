import * as Tabs from "@radix-ui/react-tabs";
import { ArrowClockwise, Buildings, DownloadSimple } from "@phosphor-icons/react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import { OrderCategoryFilter } from "../components/OrderCategoryFilter";
import { channelMatchesCategory, orderCategoryLabels, type OrderCategory } from "../lib/order-category";
import { ActivityFinance } from "../components/ActivityFinance";
import { AdminNav } from "../components/AdminNav";
import { TaxLedger } from "../components/TaxLedger";
import { SalesByMembership } from "../components/SalesByMembership";
import { DataState, LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import { apiFetch, downloadApiFile, formatMoney } from "../lib/api";
import { adminPaymentStatusLabel, fulfillmentStatusLabel, invoiceStatusLabel } from "../lib/labels";
import { financeDateTime, financePeriodPreset, type MealSalesOrder, type MealSalesPeriod, type MealSalesReport } from "../lib/meal-finance";
import "./AdminFinancePage.css";

const pageSize = 20;
const membershipNames: Record<string, string> = { nonmember: "一般使用者", trainee: "實習社員", member: "社員" };
const cancellationNames: Record<string, string> = { pending: "退單待審", approved: "退單已核准", rejected: "退單遭拒" };
const refundNames: Record<string, string> = { pending: "待退款", completed: "退款完成", failed: "退款失敗" };
const channelNames = { all: "全部銷售", meal_preorder: "便當", regular: "日常選品", group: "共同購買" };

export function AdminFinancePage() {
  const {user,openLogin}=useAuth();
  if(user?.user_role!=="admin") return <section className="admin-gate"><h1>財務報表只向管理員開放。</h1>{!user&&<button className="button button-primary" onClick={openLogin}>管理員登入</button>}</section>;
  return <section className="admin-module-page meal-finance-page">
    <header className="workspace-heading"><div><p className="eyebrow">FINANCE / 財務報表</p><h1>收款與銷售報表</h1></div></header>
    <AdminNav />
    <Tabs.Root key={user.id} defaultValue="sales">
      <Tabs.List className="tab-list" aria-label="財務來源">
        <Tabs.Trigger value="sales">商品銷售</Tabs.Trigger>
        <Tabs.Trigger value="memberships">身分銷售占比</Tabs.Trigger>
        <Tabs.Trigger value="activities">活動收款</Tabs.Trigger>
        <Tabs.Trigger value="tax">稅務彙總</Tabs.Trigger>
      </Tabs.List>
      <Tabs.Content value="sales"><ProductFinance /></Tabs.Content>
      <Tabs.Content value="memberships"><SalesByMembership adminId={user.id} /></Tabs.Content>
      <Tabs.Content value="activities"><ActivityFinance /></Tabs.Content>
      <Tabs.Content value="tax"><TaxLedger adminId={user.id} /></Tabs.Content>
    </Tabs.Root>
  </section>;
}
function ProductFinance() {
  const { user, openLogin } = useAuth();
  const isAdmin = user?.user_role === "admin";
  const [draft, setDraft] = useState(() => ({ ...financePeriodPreset("month"), search: "", salesChannel: "all", orderCategory: "all" as OrderCategory }));
  const [filters, setFilters] = useState(draft);
  const [offset, setOffset] = useState(0);
  const [formError, setFormError] = useState("");
  const parameters = new URLSearchParams({ starts_on: filters.startsOn, ends_on: filters.endsOn, search: filters.search, sales_channel: filters.salesChannel, order_category: filters.orderCategory });
  const report = useQuery({
    queryKey: ["meal-sales-report", filters, offset],
    queryFn: () => apiFetch<MealSalesReport>(`/v1/admin/finance/meal-sales?${parameters}&limit=${pageSize}&offset=${offset}`),
    enabled: isAdmin,
    refetchInterval: 30_000,
  });
  const download = useMutation({ mutationFn: () => downloadApiFile(`/v1/admin/finance/meal-sales.csv?${parameters}`, `${orderCategoryLabels[filters.orderCategory]}銷售明細-${filters.startsOn}-${filters.endsOn}.csv`) });
  useEffect(() => {
    if (report.data && offset > 0 && offset >= report.data.orders.total) setOffset(Math.max(0, Math.floor((report.data.orders.total - 1) / pageSize) * pageSize));
  }, [offset, report.data]);

  if (!isAdmin) return <section className="admin-gate"><div className="admin-gate-mark"><Buildings size={38} weight="light" /></div><h1>財務報表只向管理員開放。</h1>{!user ? <button className="button button-primary" type="button" onClick={openLogin}>管理員登入</button> : <Link className="button button-quiet" to="/">返回首頁</Link>}</section>;

  const applyPreset = (mode: "today" | "week" | "month") => {
    const next = { ...financePeriodPreset(mode), search: "", salesChannel: draft.salesChannel, orderCategory: draft.orderCategory };
    setDraft(next); setFilters(next); setOffset(0); setFormError("");
  };
  return <section>
    <h2>銷售明細與統整</h2>

    <OrderCategoryFilter value={filters.orderCategory} onChange={(orderCategory) => {
      const next = { ...filters, orderCategory, salesChannel: "all" };
      setDraft(next); setFilters(next); setOffset(0); setFormError("");
    }} />
    <div className="meal-finance-presets" aria-label="常用查詢期間">{(["today", "week", "month"] as const).map((mode) => <button key={mode} type="button" className="button button-quiet" onClick={() => applyPreset(mode)}>{{ today: "今天", week: "本週", month: "本月" }[mode]}</button>)}</div>
    <form className="meal-finance-filters" onSubmit={(event) => {
      event.preventDefault();
      if (!draft.startsOn || !draft.endsOn || draft.startsOn > draft.endsOn) { setFormError("請確認起訖日期，結束日期不能早於開始日期。"); return; }
      setFilters({ ...draft, search: draft.search.trim() }); setOffset(0); setFormError("");
    }}>
      <label className="field"><span>收退款日期起</span><input type="date" required value={draft.startsOn} onChange={(event) => setDraft({ ...draft, startsOn: event.target.value })} /></label>
      <label className="field"><span>收退款日期迄</span><input type="date" required value={draft.endsOn} onChange={(event) => setDraft({ ...draft, endsOn: event.target.value })} /></label>
      <label className="field"><span>銷售類型</span><select value={draft.salesChannel} onChange={(event) => setDraft({ ...draft, salesChannel: event.target.value })}>{Object.entries(channelNames).filter(([value]) => channelMatchesCategory(value, filters.orderCategory)).map(([value, name]) => <option key={value} value={value}>{name}</option>)}</select></label>
      <label className="field"><span>訂單或買家</span><input placeholder="訂單編號、姓名或電子信箱" value={draft.search} maxLength={100} onChange={(event) => setDraft({ ...draft, search: event.target.value })} /></label>
      <button className="button button-primary" type="submit">查詢</button>
    </form>
    {formError && <p className="form-error" role="alert">{formError}</p>}
    <div className="meal-finance-result-heading"><p>{orderCategoryLabels[filters.orderCategory]} · {channelNames[filters.salesChannel as keyof typeof channelNames]} · {filters.startsOn} 至 {filters.endsOn}{filters.search && ` · ${filters.search}`}</p><div><button className="button button-quiet" type="button" disabled={report.isFetching} onClick={() => void report.refetch()}><ArrowClockwise size={18} />更新</button><button className="button button-system" type="button" disabled={!report.data || download.isPending} onClick={() => download.mutate()}><DownloadSimple size={18} />{download.isPending ? "下載中…" : "下載全部明細 CSV"}</button></div></div>
    {download.isError && <p className="form-error" role="alert">{download.error.message}</p>}
    {report.isPending ? <LoadingLines count={5} /> : report.isError ? <DataState kind="error" title="銷售報表無法讀取" detail={report.error.message} onAction={() => void report.refetch()} /> : report.data && <>
      <div className="meal-finance-stats">
        <article><span>期間收款</span><strong>{formatMoney(report.data.summary.received)}</strong><p>{report.data.summary.receipt_count} 筆收款</p></article>
        <article><span>期間完成退款</span><strong>{formatMoney(report.data.summary.refunded)}</strong><p>{report.data.summary.refund_count} 筆退款</p></article>
        <article><span>期間淨收款</span><strong>{formatMoney(report.data.summary.net)}</strong></article>
        <article><span>銷售數量</span><strong>{report.data.summary.paid_meal_quantity}</strong><p>首次付款訂單的品項數量</p></article>
      </div>
      <details className="meal-finance-definition"><summary>待退款 {formatMoney(report.data.summary.pending_refund_amount)} · 查看計算方式</summary><p>以台灣日期統計實際收款及已完成退款。待審退單與待退款不先扣除；跨期退款記在完成日。銷售數量以首次付款計算，後續退單仍保留原數量，重複付款不重複計算品項。</p></details>
      <Tabs.Root className="admin-social-tabs" defaultValue="details">
        <Tabs.List className="tab-list" aria-label="銷售報表分類"><Tabs.Trigger value="details">銷售明細（{report.data.orders.total} 筆）</Tabs.Trigger><Tabs.Trigger value="summary">銷售統整</Tabs.Trigger></Tabs.List>
        <Tabs.Content className="tab-content" value="summary"><SalesSummary report={report.data} /></Tabs.Content>
        <Tabs.Content className="tab-content" value="details">
          {report.data.orders.total === 0 ? <DataState title="這段期間沒有符合條件的訂單" detail="可調整日期或清除搜尋文字。" /> : <div className="meal-finance-orders">{report.data.orders.items.map((order) => <details className="meal-finance-sale" key={order.id}>
            <summary><div><strong className="order-number">{order.order_number}</strong><span>{financeDateTime(order.created_at)} · {channelNames[order.sales_channel]}</span></div><div><strong>{order.customer_name}</strong><span>{order.items.map((item) => `${item.name} × ${item.quantity}`).join("、") || "無品項"}</span></div><div><span>訂單金額</span><strong>{formatMoney(order.order_amount)}</strong></div><div><span>{adminPaymentStatusLabel(order.payment_status)}</span><span>{invoiceStatusLabel(order.invoice_status)}</span></div><span className="meal-finance-expand">詳細資料</span></summary>
            <SalesOrder order={order} />
          </details>)}</div>}
          {report.data.orders.total > 0 && <nav className="admin-pagination" aria-label="銷售明細分頁"><button className="button button-quiet" type="button" disabled={offset === 0 || report.isFetching} onClick={() => setOffset(Math.max(0, offset - pageSize))}>上一頁</button><span>第 {Math.floor(offset / pageSize) + 1}／{Math.ceil(report.data.orders.total / pageSize)} 頁 · 共 {report.data.orders.total} 筆</span><button className="button button-quiet" type="button" disabled={offset + pageSize >= report.data.orders.total || report.isFetching} onClick={() => setOffset(offset + pageSize)}>下一頁</button></nav>}
        </Tabs.Content>
      </Tabs.Root>
    </>}
  </section>;
}

function SalesSummary({ report }: { report: MealSalesReport }) {
  const [period, setPeriod] = useState<"daily" | "weekly" | "monthly">("daily");
  return <div className="meal-finance-summary">
    <section><div className="meal-finance-result-heading"><h2>收款與退款統整</h2><label className="field"><span>統整方式</span><select value={period} onChange={(event) => setPeriod(event.target.value as typeof period)}><option value="daily">每日</option><option value="weekly">每週（週一開始）</option><option value="monthly">每月</option></select></label></div><PeriodTable rows={report[period]} period={period} /></section>
    <section><h2>商品銷售數量</h2>{report.products.length === 0 ? <DataState title="期間內沒有首次付款的商品" detail="跨期退款仍會顯示在收退款統整。" /> : <div className="meal-finance-table-wrap"><table className="meal-finance-table"><caption>首次付款訂單的商品銷售</caption><thead><tr><th scope="col">商品</th><th scope="col">數量</th><th scope="col">品項小計</th><th scope="col">訂單數</th></tr></thead><tbody>{report.products.map((row) => <tr key={`${row.name}-${row.unit}`}><th scope="row">{row.name}</th><td>{row.quantity} {row.unit}</td><td>{formatMoney(row.sales_amount)}</td><td>{row.order_count}</td></tr>)}</tbody></table></div>}</section>
  </div>;
}

function PeriodTable({ rows, period }: { rows: MealSalesPeriod[]; period: string }) {
  if (!rows.length) return <DataState title="期間內沒有完成收款或退款" detail="未付款或待退款訂單仍可在訂單明細查看。" />;
  return <div className="meal-finance-table-wrap"><table className="meal-finance-table"><caption>依{period === "daily" ? "日期" : period === "weekly" ? "週一日期" : "月份"}統整</caption><thead><tr><th scope="col">日期／期間</th><th scope="col">收款</th><th scope="col">完成退款</th><th scope="col">淨收款</th></tr></thead><tbody>{rows.map((row) => <tr key={row.period}><th scope="row">{row.period}{period === "weekly" ? " 起" : ""}</th><td>{formatMoney(row.received)}</td><td>{formatMoney(row.refunded)}</td><td>{formatMoney(row.net)}</td></tr>)}</tbody></table></div>;
}

function SalesOrder({ order }: { order: MealSalesOrder }) {
  return <article className="meal-finance-order">
    <header><div><h2>{order.order_number}</h2><p>{order.customer_name} · {membershipNames[order.membership_type] || order.membership_type}</p><p>{order.customer_email}</p></div><Link className="button button-quiet" to="/admin" search={{ section: "orders", q: order.order_number, order_id: order.id }}>處理付款／發票</Link></header>
    <div className="meal-finance-order-states"><span>{adminPaymentStatusLabel(order.payment_status)}</span><span>{fulfillmentStatusLabel(order.fulfillment_status)}</span><span>{invoiceStatusLabel(order.invoice_status)}{order.invoice_number && ` · ${order.invoice_number}`}</span>{order.cancellation_status && <span>{cancellationNames[order.cancellation_status] || order.cancellation_status}</span>}</div>
    <dl className="meal-finance-order-meta">{order.revision !== undefined && <div><dt>目前訂單版本</dt><dd>第 {order.revision} 版</dd></div>}<div><dt>下單時間</dt><dd>{financeDateTime(order.created_at)}</dd></div><div><dt>首次付款時間</dt><dd>{financeDateTime(order.paid_at)}</dd></div><div><dt>取貨時間</dt><dd>{financeDateTime(order.pickup_at)}{!order.pickup_time_confirmed && order.pickup_at && "（原場次開始時間）"}</dd></div><div><dt>取貨地點</dt><dd>{order.location || "—"}</dd></div></dl>
    <ul className="meal-finance-item-list">{order.items.map((item, index) => <li key={`${item.name}-${index}`}><div><strong>{item.name} × {item.quantity} {item.unit}</strong><p>單價 {formatMoney(item.unit_price)} · {item.tax_type === "taxable" ? "含稅" : "免稅"}{item.options.length > 0 && ` · 加選：${item.options.map((option) => `${option.group_name}／${option.name}`).join("、")}`}</p></div><strong>{formatMoney(item.subtotal)}</strong></li>)}</ul>
    <dl className="meal-finance-order-totals"><div><dt>訂單金額</dt><dd>{formatMoney(order.order_amount)}</dd></div><div><dt>預估稅額</dt><dd>{formatMoney(order.tax_amount)}</dd></div><div><dt>期間收款</dt><dd>{formatMoney(order.period_received)}</dd></div><div><dt>期間完成退款</dt><dd>{formatMoney(order.period_refunded)}</dd></div><div><dt>期間淨收款</dt><dd>{formatMoney(order.period_net)}</dd></div></dl>
    {order.receipts && order.receipts.length > 0 && <details className="meal-finance-receipts"><summary>查看收款交易（{order.receipts.length} 筆）</summary><ol>{order.receipts.map((receipt) => <li key={receipt.merchant_trade_no}><div><strong className="order-number">{receipt.merchant_trade_no}</strong><span>第 {receipt.order_revision} 版{receipt.obsolete_revision && " · 舊版付款"}</span></div><dl><div><dt>實收金額</dt><dd>{formatMoney(receipt.amount)}</dd></div><div><dt>交易狀態</dt><dd>{adminPaymentStatusLabel(receipt.status)}</dd></div><div><dt>收款時間</dt><dd>{receipt.received_at ? financeDateTime(receipt.received_at) : "收款日期待核對"}</dd></div></dl></li>)}</ol></details>}
    {order.refunds.length > 0 && <details className="meal-finance-refunds"><summary>查看退款紀錄（{order.refunds.length} 筆）</summary>{order.refunds.map((refund, index) => <p key={index}>{formatMoney(refund.amount)} · {refundNames[refund.status] || refund.status} · {refund.completed_at ? financeDateTime(refund.completed_at) : `申請於 ${financeDateTime(refund.requested_at)}`}{refund.purpose === "obsolete_order_revision" && " · 舊版付款退款"}{refund.reason && <><br />原因：{refund.reason}</>}</p>)}</details>}
  </article>;
}
