import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { apiFetch } from "../lib/api";
import { financeDateTime } from "../lib/meal-finance";
import "./CloudPrintPanel.css";

const states = {pending: "等待發送", sending: "正在發送", submitted: "雲端已接受", printed: "已確認列印", failed: "出單失敗", unknown: "結果待確認", simulated: "模擬完成"};
type Job = {revision?: number; id: string; order_id: string; order_number: string; status: keyof typeof states; mode: "live" | "dry_run"; kind: string; reprint_of_id: string | null; attempts: number; last_error: string | null; last_result?: {source?: string}; can_query: boolean; pickup_at: string; created_at: string};
type Device = {enabled: boolean; dry_run: boolean; status: string; error: string | null};
const deviceStates: Record<string, string> = {disabled: "未啟用", dry_run: "模擬模式", online: "在線", offline: "離線", abnormal: "設備異常", unknown: "狀態待確認"};
const errors: Record<string, string> = {connection_not_established: "連線尚未建立，等待安全重試", transport_result_unknown: "傳送結果不明，請核對紙本", http_result_unknown: "雲端回應異常，請核對紙本", invalid_response: "雲端回應格式不明", missing_vendor_order_id: "雲端未提供工作編號", sender_interrupted_result_unknown: "處理程序中斷，請核對紙本", printer_configuration_changed: "模式或設備設定已變更，原工作暫停", live_activation_not_confirmed: "尚未確認密鑰更換與亞太端點", missing_printer_credentials: "設備憑證尚未設定", receipt_too_long: "內容超出單張上限，請人工處理", order_cancelled_before_send: "訂單取消，已停止發送", order_revision_superseded: "數量已更新，舊單已停止發送"};
const jobKindLabel = (job: Job) => `${({order: "訂餐單", cancellation: "取消通知", amendment: "數量修訂單"} as Record<string, string>)[job.kind] || "出單工作"}${job.reprint_of_id ? "補印" : ""}`;
const errorText = (code: string) => errors[code] || (code.startsWith("provider_rejected_") ? `飛鵝拒絕工作（${code.replace("provider_rejected_", "")}）` : code);

