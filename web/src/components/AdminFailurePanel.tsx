import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { apiFetch } from "../lib/api";

interface FailedJob {
  id: string; event_type: string; attempts: number; error: string;
  retryable: boolean; retry_blocker: string;
}

export function AdminFailurePanel() {
  const [offset, setOffset] = useState(0);
  const query = useQuery({ queryKey: ["admin-failed-jobs", offset], queryFn: () => apiFetch<{ items: FailedJob[]; total: number }>(`/v1/admin/failed-jobs?limit=10&offset=${offset}`), refetchInterval: 30000 });
  return <section className="admin-failure-panel">
    <h2>失敗工作</h2>
    <p>發票由汎宇寄送。開票重試會先查銷貨單；過期驗證信不在這裡重寄。</p>
    {query.isError && <p className="form-error">{query.error.message}</p>}
    {query.isPending && <p>讀取中…</p>}
    {query.data?.items.map((job) => <FailedJobRow key={job.id} job={job} />)}
    {query.data?.total === 0 && <p>目前沒有失敗工作。</p>}
    {query.data && <nav className="admin-pagination" aria-label="失敗工作分頁">
      <button type="button" className="button button-quiet" disabled={!offset || query.isFetching} onClick={() => setOffset(Math.max(0, offset - 10))}>上一頁</button>
      <span>共 {query.data.total} 筆</span>
      <button type="button" className="button button-quiet" disabled={offset + 10 >= query.data.total || query.isFetching} onClick={() => setOffset(offset + 10)}>下一頁</button>
    </nav>}
  </section>;
}

function FailedJobRow({ job }: { job: FailedJob }) {
  const [reason, setReason] = useState("");
  const client = useQueryClient();
  const retry = useMutation({ mutationFn: () => apiFetch(`/v1/admin/failed-jobs/${job.id}/retry`, { method: "POST", body: JSON.stringify({ reason }) }), onSuccess: () => client.invalidateQueries({ queryKey: ["admin-failed-jobs"] }) });
  return <article className="failed-job">
    <strong>{({ send_email: "平台通知信", "invoice.issue_requested": "電子發票開立", "refund.requested": "退款處理" } as Record<string, string>)[job.event_type] || "系統工作"} · 已嘗試 {job.attempts} 次</strong>
    <p>{job.error}</p>
    {!job.retryable && <p>{job.retry_blocker}</p>}
    {job.retryable && <form className="admin-order-search" onSubmit={(event) => { event.preventDefault(); retry.mutate(); }}>
      <label>處理原因<input required maxLength={1000} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
      <button type="submit" className="button button-quiet" disabled={!reason.trim() || retry.isPending}>排入重試</button>
    </form>}
    {retry.isError && <p className="form-error">{retry.error.message}</p>}
  </article>;
}
