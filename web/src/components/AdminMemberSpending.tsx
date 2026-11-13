import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import { useMutation, useQuery } from "@tanstack/react-query";

import { formatMoney, type apiFetch, type downloadApiFile } from "../lib/api";
import { taipeiDate } from "../lib/meal-time";
import { DataState, LoadingLines } from "./Shared";

const categories = [
  ["regular", "一般商品"],
  ["group", "共同購買"],
  ["meal", "便當"],
  ["business", "會餐"],
  ["walk_in", "現場銷售"],
] as const;
const pageSize = 20;
const firstYear = 2020;

type Category = typeof categories[number][0];
type Filters = { period: string; start: string; end: string; q: string; status: string; sort: string };
type Page<T> = { items: T[]; total: number };
type Member = { id: string; member_number: string; name: string; status_label: string };
type Summary = Member & { net_amount: number; order_count: number; latest_paid_date: string | null; categories: Record<Category, number> };
type Detail = {
  id: string; date: string; order_id: string; order_number: string; category_label: string;
  amount: number; payment_method: string; invoice_number: string | null; refunded: number;
};
type Access = { adminId: string; request: typeof apiFetch; downloadFile: typeof downloadApiFile };

function dates(filters: Filters) {
  return filters.period === "custom"
    ? { starts_on: filters.start, ends_on: filters.end }
    : { starts_on: `${filters.period}-01-01`, ends_on: `${filters.period}-12-31` };
}

function dateLabel(date: string | null) {
  return date ? date.replaceAll("-", "/") : "—";
}

function SpendCell({ label, children }: { label: string; children: ReactNode }) {
  return <td role="cell"><span className="admin-spending-cell-label" aria-hidden="true">{label}</span>{children}</td>;
}

export function AdminMemberSpending({ adminId, request, downloadFile, member }: Access & { member?: string }) {
  const currentYear = Number(taipeiDate().slice(0, 4));
  const [draft, setDraft] = useState<Filters>(() => ({
    period: String(currentYear), start: `${currentYear}-01-01`, end: `${currentYear}-12-31`,
    q: "", status: "active", sort: "amount_desc",
  }));
  const [filters, setFilters] = useState(draft);
  const [offset, setOffset] = useState(0);
  const [selection, setSelection] = useState<Member | null>(null);
  const params = new URLSearchParams({ ...dates(filters), q: filters.q, status: filters.status, sort: filters.sort });
  if (member) params.set("member_number", member);
  const page = useQuery({
    queryKey: ["member-spending", adminId, params.toString(), offset],
    queryFn: ({ signal }) => request<Page<Summary>>(`/v1/admin/member-spending?${params}&limit=${pageSize}&offset=${offset}`, { signal }),
  });
  const download = useMutation({
    mutationFn: () => downloadFile(`/v1/admin/member-spending.csv?${params}`, "社員消費彙總.csv"),
  });
  const years = Array.from({ length: Math.max(currentYear - firstYear + 1, 1) }, (_, index) => currentYear - index);
  return <section aria-label="社員消費紀錄" className="admin-member-spending">
    <h3>社員消費紀錄</h3>
    <p>金額按台灣時間的付款日計入，退款在完成退款日扣回。筆數按期間內已付款的訂單計算。</p>
    <form className="admin-records-filters" aria-label="查詢社員消費" onSubmit={event => {
      event.preventDefault();
      setFilters({ ...draft, q: draft.q.trim() });
      setOffset(0);
      setSelection(null);
      download.reset();
    }}>
      <label className="field"><span>社員編號、姓名或 Email</span>
        <input type="search" value={draft.q} maxLength={120} onChange={event => setDraft({ ...draft, q: event.target.value })} />
      </label>
      <label className="field"><span>期間</span>
        <select aria-label="期間" value={draft.period} onChange={event => setDraft({ ...draft, period: event.target.value })}>
          {years.map(year => <option key={year} value={year}>{year} 年</option>)}
          <option value="custom">自訂日期</option>
        </select>
      </label>
      {draft.period === "custom" && <>
        <label className="field"><span>開始日期</span><input required type="date" value={draft.start} max={draft.end || undefined} onChange={event => setDraft({ ...draft, start: event.target.value })} /></label>
        <label className="field"><span>結束日期</span><input required type="date" value={draft.end} min={draft.start || undefined} onChange={event => setDraft({ ...draft, end: event.target.value })} /></label>
      </>}
      <label className="field"><span>會籍狀態</span>
        <select aria-label="會籍狀態" value={draft.status} onChange={event => setDraft({ ...draft, status: event.target.value })}>
          <option value="active">正式社員</option><option value="all">全部</option>
        </select>
      </label>
      <label className="field"><span>排序</span>
        <select aria-label="排序" value={draft.sort} onChange={event => setDraft({ ...draft, sort: event.target.value })}>
          <option value="amount_desc">金額由高到低</option><option value="amount_asc">金額由低到高</option>
          <option value="name_asc">姓名正序</option><option value="name_desc">姓名倒序</option>
        </select>
      </label>
      <button type="submit" className="button button-primary">查詢</button>
      <button type="button" className="button button-quiet" disabled={!page.isSuccess || page.isFetching || download.isPending}
        onClick={() => download.mutate()}>匯出彙總 CSV</button>
    </form>
    {download.isError && <p role="alert" className="form-error">{download.error.message}</p>}
    {page.isPending ? <LoadingLines count={4} /> : page.isError
      ? <DataState kind="error" title="社員消費紀錄無法讀取" detail={page.error.message} onAction={() => page.refetch()} />
      : <>
        <p role="status">共 {page.data.total} 位社員</p>
        {page.data.total > 0 && <div className="admin-records-table" tabIndex={0} role="region" aria-label="社員消費彙總">
          <table role="table">
            <thead role="rowgroup"><tr role="row">
              {["社員編號", "姓名", "會籍狀態", "消費金額合計", "筆數", "最近消費日", ...categories.map(([, label]) => label)].map(label => <th key={label} scope="col" role="columnheader">{label}</th>)}
            </tr></thead>
            <tbody role="rowgroup">{page.data.items.map(row => <tr key={row.id} role="row">
              <SpendCell label="社員編號">{row.member_number || "—"}</SpendCell>
              <SpendCell label="姓名"><button type="button" className="text-link admin-spending-member-link" aria-label={`查看 ${row.name || row.member_number || "社員"} 消費明細`}
                aria-expanded={selection?.id === row.id} onClick={() => setSelection(selection?.id === row.id ? null : row)}>{row.name || "—"}</button></SpendCell>
              <SpendCell label="會籍狀態">{row.status_label}</SpendCell>
              <SpendCell label="消費金額合計">{formatMoney(row.net_amount)}</SpendCell>
              <SpendCell label="筆數">{row.order_count}</SpendCell>
              <SpendCell label="最近消費日">{dateLabel(row.latest_paid_date)}</SpendCell>
              {categories.map(([category, label]) => <SpendCell key={category} label={label}>{formatMoney(row.categories[category])}</SpendCell>)}
            </tr>)}</tbody>
          </table>
        </div>}
        <Pagination label="社員消費彙總分頁" total={page.data.total} offset={offset} busy={page.isFetching}
          onChange={next => { setOffset(next); setSelection(null); }} />
      </>}
    {selection && <SpendingDetails key={selection.id} adminId={adminId} request={request} downloadFile={downloadFile}
      member={selection} period={dates(filters)} onClose={() => setSelection(null)} />}
  </section>;
}