export function CloudPrintPanel() {
  const [filter, setFilter] = useState("");
  const [search, setSearch] = useState("");
  const [searchDraft, setSearchDraft] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [acknowledge, setAcknowledge] = useState(false);
  const [requestId, setRequestId] = useState(() => crypto.randomUUID());
  const queryClient = useQueryClient();
  const device = useQuery({queryKey: ["cloud-printer"], queryFn: () => apiFetch<Device>("/v1/admin/printers/status"), staleTime: 60_000});
  const jobs = useQuery({queryKey: ["print-jobs", filter, search, offset], queryFn: () => apiFetch<{items: Job[]; total: number}>(`/v1/admin/printers/jobs?${new URLSearchParams({...(filter ? {status: filter} : {}), ...(search ? {search} : {}), offset: String(offset), limit: "30"})}`), refetchInterval: 10_000});
  const preview = useQuery({queryKey: ["print-job", selected], queryFn: () => apiFetch<Job & {preview: string}>(`/v1/admin/printers/jobs/${selected}`), enabled: Boolean(selected), refetchInterval: selected ? 10_000 : false});
  const refresh = () => Promise.all([queryClient.invalidateQueries({queryKey: ["print-jobs"]}), queryClient.invalidateQueries({queryKey: ["print-job"]})]);
  const reprint = useMutation({mutationFn: () => apiFetch<Job>(`/v1/admin/printers/jobs/${selected}/reprint`, {method: "POST", body: JSON.stringify({request_id: requestId, reason, acknowledge_duplicate: acknowledge})}), onSuccess: () => { setRequestId(crypto.randomUUID()); return refresh(); }});
  const query = useMutation({mutationFn: () => apiFetch<Job>(`/v1/admin/printers/jobs/${selected}/query`, {method: "POST"}), onSuccess: refresh});
  const resolve = useMutation({mutationFn: (printed: boolean) => apiFetch<Job>(`/v1/admin/printers/jobs/${selected}/resolve`, {method: "POST", body: JSON.stringify({printed, reason})}), onSuccess: refresh});
  const job = preview.data;
  return <details className="cloud-print-panel" open>
    <summary><span>雲端出單 <small>CLOUD PRINT</small></span><span>{device.data ? deviceStates[device.data.status] : "讀取設備狀態"}</span></summary>
    <div className="cloud-print-body">
      <form className="cloud-print-filters" aria-label="列印紀錄查詢" onSubmit={event => {event.preventDefault(); setSearch(searchDraft.trim()); setOffset(0); setSelected(null);}}>
        <label className="field"><span>查詢列印紀錄</span><input type="search" maxLength={100} placeholder="訂單編號、姓名、餐點名稱" value={searchDraft} onChange={event => setSearchDraft(event.target.value)} /></label>
        <label className="field"><span>列印狀態</span><select aria-label="列印狀態" value={filter} onChange={event => {setFilter(event.target.value); setOffset(0); setSelected(null);}}><option value="">全部狀態</option>{Object.entries(states).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <div className="cloud-print-filter-actions"><button className="button button-primary" type="submit">查詢</button><button className="button button-quiet" type="button" onClick={() => {setSearchDraft(""); setSearch(""); setFilter(""); setOffset(0); setSelected(null);}}>清除</button><button className="button button-quiet" type="button" disabled={device.isFetching} onClick={() => void device.refetch()}>查詢設備</button></div>
      </form>
      <div className="cloud-print-result-heading"><p role="status">{jobs.data ? `共 ${jobs.data.total} 筆列印紀錄` : "讀取列印紀錄"}</p><Link to="/help">出單操作說明</Link></div>
      {device.isError && <p role="alert">{device.error.message}</p>}{device.data?.error && <p role="alert">{errorText(device.data.error)}</p>}
      {jobs.isError && <p role="alert">{jobs.error.message}</p>}
      {jobs.data?.items.length === 0 && <p role="status">沒有符合的列印工作</p>}
      <ul className="cloud-print-jobs">{jobs.data?.items.map(item => <li key={item.id}><div><strong>{item.order_number}</strong><span>{jobKindLabel(item)}{item.revision !== undefined && ` · 第 ${item.revision} 版`} · {item.mode === "dry_run" ? "模擬" : "正式"} · {states[item.status]}</span><small>取餐 {item.pickup_at} · 建立 {financeDateTime(item.created_at)}</small>{item.last_result?.source === "admin_paper_check" && <small>人工紙本核對</small>}{item.last_error && <p role="status">{errorText(item.last_error)}</p>}</div><button className="button button-quiet" onClick={() => {setSelected(item.id); setReason(""); setAcknowledge(false); setRequestId(crypto.randomUUID()); reprint.reset(); query.reset(); resolve.reset();}}>查看／補印</button></li>)}</ul>
      {jobs.data && jobs.data.total > 30 && <nav className="cloud-print-controls" aria-label="出單紀錄分頁"><button className="button button-quiet" disabled={offset === 0} onClick={() => setOffset(offset - 30)}>上一頁</button><span>{offset + 1}–{Math.min(offset + 30, jobs.data.total)}／{jobs.data.total}</span><button className="button button-quiet" disabled={offset + 30 >= jobs.data.total} onClick={() => setOffset(offset + 30)}>下一頁</button></nav>}
      {selected && <section className="cloud-print-preview" aria-label="雲端出單預覽"><button className="button button-quiet" onClick={() => setSelected(null)}>關閉預覽</button>{preview.isError && <p role="alert">{preview.error.message}</p>}{job && <><h3>{job.order_number} · {jobKindLabel(job)}{job.revision !== undefined && ` · 第 ${job.revision} 版`} · {states[job.status]}</h3><pre>{job.preview}</pre><div className="cloud-print-controls">{job.can_query && <button className="button button-quiet" disabled={query.isPending} onClick={() => query.mutate()}>重新查詢結果</button>}</div><form onSubmit={event => {event.preventDefault(); reprint.mutate();}}><label className="field"><span>補印原因</span><input required minLength={2} maxLength={200} value={reason} onChange={event => setReason(event.target.value)} /></label>{job.mode === "live" && ["submitted", "unknown"].includes(job.status) && <div className="cloud-print-controls"><button type="button" className="button button-quiet" disabled={reason.trim().length < 2 || resolve.isPending} onClick={() => resolve.mutate(true)}>確認紙本已印</button><button type="button" className="button button-quiet" disabled={reason.trim().length < 2 || resolve.isPending} onClick={() => resolve.mutate(false)}>記錄尚未看到紙本</button></div>}{["submitted", "unknown"].includes(job.status) && <label className="cloud-print-ack"><input type="checkbox" required checked={acknowledge} onChange={event => setAcknowledge(event.target.checked)} />已核對紙本，接受可能重複列印</label>}<button className="button button-primary" disabled={!device.data?.enabled || ["pending", "sending"].includes(job.status) || reprint.isPending || reprint.isSuccess}>{device.data?.dry_run ? "建立模擬補印" : "建立正式補印"}</button></form>{resolve.isSuccess && <p role="status">已記錄人工核對結果：{states[resolve.data.status]}</p>}{resolve.isError && <p role="alert">{resolve.error.message}</p>}{query.isSuccess && <p role="status">已安排查詢雲端結果</p>}{reprint.isSuccess && <p role="status">已建立{reprint.data.mode === "dry_run" ? "模擬" : "正式"}補印工作：{states[reprint.data.status]}</p>}{query.isError && <p role="alert">{query.error.message}</p>}{reprint.isError && <p role="alert">{reprint.error.message}</p>}</>}</section>}
    </div>
  </details>;
}
