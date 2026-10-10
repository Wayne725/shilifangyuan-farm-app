import * as Tabs from "@radix-ui/react-tabs";
import { ArrowClockwise, Buildings, DownloadSimple } from "@phosphor-icons/react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import { AdminNav } from "../components/AdminNav";
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

export function AdminFinancePage() {
  const { user, openLogin } = useAuth();
  const isAdmin = user?.user_role === "admin";
  const [draft, setDraft] = useState(() => ({ ...financePeriodPreset("month"), search: "" }));
  const [filters, setFilters] = useState(draft);
  const [offset, setOffset] = useState(0);
  const [formError, setFormError] = useState("");
  const parameters = new URLSearchParams({ starts_on: filters.startsOn, ends_on: filters.endsOn, search: filters.search });
  const report = useQuery({
    queryKey: ["meal-sales-report", filters, offset],
    queryFn: () => apiFetch<MealSalesReport>(`/v1/admin/finance/meal-sales?${parameters}&limit=${pageSize}&offset=${offset}`),
    enabled: isAdmin,
    refetchInterval: 30_000,
  });
  const download = useMutation({ mutationFn: () => downloadApiFile(`/v1/admin/finance/meal-sales.csv?${parameters}`, `便當銷售明細-${filters.startsOn}-${filters.endsOn}.csv`) });
  useEffect(() => {
    if (report.data && offset > 0 && offset >= report.data.orders.total) setOffset(Math.max(0, Math.floor((report.data.orders.total - 1) / pageSize) * pageSize));
  }, [offset, report.data]);

  if (!isAdmin) return <section className="admin-gate"><div className="admin-gate-mark"><Buildings size={38} weight="light" /></div><h1>財務報表只向管理員開放。</h1>{!user ? <button className="button button-primary" type="button" onClick={openLogin}>管理員登入</button> : <Link className="button button-quiet" to="/">返回首頁</Link>}</section>;

  const applyPreset = (mode: "today" | "week" | "month") => {
    const next = { ...financePeriodPreset(mode), search: "" };
    setDraft(next); setFilters(next); setOffset(0); setFormError("");
  };
  return <section className="admin-module-page meal-finance-page">
    <header className="workspace-heading"><div><p className="eyebrow">財務報表</p><h1>便當銷售明細與統整</h1></div><p>依台灣時間計算收款與完成退款，逐筆查看訂單及餐點。</p></header>
    <AdminNav />
    <div className="meal-finance-presets" aria-label="常用查詢期間">{(["today", "week", "month"] as const).map((mode) => <button key={mode} type="button" className="button button-quiet" onClick={() => applyPreset(mode)}>{{ today: "今天", week: "本週", month: "本月" }[mode]}</button>)}</div>
    <form className="meal-finance-filters" onSubmit={(event) => {
      event.preventDefault();
      if (!draft.startsOn || !draft.endsOn || draft.startsOn > draft.endsOn) { setFormError("請確認起訖日期，結束日期不能早於開始日期。"); return; }
      setFilters({ ...draft, search: draft.search.trim() }); setOffset(0); setFormError("");
    }}>
      <label className="field"><span>開始日期</span><input type="date" required value={draft.startsOn} onChange={(event) => setDraft({ ...draft, startsOn: event.target.value })} /></label>
      <label className="field"><span>結束日期</span><input type="date" required value={draft.endsOn} onChange={(event) => setDraft({ ...draft, endsOn: event.target.value })} /></label>
      <label className="field"><span>訂單或買家</span><input placeholder="訂單編號、姓名或電子信箱" value={draft.search} maxLength={100} onChange={(event) => setDraft({ ...draft, search: event.target.value })} /></label>
      <button className="button button-primary" type="submit">查詢</button>
    </form>
    {formError && <p className="form-error" role="alert">{formError}</p>}
    <div className="meal-finance-result-heading"><p>目前查詢：{filters.startsOn} 至 {filters.endsOn}{filters.search && ` · ${filters.search}`}</p><div><button className="button button-quiet" type="button" disabled={report.isFetching} onClick={() => void report.refetch()}><ArrowClockwise size={18} />更新</button><button className="button button-system" type="button" disabled={!report.data || download.isPending} onClick={() => download.mutate()}><DownloadSimple size={18} />{download.isPending ? "下載中…" : "下載全部明細 CSV"}</button></div></div>
    {download.isError && <p className="form-error" role="alert">{download.error.message}</p>}
    {report.isPending ? <LoadingLines count={5} /> : report.isError ? <DataState kind="error" title="銷售報表無法讀取" detail={report.error.message} onAction={() => void report.refetch()} /> : report.data && <>
      <div className="meal-finance-stats">
        <article><span>期間收款</span><strong>{formatMoney(report.data.summary.received)}</strong><p>{report.data.summary.receipt_count} 筆收款</p></article>
        <article><span>期間完成退款</span><strong>{formatMoney(report.data.summary.refunded)}</strong><p>{report.data.summary.refund_count} 筆退款</p></article>
        <article><span>期間淨收款</span><strong>{formatMoney(report.data.summary.net)}</strong><p>收款減去完成退款</p></article>
        <article><span>首次付款餐點份數</span><strong>{report.data.summary.paid_meal_quantity} 份</strong><p>含後續退單，重複付款不重複計份數</p></article>
      </div>
      <p className="meal-finance-definition">待審退單與待處理退款不先扣除收款。相關訂單目前待退款 {formatMoney(report.data.summary.pending_refund_amount)}。跨期退款記在退款完成日；重複或遲到付款仍列實際收款與退款。</p>
      <Tabs.Root className="admin-social-tabs" defaultValue="summary">
        <Tabs.List className="tab-list" aria-label="銷售報表分類"><Tabs.Trigger value="summary">銷售統整</Tabs.Trigger><Tabs.Trigger value="details">訂單明細（{report.data.orders.total} 筆）</Tabs.Trigger></Tabs.List>
        <Tabs.Content className="tab-content" value="summary"><SalesSummary report={report.data} /></Tabs.Content>
        <Tabs.Content className="tab-content" value="details">
          <p>列出期間內下單、收款或退款的訂單。訂單金額與期間收退款分別顯示，方便核對跨期交易。</p>
          {report.data.orders.total === 0 ? <DataState title="這段期間沒有符合條件的便當訂單" detail="可調整日期或清除搜尋文字。" /> : <div className="meal-finance-orders">{report.data.orders.items.map((order) => <SalesOrder key={order.id} order={order} />)}</div>}
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
    <section><h2>餐點銷售數量</h2><p>以期間內首次付款的訂單計算；品項小計已包含加選金額。後續退單保留原銷售數量，退款另見上方統整。</p>{report.products.length === 0 ? <DataState title="期間內沒有首次付款的餐點" detail="跨期退款仍會顯示在收退款統整。" /> : <div className="meal-finance-table-wrap"><table className="meal-finance-table"><caption>首次付款訂單的餐點銷售</caption><thead><tr><th scope="col">餐點</th><th scope="col">數量</th><th scope="col">品項小計</th><th scope="col">訂單數</th></tr></thead><tbody>{report.products.map((row) => <tr key={`${row.name}-${row.unit}`}><th scope="row">{row.name}</th><td>{row.quantity} {row.unit}</td><td>{formatMoney(row.sales_amount)}</td><td>{row.order_count}</td></tr>)}</tbody></table></div>}</section>
  </div>;
}

function PeriodTable({ rows, period }: { rows: MealSalesPeriod[]; period: string }) {
  if (!rows.length) return <DataState title="期間內沒有完成收款或退款" detail="未付款或待退款訂單仍可在訂單明細查看。" />;
  return <div className="meal-finance-table-wrap"><table className="meal-finance-table"><caption>依{period === "daily" ? "日期" : period === "weekly" ? "週一日期" : "月份"}統整</caption><thead><tr><th scope="col">日期／期間</th><th scope="col">收款</th><th scope="col">完成退款</th><th scope="col">淨收款</th></tr></thead><tbody>{rows.map((row) => <tr key={row.period}><th scope="row">{row.period}{period === "weekly" ? " 起" : ""}</th><td>{formatMoney(row.received)}</td><td>{formatMoney(row.refunded)}</td><td>{formatMoney(row.net)}</td></tr>)}</tbody></table></div>;
}

function SalesOrder({ order }: { order: MealSalesOrder }) {
  return <article className="meal-finance-order">
    <header><div><h2>{order.order_number}</h2><p>{order.customer_name} · {membershipNames[order.membership_type] || order.membership_type}</p><p>{order.customer_email}</p></div><Link className="button button-quiet" to="/admin" search={{ q: order.order_number }}>處理付款／發票</Link></header>
    <div className="meal-finance-order-states"><span>{adminPaymentStatusLabel(order.payment_status)}</span><span>{fulfillmentStatusLabel(order.fulfillment_status)}</span><span>{invoiceStatusLabel(order.invoice_status)}{order.invoice_number && ` · ${order.invoice_number}`}</span>{order.cancellation_status && <span>{cancellationNames[order.cancellation_status] || order.cancellation_status}</span>}</div>
    <dl className="meal-finance-order-meta"><div><dt>下單時間</dt><dd>{financeDateTime(order.created_at)}</dd></div><div><dt>首次付款時間</dt><dd>{financeDateTime(order.paid_at)}</dd></div><div><dt>取餐時間</dt><dd>{financeDateTime(order.pickup_at)}{!order.pickup_time_confirmed && order.pickup_at && "（原場次開始時間）"}</dd></div><div><dt>餐別／取餐地點</dt><dd>{order.meal_period === "lunch" ? "午餐" : order.meal_period === "dinner" ? "晚餐" : "未指定餐別"} · {order.location || "未指定地點"}</dd></div></dl>
    <ul className="meal-finance-item-list">{order.items.map((item, index) => <li key={`${item.name}-${index}`}><div><strong>{item.name} × {item.quantity} {item.unit}</strong><p>單價 {formatMoney(item.unit_price)} · {item.tax_type === "taxable" ? "含稅" : "免稅"}{item.options.length > 0 && ` · 加選：${item.options.map((option) => `${option.group_name}／${option.name}`).join("、")}`}</p></div><strong>{formatMoney(item.subtotal)}</strong></li>)}</ul>
    <dl className="meal-finance-order-totals"><div><dt>訂單金額</dt><dd>{formatMoney(order.order_amount)}</dd></div><div><dt>金額內含稅額</dt><dd>{formatMoney(order.tax_amount)}</dd></div><div><dt>期間收款</dt><dd>{formatMoney(order.period_received)}</dd></div><div><dt>期間完成退款</dt><dd>{formatMoney(order.period_refunded)}</dd></div><div><dt>期間淨收款</dt><dd>{formatMoney(order.period_net)}</dd></div></dl>
    {order.refunds.length > 0 && <details className="meal-finance-refunds"><summary>查看退款紀錄（{order.refunds.length} 筆）</summary>{order.refunds.map((refund, index) => <p key={index}>{formatMoney(refund.amount)} · {refundNames[refund.status] || refund.status} · {refund.completed_at ? financeDateTime(refund.completed_at) : `申請於 ${financeDateTime(refund.requested_at)}`}<br />原因：{refund.reason}</p>)}</details>}
  </article>;
}