function SpendingDetails({ adminId, request, downloadFile, member, period, onClose }: Access & {
  member: Member;
  period: ReturnType<typeof dates>;
  onClose: () => void;
}) {
  const [offset, setOffset] = useState(0);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { heading.current?.focus(); }, []);
  const params = new URLSearchParams(period);
  const page = useQuery({
    queryKey: ["member-spending-details", adminId, member.id, params.toString(), offset],
    queryFn: ({ signal }) => request<Page<Detail>>(`/v1/admin/member-spending/${member.id}?${params}&limit=${pageSize}&offset=${offset}`, { signal }),
  });
  const download = useMutation({
    mutationFn: () => downloadFile(`/v1/admin/member-spending/${member.id}/details.csv?${params}`, "社員消費明細.csv"),
  });
  return <section className="admin-records-form" aria-label="社員消費明細">
    <h4 ref={heading} tabIndex={-1}>{[member.member_number, member.name].filter(Boolean).join(" · ")} 消費明細</h4>
    <div className="admin-quick-buttons">
      <button type="button" className="button button-quiet" disabled={!page.isSuccess || download.isPending} onClick={() => download.mutate()}>匯出明細 CSV</button>
      <button type="button" className="button button-quiet" onClick={onClose}>關閉明細</button>
    </div>
    {download.isError && <p role="alert" className="form-error">{download.error.message}</p>}
    {page.isPending ? <LoadingLines count={4} /> : page.isError
      ? <DataState kind="error" title="消費明細無法讀取" detail={page.error.message} onAction={() => page.refetch()} />
      : page.data.total === 0 ? <p role="status">期間內沒有收款或退款紀錄</p> : <>
        <div className="admin-records-table" tabIndex={0} role="region" aria-label="收退款明細">
          <table role="table">
            <thead role="rowgroup"><tr role="row">{["日期", "訂單編號", "類別", "金額", "付款方式", "發票號碼", "退款金額"].map(label => <th key={label} scope="col" role="columnheader">{label}</th>)}</tr></thead>
            <tbody role="rowgroup">{page.data.items.map(row => <tr key={row.id} role="row">
              <SpendCell label="日期">{dateLabel(row.date)}</SpendCell>
              <SpendCell label="訂單編號"><Link className="text-link" to="/admin" search={{ section: "orders", q: row.order_number, order_id: row.order_id }}>{row.order_number}</Link></SpendCell>
              <SpendCell label="類別">{row.category_label}</SpendCell>
              <SpendCell label="金額">{formatMoney(row.amount)}</SpendCell>
              <SpendCell label="付款方式">{row.payment_method}</SpendCell>
              <SpendCell label="發票號碼">{row.invoice_number || "—"}</SpendCell>
              <SpendCell label="退款金額">{formatMoney(row.refunded)}</SpendCell>
            </tr>)}</tbody>
          </table>
        </div>
        <Pagination label="社員消費明細分頁" total={page.data.total} offset={offset} busy={page.isFetching} onChange={setOffset} />
      </>}
  </section>;
}

function Pagination({ label, total, offset, busy, onChange }: {
  label: string; total: number; offset: number; busy: boolean; onChange: (offset: number) => void;
}) {
  if (total <= pageSize) return null;
  return <nav className="admin-pagination" aria-label={label}>
    <button type="button" className="button button-quiet" disabled={offset === 0 || busy} onClick={() => onChange(offset - pageSize)}>上一頁</button>
    <span>{Math.floor(offset / pageSize) + 1}／{Math.ceil(total / pageSize)}</span>
    <button type="button" className="button button-quiet" disabled={offset + pageSize >= total || busy} onClick={() => onChange(offset + pageSize)}>下一頁</button>
  </nav>;
}
