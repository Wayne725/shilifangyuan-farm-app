import { useEffect, useRef, useState } from "react";
import { Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AdminNav } from "../components/AdminNav";
import { DataState, LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import { apiFetch, downloadApiFile } from "../lib/api";
import { financeDateTime } from "../lib/meal-finance";
import "./AdminRecordsPage.css";
import "./AdminStatementsPage.css";

const sources: Record<string, {label: string; extension: string}> = {
  newebpay: {label: "藍新金流", extension: ".csv"},
  jkopay: {label: "街口支付", extension: ".xlsx"},
  esun: {label: "玉山銀行", extension: ".xls"},
  linepay: {label: "LINE Pay", extension: ".xlsx"},
  invoice: {label: "發票明細", extension: ".xlsx"},
};
const results: Record<string, string> = {all: "全部", matched: "吻合", difference: "有差異", unmatched: "待連結"};
const kinds: Record<string, string> = {receipt: "收款", refund: "退款", review: "待核對", bank_credit: "存入", bank_debit: "支出", invoice: "發票"};
const amount = (value: string | null) => {
  if (value === null) return "—";
  const [whole, fraction] = value.split(".");
  return whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",") + (fraction ? `.${fraction}` : "");
};
interface Batch {id: string; source: string; filename: string; row_count: number; skipped_rows: number; created_at: string;}
interface Row {row_number: number; external_id: string; reference: string; kind: string; amount: string; fee: string | null; payout: string | null; occurred_at: string; order_id: string | null; order_number: string | null; expected_amount: string | null; difference: string | null; result: string; reasons: string[]; manual_order_id: string | null;}
interface Detail extends Batch {summary: {matched: number; difference: number; unmatched: number}; items: Row[]; total: number;}
interface OrderOption {id: string; order_number: string; amount: number; created_at: string;}

