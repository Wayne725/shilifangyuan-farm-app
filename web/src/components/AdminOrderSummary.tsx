import { ArrowClockwise } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { useAuth } from "../context/AuthContext";
import { apiFetch } from "../lib/api";
import { financePeriodPreset } from "../lib/meal-finance";
import {
  orderSummaryChannelLabels,
  orderSummaryStatusLabels,
  type OrderSummaryFilters,
  type OrderSummaryReport,
} from "../lib/order-summary";
import { channelMatchesCategory, orderCategoryLabels } from "../lib/order-category";
import { DataState, LoadingLines } from "./Shared";
import "./AdminOrderSummary.css";

const pageSize = 25;
const periodLabels = { today: "今天", week: "本週", month: "本月" } as const;
const formatCount = (value: number) => new Intl.NumberFormat("zh-TW").format(value);

export function AdminOrderSummary() {
  const { user } = useAuth();
  const isAdmin = user?.user_role === "admin";
  const [draft, setDraft] = useState<OrderSummaryFilters>(() => ({
    ...financePeriodPreset("month"), salesChannel: "all", orderCategory: "meal", status: "active", category: "", search: "",
  }));
  const [filters, setFilters] = useState(draft);
  const [preset, setPreset] = useState<keyof typeof periodLabels | "">("month");
  const [offset, setOffset] = useState(0);
  const [formError, setFormError] = useState("");
  const parameters = new URLSearchParams({
    starts_on: filters.startsOn,
    ends_on: filters.endsOn,
    sales_channel: filters.salesChannel,
    order_category: filters.orderCategory,
    status: filters.status,
    category: filters.category,
    search: filters.search,
    limit: String(pageSize),
    offset: String(offset),
  });
  const report = useQuery({
    queryKey: ["admin-order-summary", filters, offset],
    queryFn: () => apiFetch<OrderSummaryReport>(`/v1/admin/order-summary?${parameters}`),
    enabled: isAdmin,
  });
  useEffect(() => {
    if (report.data && offset > 0 && offset >= report.data.products.total) {
      setOffset(Math.max(0, Math.floor((report.data.products.total - 1) / pageSize) * pageSize));
    }
  }, [offset, report.data]);

  if (!isAdmin) return null;

  const categoryOptions = [...new Set([draft.category, ...(report.data?.category_options || [])].filter(Boolean))];
  const applyPreset = (mode: keyof typeof periodLabels) => {
    const next = { ...draft, ...financePeriodPreset(mode), search: draft.search.trim() };
    setDraft(next); setFilters(next); setPreset(mode); setOffset(0); setFormError("");
  };
  const dateError = formError ? "order-summary-date-error" : undefined;

  return <section className="admin-order-summary" aria-label="訂單品項彙整">
    <div className="tab-list order-summary-category-tabs" role="group" aria-label="訂單分類">
      {(["meal", "general"] as const).map((orderCategory) => <button key={orderCategory} type="button"
        aria-pressed={filters.orderCategory === orderCategory}
        data-state={filters.orderCategory === orderCategory ? "active" : "inactive"}
        onClick={() => {
          if (filters.orderCategory === orderCategory) return;
          const next = { ...filters, orderCategory, salesChannel: "all" as const, category: "" };
          setDraft(next); setFilters(next); setOffset(0); setFormError("");
        }}>{orderCategoryLabels[orderCategory]}</button>)}
    </div>
    <div className="admin-order-summary-presets" role="group" aria-label="下單日期快速選擇">
      {(Object.keys(periodLabels) as Array<keyof typeof periodLabels>).map((mode) => <button key={mode} className="button button-quiet" type="button" aria-pressed={preset === mode} onClick={() => applyPreset(mode)}>{periodLabels[mode]}</button>)}
    </div>
    <form className="admin-order-summary-filters" onSubmit={(event) => {
      event.preventDefault();
      if (!draft.startsOn || !draft.endsOn || draft.startsOn > draft.endsOn) {
        setFormError("請確認下單日期，結束日期不能早於開始日期。");
        return;
      }
      const next = { ...draft, search: draft.search.trim() };
      setDraft(next); setFilters(next); setOffset(0); setFormError("");
    }}>
      <label className="field"><span>下單日期起（台灣）</span><input type="date" required value={draft.startsOn} aria-invalid={Boolean(formError)} aria-describedby={dateError} onChange={(event) => { setDraft({ ...draft, startsOn: event.target.value }); setPreset(""); }} /></label>
      <label className="field"><span>下單日期迄（台灣）</span><input type="date" required value={draft.endsOn} aria-invalid={Boolean(formError)} aria-describedby={dateError} onChange={(event) => { setDraft({ ...draft, endsOn: event.target.value }); setPreset(""); }} /></label>
      <label className="field"><span id="order-summary-channel-label">銷售類型</span><select aria-labelledby="order-summary-channel-label" value={draft.salesChannel} onChange={(event) => setDraft({ ...draft, salesChannel: event.target.value as OrderSummaryFilters["salesChannel"] })}>{Object.entries(orderSummaryChannelLabels).filter(([value]) => channelMatchesCategory(value, filters.orderCategory)).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label className="field"><span id="order-summary-status-label">訂單狀態</span><select aria-labelledby="order-summary-status-label" value={draft.status} onChange={(event) => setDraft({ ...draft, status: event.target.value as OrderSummaryFilters["status"] })}>{Object.entries(orderSummaryStatusLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label className="field"><span id="order-summary-category-label">品類</span><select aria-labelledby="order-summary-category-label" value={draft.category} onChange={(event) => setDraft({ ...draft, category: event.target.value })}><option value="">全部品類</option>{categoryOptions.map((category) => <option key={category} value={category}>{category}</option>)}</select></label>
      <label className="field admin-order-summary-search"><span>品項名稱</span><input type="search" maxLength={100} value={draft.search} onChange={(event) => setDraft({ ...draft, search: event.target.value })} /></label>
      <button className="button button-primary" type="submit">查詢</button>
    </form>
    {formError && <p className="form-error" id="order-summary-date-error" role="alert">{formError}</p>}
    <div className="admin-order-summary-result-heading">
      <div><p>{filters.startsOn} 至 {filters.endsOn}</p><p>{[orderCategoryLabels[filters.orderCategory], orderSummaryChannelLabels[filters.salesChannel], orderSummaryStatusLabels[filters.status], filters.category || "全部品類", filters.search].filter(Boolean).join(" · ")}</p></div>
      <button className="button button-quiet" type="button" disabled={report.isFetching} onClick={() => void report.refetch()}><ArrowClockwise size={18} />{report.isFetching && !report.isPending ? "更新中…" : "更新"}</button>
    </div>
    <div aria-busy={report.isFetching}>
      {report.isPending ? <LoadingLines count={5} /> : report.isError ? <DataState kind="error" title="訂單彙整無法讀取" detail={report.error.message} onAction={() => void report.refetch()} /> : report.data && <>
        <dl className="admin-order-summary-metrics" aria-label="查詢結果統計">
          <div><dt>訂單數</dt><dd>{formatCount(report.data.summary.order_count)}<span>筆</span></dd></div>
          <div><dt>品項數量</dt><dd>{formatCount(report.data.summary.quantity)}</dd></div>
          <div><dt>品項種類</dt><dd>{formatCount(report.data.summary.product_count)}<span>種</span></dd></div>
        </dl>
        <dl className="admin-order-summary-statuses" aria-label="查詢結果訂單狀態">
          <div><dt>已付款</dt><dd>{formatCount(report.data.summary.paid_order_count)} 筆</dd></div>
          <div><dt>待付款</dt><dd>{formatCount(report.data.summary.pending_order_count)} 筆</dd></div>
          <div><dt>取消／失效／退款</dt><dd>{formatCount(report.data.summary.inactive_order_count)} 筆</dd></div>
        </dl>
        {report.data.summary.order_count === 0 ? <DataState title="沒有符合條件的訂單" detail="查詢結果為 0 筆。" /> : <>
          <div className="admin-order-summary-breakdowns">
            <section aria-labelledby="order-summary-categories"><h2 id="order-summary-categories">品類匯總</h2><div className="admin-order-summary-table-wrap" role="region" tabIndex={0} aria-label="品類匯總表格"><table><thead><tr><th scope="col">品類</th><th scope="col">訂單數</th><th scope="col">品項數量</th></tr></thead><tbody>{report.data.categories.map((row) => <tr key={row.category}><th scope="row">{row.category}</th><td>{formatCount(row.order_count)}</td><td>{formatCount(row.quantity)}</td></tr>)}</tbody></table></div></section>
            <section aria-labelledby="order-summary-channels"><h2 id="order-summary-channels">銷售類型匯總</h2><div className="admin-order-summary-table-wrap" role="region" tabIndex={0} aria-label="銷售類型匯總表格"><table><thead><tr><th scope="col">銷售類型</th><th scope="col">訂單數</th><th scope="col">品項數量</th></tr></thead><tbody>{report.data.channels.map((row) => <tr key={row.sales_channel}><th scope="row">{orderSummaryChannelLabels[row.sales_channel]}</th><td>{formatCount(row.order_count)}</td><td>{formatCount(row.quantity)}</td></tr>)}</tbody></table></div></section>
          </div>
          <section className="admin-order-summary-products" aria-labelledby="order-summary-products-heading">
            <div className="admin-order-summary-result-heading"><h2 id="order-summary-products-heading">商品明細</h2><p role="status">共 {formatCount(report.data.products.total)} 種品項</p></div>
            {report.data.products.total === 0 ? <DataState title="沒有符合條件的品項" detail="查詢結果為 0 種品項。" /> : <>
              <div className="admin-order-summary-table-wrap" role="region" tabIndex={0} aria-label="商品明細表格"><table className="admin-order-summary-product-table"><thead><tr><th scope="col">銷售類型</th><th scope="col">品類</th><th scope="col">品項</th><th scope="col">訂單數</th><th scope="col" aria-sort="descending">數量／單位</th></tr></thead><tbody>{report.data.products.items.map((row) => <tr key={row.key}><td>{orderSummaryChannelLabels[row.sales_channel]}</td><td>{row.category}</td><th scope="row">{row.name}</th><td>{formatCount(row.order_count)}</td><td>{formatCount(row.quantity)} {row.unit}</td></tr>)}</tbody></table></div>
              <nav className="admin-pagination" aria-label="商品明細分頁"><button className="button button-quiet" type="button" disabled={offset === 0 || report.isFetching} onClick={() => setOffset(Math.max(0, offset - pageSize))}>上一頁</button><span>第 {Math.floor(offset / pageSize) + 1}／{Math.ceil(report.data.products.total / pageSize)} 頁 · 共 {formatCount(report.data.products.total)} 種</span><button className="button button-quiet" type="button" disabled={offset + pageSize >= report.data.products.total || report.isFetching} onClick={() => setOffset(offset + pageSize)}>下一頁</button></nav>
            </>}
          </section>
        </>}
      </>}
    </div>
  </section>;
}
