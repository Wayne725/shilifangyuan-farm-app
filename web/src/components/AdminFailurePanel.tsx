import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { formatDateTime } from "../lib/api";
import { useAdminPanelAccess, type AdminPanelAccess } from "../lib/admin-panel-access";

import { OrderCategoryFilter } from "./OrderCategoryFilter";
import { AdminReconciliationStatus } from "./AdminReconciliationStatus";
import { orderCategoryLabels, type IssueOrderCategory } from "../lib/order-category";
import { OptionalMark } from "./OptionalMark";

interface FailedJob {
  order_category?: IssueOrderCategory;
  id: string; event_type: string; attempts: number; error: string;
  retryable: boolean; retry_blocker: string;
  delivery_unknown?: boolean; requires_delivery_confirmation?: boolean;
  delivery_provider?: string | null; provider_message_id?: string | null;
  title?: string; description?: string; next_action?: string;
  order_id?: string | null; order_number?: string | null; created_at?: string;
}

const jobKinds = { all: "全部問題", invoice: "電子發票", refund: "退款", email: "通知信", other: "其他系統工作" };

export function AdminFailurePanel() {
  const access = useAdminPanelAccess();
  const [input, setInput] = useState("");
  const [search, setSearch] = useState("");
  const [kind, setKind] = useState("all");
  const [orderCategory, setOrderCategory] = useState<IssueOrderCategory>("all");
  const [offset, setOffset] = useState(0);
  const [notice, setNotice] = useState("");
  const query = useQuery({
    queryKey: ["admin-failed-jobs", search, kind, offset, orderCategory],
    queryFn: ({ signal }) => access.request<{ items: FailedJob[]; total: number }>(`/v1/admin/failed-jobs?q=${encodeURIComponent(search)}&kind=${kind}&order_category=${orderCategory}&limit=10&offset=${offset}`, { signal }),
    enabled: !access.denied,
    refetchInterval: access.denied ? false : 30000,
    retry: (failureCount) => !access.isDenied() && failureCount < 1,
  });
  useEffect(() => {
    if (query.data && offset > 0 && offset >= query.data.total) setOffset(Math.max(0, Math.floor((query.data.total - 1) / 10) * 10));
  }, [offset, query.data]);
  if (access.denied) return <section className="admin-failure-panel">
    <h2>待處理問題</h2>
    <p className="form-error" role="alert">管理權限已失效或已變更，請重新驗證後再操作。</p>
    <button type="button" className="button button-system" onClick={() => window.location.reload()}>重新驗證管理權限</button>
  </section>;
  return <section className="admin-failure-panel">
    <h2>待處理問題</h2>
    <AdminReconciliationStatus access={access} />
    <OrderCategoryFilter value={orderCategory} includeSystem onChange={(value) => { setOrderCategory(value); setOffset(0); setNotice(""); }} />
    <form className="failed-job-search" onSubmit={(event) => { event.preventDefault(); setSearch(input.trim()); setOffset(0); setNotice(""); }}>
      <label>搜尋問題<input type="search" maxLength={120} placeholder="訂單編號、問題編號、類型或原因" value={input} onChange={(event) => setInput(event.target.value)} /></label>
      <label>問題類型<select aria-label="問題類型" value={kind} onChange={(event) => { setKind(event.target.value); setOffset(0); setNotice(""); }}>{Object.entries(jobKinds).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <button type="submit" className="button button-system" disabled={query.isFetching}>查詢問題</button>
      <button type="button" className="button button-quiet" onClick={() => { setInput(""); setSearch(""); setKind("all"); setOffset(0); setNotice(""); }}>清除篩選</button>
    </form>
    {notice && <p role="status">{notice}</p>}
    {query.isError && <p className="form-error" role="alert">{query.error.message}</p>}
    {query.isPending && <p role="status">讀取中…</p>}
    {query.data?.items.map((job) => <FailedJobRow key={job.id} job={job} onRetry={setNotice} access={access} />)}
    {query.data?.total === 0 && <p>{search || kind !== "all" ? "沒有符合條件的問題。" : "目前沒有待處理問題。"}</p>}
    {query.data && <nav className="admin-pagination" aria-label="問題清單分頁">
      <button type="button" className="button button-quiet" disabled={!offset || query.isFetching} onClick={() => setOffset(Math.max(0, offset - 10))}>上一頁</button>
      <span>共 {query.data.total} 筆{query.data.total > 0 && `・第 ${Math.floor(offset / 10) + 1} 頁`}</span>
      <button type="button" className="button button-quiet" disabled={offset + 10 >= query.data.total || query.isFetching} onClick={() => setOffset(offset + 10)}>下一頁</button>
    </nav>}
  </section>;
}

function FailedJobRow({ job, onRetry, access }: { job: FailedJob; onRetry: (message: string) => void; access: AdminPanelAccess }) {
  const [reason, setReason] = useState("");
  const [confirmDeliveryRisk, setConfirmDeliveryRisk] = useState(false);
  const client = useQueryClient();
  const retry = useMutation({
    mutationFn: () => access.request<{ message: string }>(`/v1/admin/failed-jobs/${job.id}/retry`, { method: "POST", body: JSON.stringify({ reason,
      ...(job.requires_delivery_confirmation ? { confirm_delivery_risk: confirmDeliveryRisk } : {}) }) }),
    onSuccess: (result) => { if (!access.isDenied()) { onRetry(result.message); return client.invalidateQueries({ queryKey: ["admin-failed-jobs"] }); } },
  });
  const title = job.title || ({ send_email: "平台通知信", "invoice.issue_requested": "電子發票開立", "refund.requested": "退款處理" } as Record<string, string>)[job.event_type] || "系統背景工作";
  return <article className="failed-job">
    <header><h3>{title}</h3><span>已嘗試 {job.attempts} 次</span></header>
    {job.delivery_unknown && <p className="status-chip" role="status">寄送結果未知</p>}
    {job.description && <p className="failed-job-description">{job.description}</p>}
    <dl>
      {job.order_category && <div><dt>訂單分類</dt><dd>{job.order_category === "system" ? "其他系統工作" : orderCategoryLabels[job.order_category]}</dd></div>}
      <div><dt>訂單編號</dt><dd>{job.order_number || "未關聯訂單"}</dd></div>
      {job.created_at && <div><dt>發生時間</dt><dd>{formatDateTime(job.created_at)}</dd></div>}
      <div><dt>問題編號</dt><dd>{job.id}</dd></div>
      <div><dt>失敗原因</dt><dd>{job.error}</dd></div>
      {job.delivery_unknown && <><div><dt>郵件供應商</dt><dd>{({ resend: "Resend", mailersend: "MailerSend" } as Record<string, string>)[job.delivery_provider || ""] || "未確認"}</dd></div>
        <div><dt>供應商編號</dt><dd>{job.provider_message_id || "未取得"}</dd></div></>}
      {!job.retryable && <div><dt>目前限制</dt><dd>{job.retry_blocker}</dd></div>}
      {job.next_action && <div><dt>處理方式</dt><dd>{job.next_action}</dd></div>}
    </dl>
    {job.order_number && <Link className="button button-quiet" to="/admin" search={{ section: "orders", q: job.order_number }}>查看訂單</Link>}
    {job.retryable && <form className="admin-order-search" onSubmit={(event) => { event.preventDefault(); retry.mutate(); }}>
      <label>處理原因<OptionalMark /><input maxLength={1000} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
      {job.requires_delivery_confirmation && <label className="check-field"><input type="checkbox" required checked={confirmDeliveryRisk}
        onChange={(event) => setConfirmDeliveryRisk(event.target.checked)} />已查詢供應商確認未寄出，並了解重新排入仍可能重寄</label>}
      <button type="submit" className="button button-quiet" disabled={retry.isPending || (job.requires_delivery_confirmation && !confirmDeliveryRisk)}>{retry.isPending ? "排入中…" : "排入重試"}</button>
    </form>}
    {retry.isError && <p className="form-error" role="alert">{retry.error.message}</p>}
  </article>;
}