export function AdminStatementsPage() {
  const {user, openLogin} = useAuth();
  const client = useQueryClient();
  const isAdmin = user?.user_role === "admin";
  const [source, setSource] = useState("newebpay");
  const [file, setFile] = useState<File | null>(null);
  const [selected, setSelected] = useState("");
  const [listOffset, setListOffset] = useState(0);
  const [result, setResult] = useState("all");
  const [offset, setOffset] = useState(0);
  const [message, setMessage] = useState("");
  const [validation, setValidation] = useState("");
  const [target, setTarget] = useState<Row | null>(null);
  const [searchDraft, setSearchDraft] = useState("");
  const [search, setSearch] = useState("");
  const [orderId, setOrderId] = useState("");
  const matchHeading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (target) { matchHeading.current?.focus(); matchHeading.current?.scrollIntoView({block: "center"}); }
  }, [target]);
  const batches = useQuery({queryKey: ["statement-imports", listOffset], queryFn: () => apiFetch<{items: Batch[]; total: number}>(`/v1/admin/financial-statements?limit=20&offset=${listOffset}`), enabled: isAdmin});
  const batchId = selected || batches.data?.items[0]?.id || "";
  const detail = useQuery({queryKey: ["statement-detail", batchId, result, offset], queryFn: () => apiFetch<Detail>(`/v1/admin/financial-statements/${batchId}?result=${result}&limit=30&offset=${offset}`), enabled: isAdmin && !!batchId});
  const options = useQuery({queryKey: ["statement-orders", search], queryFn: () => apiFetch<OrderOption[]>(`/v1/admin/financial-statements/order-options?q=${encodeURIComponent(search)}`), enabled: isAdmin && !!target});
  const upload = useMutation({mutationFn: () => {
    const body = new FormData(); body.set("source", source); body.set("file", file!);
    return apiFetch<Batch & {already_imported: boolean}>("/v1/admin/financial-statements", {method: "POST", body});
  }, onSuccess: data => {
    setSelected(data.id); setOffset(0); setResult("all"); setListOffset(0); setTarget(null);
    setMessage(data.already_imported ? "此檔案已匯入，已開啟原有紀錄。" : `已匯入 ${data.row_count} 筆明細。`);
    void client.invalidateQueries({queryKey: ["statement-imports"]});
    void client.invalidateQueries({queryKey: ["statement-detail", data.id]});
  }});
  const download = useMutation({mutationFn: ({original}: {original: boolean}) => downloadApiFile(`/v1/admin/financial-statements/${batchId}/${original ? "original" : "reconciliation.csv"}`, original ? detail.data!.filename : `對帳結果-${sources[detail.data!.source].label}.csv`)});
  const link = useMutation({mutationFn: (id: string | null) => apiFetch(`/v1/admin/financial-statements/${batchId}/rows/${target!.row_number}/match`, {method: "PUT", body: JSON.stringify({order_id: id})}), onSuccess: () => {setTarget(null); setMessage("訂單連結已更新，對帳結果已重新計算。"); void client.invalidateQueries({queryKey: ["statement-detail", batchId]});}});
  useEffect(() => {
    if (detail.data && offset > 0 && offset >= detail.data.total) setOffset(Math.max(0, Math.floor((detail.data.total - 1) / 30) * 30));
  }, [detail.data, offset]);
  if (!isAdmin) return <section className="admin-gate"><h1>金流與發票對帳</h1>{!user && <button className="button button-primary" onClick={openLogin}>管理員登入</button>}</section>;
  const changeListPage = (next: number) => {setListOffset(next); setSelected(""); setTarget(null); setOffset(0); setResult("all"); setMessage("");};
  const selectBatch = (id: string) => {setSelected(id); setOffset(0); setResult("all"); setTarget(null); setMessage(""); download.reset();};
  return <section className="admin-module-page statement-page">
    <header className="workspace-heading"><div><p className="eyebrow">RECONCILIATION / 金流與發票對帳</p><h1>匯入明細與對帳</h1></div></header><AdminNav/>
    <div className="admin-records-content">
      <nav className="statement-links" aria-label="財務工作"><Link to="/admin/finance">銷售報表</Link><Link to="/admin/invoices">發票管理</Link><Link to="/help">常見問題</Link></nav>
      <form className="admin-records-filters" onSubmit={event => {event.preventDefault(); setMessage(""); setValidation(""); if (!file || file.size > 5 * 1024 * 1024 || !file.name.toLowerCase().endsWith(sources[source].extension)) {setValidation(`請選擇 5 MB 以內的 ${sources[source].extension} 明細檔。`); return;} upload.mutate();}}>
        <label className="field"><span>明細來源</span><select aria-label="明細來源" value={source} disabled={upload.isPending} onChange={event => {setSource(event.target.value); setFile(null); setValidation(""); upload.reset();}}>{Object.entries(sources).map(([value, item]) => <option key={value} value={value}>{item.label}</option>)}</select></label>
        <label className="field"><span>原始明細檔（{sources[source].extension}，上限 5 MB）</span><input key={source} type="file" required accept={sources[source].extension} disabled={upload.isPending} onChange={event => {setFile(event.target.files?.[0] || null); setValidation(""); upload.reset();}}/></label>
        <button className="button button-primary" disabled={!file || upload.isPending}>{upload.isPending ? "匯入中…" : "匯入並核對"}</button>
      </form>
      {validation && <p className="form-error" role="alert">{validation}</p>}{upload.isError && <p className="form-error" role="alert">{upload.error.message}</p>}{message && <p role="status">{message}</p>}
      {batches.isPending ? <LoadingLines count={3}/> : batches.isError ? <DataState title="匯入紀錄無法讀取" detail={batches.error.message}/> : batches.data.total === 0 ? <DataState title="尚未匯入明細" detail=""/> : <>
        <label className="field"><span>匯入紀錄</span><select aria-label="匯入紀錄" disabled={link.isPending || upload.isPending} value={batchId} onChange={event => selectBatch(event.target.value)}>{!batches.data.items.some(batch => batch.id === batchId) && detail.data && <option value={batchId}>{detail.data.filename}</option>}{batches.data.items.map(batch => <option key={batch.id} value={batch.id}>{sources[batch.source].label} · {batch.filename} · {financeDateTime(batch.created_at)}</option>)}</select></label>
        {batches.data.total > 20 && <nav className="admin-pagination" aria-label="匯入紀錄分頁"><button className="button button-quiet" disabled={!listOffset || link.isPending || upload.isPending} onClick={() => changeListPage(Math.max(0, listOffset - 20))}>上一頁紀錄</button><span>{Math.floor(listOffset / 20) + 1}／{Math.ceil(batches.data.total / 20)}</span><button className="button button-quiet" disabled={listOffset + 20 >= batches.data.total || link.isPending || upload.isPending} onClick={() => changeListPage(listOffset + 20)}>下一頁紀錄</button></nav>}
        {detail.isPending ? <LoadingLines count={5}/> : detail.isError ? <DataState title="對帳結果無法讀取" detail={detail.error.message}/> : detail.data && <>
          <dl className="admin-records-totals"><div><dt>吻合</dt><dd>{detail.data.summary.matched} 筆</dd></div><div><dt>有差異</dt><dd>{detail.data.summary.difference} 筆</dd></div><div><dt>待連結</dt><dd>{detail.data.summary.unmatched} 筆</dd></div></dl>
          <div className="statement-actions"><button className="button button-quiet" disabled={detail.isFetching} onClick={() => void detail.refetch()}>重新核對</button><button className="button button-quiet" disabled={download.isPending} onClick={() => download.mutate({original: true})}>下載原格式明細</button><button className="button button-primary" disabled={download.isPending} onClick={() => download.mutate({original: false})}>匯出對帳結果 CSV</button></div>
          {download.isError && <p className="form-error" role="alert">{download.error.message}</p>}
          <div className="statement-filters" role="group" aria-label="對帳結果篩選">{Object.entries(results).map(([value, label]) => <button className="button button-quiet" key={value} aria-pressed={result === value} onClick={() => {setResult(value); setOffset(0);}}>{label}</button>)}</div>
          <p>共 {detail.data.total} 筆</p>
          <div className="admin-records-table" tabIndex={0} role="region" aria-label="對帳明細"><table><thead><tr>{["原檔列號／日期", "交易或發票編號", "來源金額", "網站訂單／核對金額", "差額", "對帳結果", "操作"].map(label => <th key={label} scope="col">{label}</th>)}</tr></thead><tbody>{detail.data.items.map(row => <tr key={row.row_number}><td data-label="原檔列號／日期">第 {row.row_number} 列 · {kinds[row.kind]}<br/>{row.occurred_at}</td><td data-label="交易或發票編號">{row.external_id || "—"}<br/>{row.reference}</td><td data-label="來源金額">{amount(row.amount)}{row.fee !== null && <><br/>手續費 {amount(row.fee)}</>}{row.payout !== null && <><br/>預計撥款 {amount(row.payout)}</>}</td><td data-label="網站訂單／核對金額">{row.order_number ? <Link to="/admin" search={{section: "orders", q: row.order_number}}>{row.order_number}</Link> : "—"}<br/>{amount(row.expected_amount)}{row.manual_order_id && <><br/>人工連結</>}</td><td data-label="差額">{amount(row.difference)}</td><td data-label="對帳結果">{results[row.result]}{row.reasons.map(reason => <div key={reason}>{reason}</div>)}</td><td data-label="操作"><button className="button button-quiet" onClick={() => {setTarget(row); setOrderId(row.manual_order_id || ""); setSearch(""); setSearchDraft(""); link.reset();}}>連結訂單</button></td></tr>)}</tbody></table></div>
          {detail.data.total > 30 && <nav className="admin-pagination" aria-label="對帳分頁"><button className="button button-quiet" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 30))}>上一頁</button><span>{Math.floor(offset / 30) + 1}／{Math.ceil(detail.data.total / 30)}</span><button className="button button-quiet" disabled={offset + 30 >= detail.data.total} onClick={() => setOffset(offset + 30)}>下一頁</button></nav>}
        </>}
      </>}
      {target && <section className="admin-records-form" aria-label="連結網站訂單"><h2 ref={matchHeading} tabIndex={-1}>第 {target.row_number} 列 · 連結網站訂單</h2><form className="statement-actions" onSubmit={event => {event.preventDefault(); setSearch(searchDraft.trim()); setOrderId("");}}><label className="field"><span>搜尋訂單編號</span><input value={searchDraft} maxLength={120} onChange={event => setSearchDraft(event.target.value)}/></label><button className="button button-quiet">搜尋</button></form><label className="field"><span>網站訂單</span><select aria-label="網站訂單" value={orderId} onChange={event => setOrderId(event.target.value)}><option value="">請選擇訂單</option>{options.data?.map(order => <option key={order.id} value={order.id}>{order.order_number} · {amount(String(order.amount))} · {financeDateTime(order.created_at)}</option>)}</select></label>{options.isError && <p className="form-error" role="alert">{options.error.message}</p>}{options.data?.length === 0 && <p role="status">沒有符合的網站訂單</p>}{link.isError && <p className="form-error" role="alert">{link.error.message}</p>}<div className="statement-actions"><button className="button button-primary" disabled={!orderId || link.isPending} onClick={() => link.mutate(orderId)}>儲存連結並核對</button>{target.manual_order_id && <button className="button button-quiet" disabled={link.isPending} onClick={() => link.mutate(null)}>還原自動核對</button>}<button className="button button-quiet" onClick={() => setTarget(null)}>取消</button></div></section>}
    </div>
  </section>;
}
