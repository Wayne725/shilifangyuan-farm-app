import { useEffect, useState } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DataState, LoadingLines } from "./Shared";
import { ApiError, formatMoney, type apiFetch, type downloadApiFile } from "../lib/api";
import { financeDateTime } from "../lib/meal-finance";
import "../pages/AdminRecordsPage.css";
import { OptionalMark } from "./OptionalMark";

type Person = { id: string; source: "membership" | "roster"; member_number: string; legal_name: string; email: string; phone: string; status: string; share_certificate_number?: string; share_count?: number; share_capital_amount?: number; share_paid_on?: string };
type Receipt = { id: string; source: "online" | "offline"; member_number: string; name: string; kind: string; amount: number; received: number; refunded: number; net: number; status: string; paid_at?: string; receipt_number?: string; payment_method: string; reference?: string };
type Point = { user_id: string; member_number: string | null; name: string; email: string; balance: number };
type PointLedger = { items: Point[]; total: number; applied_query?: string };
const statuses: Record<string, string> = { active: "正式社員", inactive: "未啟用", suspended: "已移除會籍", trainee: "見習社員", pending: "待繳款", paid: "已收款", refund_pending: "退款處理中", refunded: "已退款", voided: "已作廢", failed: "未完成" };
const methods: Record<string, string> = { cash: "現金", bank_transfer: "轉帳", other: "其他", ecpay: "綠界", sandbox: "測試金流" };
export function AdminRecordsPanel({ adminId, request, downloadFile, tab, member, membership }: {
  adminId: string;
  request: typeof apiFetch;
  downloadFile: typeof downloadApiFile;
  tab: "members" | "payments" | "points";
  member?: string;
  membership?: string;
}) {
  const navigate = useNavigate({ from: "/admin/users" });
  const openLedger = (ledger: "members" | "payments" | "points") => void navigate({ search: {
    tab: "records", ledger: ledger === "members" ? undefined : ledger, member, membership,
  } });
  const [q, setQ] = useState(""), [search, setSearch] = useState("");
  const [status, setStatus] = useState(member ? "all" : "active"), [kind, setKind] = useState("all"), [start, setStart] = useState(""), [end, setEnd] = useState(""), [offset, setOffset] = useState(0);
  useEffect(() => { setQ(""); setSearch(""); setOffset(0); }, [tab]);
  const [target, setTarget] = useState<Person | null>(null), [voidTarget, setVoidTarget] = useState<Receipt | null>(null), [voidReason, setVoidReason] = useState("");
  const client = useQueryClient();
  const params = new URLSearchParams({ q: search, limit: "30", offset: String(offset) });
  const pointParams = new URLSearchParams();
  if (search) pointParams.set("q", search);
  if (member) {
    params.set("member_number", member);
    pointParams.set("member_number", member);
  }
  if (tab === "members") params.set("status", status);
  if (tab === "payments") { params.set("kind", kind); if (start) params.set("starts_on", start); if (end) params.set("ends_on", end); }
  const members = useQuery({ queryKey: ["member-register", adminId, params.toString()], queryFn: ({ signal }) => request<{items: Person[]; total: number}>(`/v1/admin/member-register?${params}`, { signal }), enabled: tab === "members" });
  const payments = useQuery({ queryKey: ["membership-payments", adminId, params.toString()], queryFn: ({ signal }) => request<{items: Receipt[]; total: number; summary: {received: number; refunded: number; net: number}}>(`/v1/admin/membership-payments?${params}`, { signal }), enabled: tab === "payments" });
  const points = useQuery({
    queryKey: ["points-ledger", adminId, pointParams.toString()],
    queryFn: ({ signal }) => request<PointLedger>(`/v1/admin/points-ledger?${pointParams}`, { signal }),
    enabled: tab === "points",
  });
  const download = useMutation({
    mutationFn: async () => {
      if (tab === "members") return downloadFile(`/v1/admin/member-register.csv?${params}`, "社員股籍名冊.csv");
      if (tab === "payments") return downloadFile(`/v1/admin/membership-payments.csv?${params}`, "入社收款紀錄.csv");
      if (!search) return downloadFile(`/v1/admin/points-ledger.csv?${pointParams}`, "社員點數.csv");
      try {
        // Older API versions ignore unknown query parameters on the unfiltered CSV.
        await downloadFile(`/v1/admin/points-ledger/filtered.csv?${pointParams}`, "社員點數.csv");
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) throw new Error("點數搜尋匯出暫時無法使用，請稍後重試。");
        throw error;
      }
    },
  });
  const voidReceipt = useMutation({ mutationFn: () => request(`/v1/admin/membership-offline-receipts/${voidTarget?.id}/void`, {method: "POST", body: JSON.stringify({reason: voidReason})}), onSuccess: () => { setVoidTarget(null); setVoidReason(""); void client.invalidateQueries({queryKey: ["membership-payments"]}); } });
  const current = tab === "members" ? members : tab === "payments" ? payments : points;
  const total = current.data?.total || 0;
  const pointRows = points.data?.items ?? [];
  const visiblePointRows = search && points.data?.applied_query !== search
    ? pointRows.filter(row => `${row.member_number ?? ""} ${row.name} ${row.email}`.toLowerCase().includes(search.toLowerCase()))
    : pointRows;
  return <section className="admin-records-page"><h2>社員與收款紀錄</h2>
    <nav className="admin-quick-buttons" aria-label="社員帳冊分類">{([ ["members", "社員股籍"], ["payments", "入社費與股金"], ["points", "累積點數"] ] as const).map(([value, label]) => <button className="button button-quiet" aria-pressed={tab === value} key={value} onClick={() => openLedger(value)}>{label}</button>)}</nav>
    {member && <div className="admin-quick-buttons"><span>社員編號 {member}</span><Link className="text-link" to="/admin/users" search={{ tab: "records", ledger: tab === "members" ? undefined : tab }}>清除社員篩選</Link></div>}
    <form className="admin-records-filters" onSubmit={event => {event.preventDefault(); setSearch(tab === "points" ? q.trim() : q); setOffset(0);}}><label className="field"><span>{tab === "points" ? "姓名、Email 或社員編號" : "姓名、Email、社員編號或股號"}</span><input value={q} onChange={event => setQ(event.target.value)} maxLength={120}/></label>
    {tab === "members" && <label className="field"><span>會籍狀態</span><select value={status} onChange={event => {setStatus(event.target.value); setOffset(0);}}><option value="active">正式社員</option><option value="all">全部名冊</option></select></label>}
    {tab === "payments" && <><label className="field"><span>款項</span><select value={kind} onChange={event => {setKind(event.target.value); setOffset(0);}}><option value="all">入社費與股金</option><option value="admission_fee">入社費</option><option value="share_capital">股金</option></select></label><label className="field"><span>收款日起</span><input type="date" value={start} onChange={event => {setStart(event.target.value); setOffset(0);}}/></label><label className="field"><span>收款日迄</span><input type="date" value={end} min={start} onChange={event => {setEnd(event.target.value); setOffset(0);}}/></label></>}
    <button className="button button-primary">查詢</button><button type="button" className="button button-quiet" disabled={download.isPending || current.isPending || current.isError} onClick={() => download.mutate()}>匯出 CSV</button></form>
    {download.isError && <p className="form-error" role="alert">{download.error.message}</p>}
    {current.isPending ? <LoadingLines count={5}/> : current.isError ? <DataState title="帳冊無法讀取" detail={current.error.message}/> : <>
      {tab === "payments" && payments.data && <dl className="admin-records-totals">{([["實收", payments.data.summary.received], ["已退款", payments.data.summary.refunded], ["淨收款", payments.data.summary.net]] as const).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{formatMoney(value)}</dd></div>)}</dl>}
      <p role="status">共 {tab === "points" ? visiblePointRows.length : total} 筆紀錄</p><div className="admin-records-table" tabIndex={0} role="region" aria-label="帳冊明細">
      {tab === "members" && <table><thead><tr>{["社員編號", "姓名／Email", "會籍", "股號", "股數", "股金", "繳股日期", "操作"].map(label => <th key={label}>{label}</th>)}</tr></thead><tbody>{members.data?.items.map(row => <tr key={`${row.source}-${row.id}`}><td>{row.member_number}</td><td>{row.legal_name}<br/>{row.email}</td><td>{statuses[row.status] || row.status}</td><td>{row.share_certificate_number || "—"}</td><td>{row.share_count ?? "—"}</td><td>{formatMoney(row.share_capital_amount || 0)}</td><td>{row.share_paid_on || "—"}</td><td><button className="button button-quiet" onClick={() => setTarget(row)}>登錄線下收款</button></td></tr>)}</tbody></table>}
      {tab === "payments" && <table><thead><tr>{["收款日期／收據", "社員", "款項", "實收", "退款", "狀態", "方式／收款資訊", "操作"].map(label => <th key={label}>{label}</th>)}</tr></thead><tbody>{payments.data?.items.map(row => <tr key={`${row.source}-${row.id}`}><td>{row.paid_at ? financeDateTime(row.paid_at) : "未收款"}<br/>{row.receipt_number || "—"}</td><td>{row.member_number}<br/>{row.name}</td><td>{row.kind === "admission_fee" ? "入社費" : "股金"}</td><td>{formatMoney(row.received)}</td><td>{formatMoney(row.refunded)}</td><td>{statuses[row.status] || row.status}</td><td>{methods[row.payment_method] || row.payment_method}<br/>{row.reference || "—"}</td><td>{row.source === "offline" && row.status !== "voided" ? <button className="button button-quiet" onClick={() => {setVoidTarget(row); voidReceipt.reset();}}>作廢紀錄</button> : row.source === "online" ? "線上金流" : "已作廢"}</td></tr>)}</tbody></table>}
      {tab === "points" && <table><thead><tr><th>社員編號</th><th>姓名</th><th>Email</th><th>累積點數</th></tr></thead><tbody>{visiblePointRows.map(row => <tr key={row.user_id}><td>{row.member_number}</td><td>{row.name}</td><td>{row.email}</td><td>{row.balance}</td></tr>)}</tbody></table>}
      </div>{tab !== "points" && total > 30 && <nav className="admin-pagination" aria-label="帳冊分頁"><button className="button button-quiet" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 30))}>上一頁</button><span>{Math.floor(offset / 30) + 1}／{Math.ceil(total / 30)}</span><button className="button button-quiet" disabled={offset + 30 >= total} onClick={() => setOffset(offset + 30)}>下一頁</button></nav>}
    </>}
    {target && <ReceiptForm request={request} target={target} onClose={() => setTarget(null)} onSaved={() => {setTarget(null); openLedger("payments"); void client.invalidateQueries({queryKey: ["membership-payments"]});}}/>}
    {voidTarget && <form className="admin-records-form" onSubmit={event => {event.preventDefault(); voidReceipt.mutate();}}><h2>作廢線下收款紀錄</h2><p>{voidTarget.receipt_number} · {formatMoney(voidTarget.amount)}</p><label className="field"><span>作廢原因<OptionalMark /></span><input maxLength={1000} value={voidReason} onChange={event => setVoidReason(event.target.value)}/></label>{voidReceipt.isError && <p role="alert">{voidReceipt.error.message}</p>}<div className="form-actions"><button className="button button-primary" disabled={voidReceipt.isPending}>確認作廢紀錄</button><button type="button" className="button button-quiet" onClick={() => setVoidTarget(null)}>取消</button></div></form>}
    </section>;
}
function ReceiptForm({request, target, onClose, onSaved}: {request: typeof apiFetch; target: Person; onClose: () => void; onSaved: () => void}) {
  const [key] = useState(() => crypto.randomUUID()), [kind, setKind] = useState("admission_fee"), [amount, setAmount] = useState(""), [method, setMethod] = useState("bank_transfer"), [paidAt, setPaidAt] = useState(""), [reference, setReference] = useState(""), [confirmed, setConfirmed] = useState(false);
  const save = useMutation({mutationFn: () => request("/v1/admin/membership-offline-receipts", {method: "POST", body: JSON.stringify({idempotency_key: key, target_type: target.source, target_id: target.id, kind, amount: Number(amount), payment_method: method, paid_at: `${paidAt}:00+08:00`, reference, confirmed_received: confirmed})}), onSuccess: onSaved});
  return <form className="admin-records-form" onSubmit={event => {event.preventDefault(); save.mutate();}}><p className="eyebrow">OFFLINE RECEIPT / 線下收款</p><h2>{target.member_number} · {target.legal_name}</h2><div className="field-grid two-columns"><label className="field"><span>款項</span><select value={kind} onChange={event => setKind(event.target.value)}><option value="admission_fee">入社費</option><option value="share_capital">股金</option></select></label><label className="field"><span>實收金額</span><input required type="number" min="1" max="100000000" step="1" value={amount} onChange={event => setAmount(event.target.value)}/></label><label className="field"><span>收款方式</span><select value={method} onChange={event => setMethod(event.target.value)}><option value="bank_transfer">轉帳</option><option value="cash">現金</option><option value="other">其他</option></select></label><label className="field"><span>收款日期（台灣時間）</span><input required type="datetime-local" value={paidAt} onChange={event => setPaidAt(event.target.value)}/></label></div><label className="field"><span>匯款末五碼／收款資訊</span><input maxLength={500} value={reference} onChange={event => setReference(event.target.value)}/></label><label className="check-field"><input required type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)}/>已核對款項確實收到，登錄此筆收款</label>{save.isError && <p className="form-error" role="alert">{save.error.message}</p>}<div className="form-actions"><button className="button button-primary" disabled={save.isPending}>儲存收款紀錄</button><button type="button" className="button button-quiet" onClick={onClose}>取消</button></div></form>;
}
