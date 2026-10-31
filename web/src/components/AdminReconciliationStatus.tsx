import { useQuery } from "@tanstack/react-query";
import { formatDateTime } from "../lib/api";
import type { AdminPanelAccess } from "../lib/admin-panel-access";
import "./AdminReconciliationStatus.css";

interface Run {
  id: number; source: "job" | "internal" | "request" | "periodic";
  status: "running" | "succeeded" | "partial_failure" | "cancelled";
  started_at: string; progress_at: string; finished_at: string | null;
  current_phase: string | null; deferred_reason: string | null;
  phases: Record<string, { status: string; error_type?: string | null }>;
}

interface BackgroundStatus {
  current: Run | null; last_success: Run | null; long_running: boolean;
  backlog: Array<{ kind: string; count: number; oldest_at: string | null }>;
  cleanup?: {
    enabled: boolean; retention_days: number; status: "never_run" | "running" | "succeeded" | "failed";
    last_finished_at: string | null; last_success_at: string | null; deleted_count: number;
    error_type: string | null; oldest_eligible_at: string | null;
  };
}

const runLabels = { running: "執行中", succeeded: "本輪巡檢完成", partial_failure: "部分階段未完成", cancelled: "本輪已中止" };
const sourceLabels = { job: "結算工作", internal: "內部結算入口", request: "網站請求補跑", periodic: "定期驅動" };
const cleanupLabels = { never_run: "等待首次清理", running: "清理中", succeeded: "最近清理完成", failed: "清理未完成" };
const phaseLabels: Record<string, string> = {
  expired_payments: "付款補查", activity_charges: "活動費用", cash_orders: "現場付款保留", proposals: "共同購買提案",
  campaigns: "共同購買結算", member_proposals: "社員提案結算", activities: "活動出席結算", meal_events: "便當場次結算",
  meal_event_generation: "便當排程產生", outbox: "通知與外部工作", monitoring: "監測紀錄",
};
const backlogLabels: Record<string, string> = {
  payments_due: "待補查付款", meal_events_due: "待結算便當場次", outbox_due: "待處理通知工作", outbox_delayed: "等待重試時間",
  outbox_processing: "處理中工作", outbox_stale: "長時間處理中", outbox_failed: "失敗工作", email_unknown: "寄送結果未知",
  activities_due: "待結算活動", member_proposals_due: "待結算社員提案",
};
const dateLabel = (value?: string | null) => value ? formatDateTime(value) : "尚無紀錄";

export function AdminReconciliationStatus({ access }: { access: AdminPanelAccess }) {
  const query = useQuery({
    queryKey: ["admin-reconciliation-status"],
    queryFn: ({ signal }) => access.request<BackgroundStatus>("/v1/admin/reconciliation-status", { signal }),
    enabled: !access.denied,
    refetchInterval: access.denied ? false : 30000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: false,
    retry: false,
  });
  const current = query.data?.current;
  const failures = Object.entries(current?.phases || {}).filter(([, phase]) => ["failed", "cancelled"].includes(phase.status));
  return <section className="admin-reconciliation-status" aria-label="背景結算狀態">
    <header><h3>背景結算狀態</h3><button type="button" className="button button-quiet" disabled={query.isFetching}
      onClick={() => { void query.refetch(); }}>更新結算狀態</button></header>
    {query.isPending && <p role="status">讀取背景狀態中…</p>}
    {query.isError && <p className="form-error" role="alert">{query.error.message}</p>}
    {query.data && <>
      {query.isError && <p role="status">目前顯示上次取得的狀態</p>}
      <dl>
        <div><dt>最近一輪</dt><dd>{current ? runLabels[current.status] : "尚無巡檢紀錄"}</dd></div>
        <div><dt>觸發來源</dt><dd>{current ? sourceLabels[current.source] : "尚無紀錄"}</dd></div>
        <div><dt>開始時間</dt><dd>{dateLabel(current?.started_at)}</dd></div>
        <div><dt>最後更新</dt><dd>{dateLabel(current?.progress_at)}</dd></div>
        <div><dt>本輪完成</dt><dd>{dateLabel(current?.finished_at)}</dd></div>
        <div><dt>最近完整巡檢</dt><dd>{dateLabel(query.data.last_success?.finished_at)}</dd></div>
        <div><dt>完整巡檢來源</dt><dd>{query.data.last_success ? sourceLabels[query.data.last_success.source] : "尚無紀錄"}</dd></div>
        {current?.current_phase && <div><dt>目前階段</dt><dd>{phaseLabels[current.current_phase] || "其他階段"}</dd></div>}
        {current?.deferred_reason && <div><dt>開始紀錄</dt><dd>首段交易結束後補記</dd></div>}
      </dl>
      {query.data.long_running && <p className="form-error" role="status">長時間未更新，執行結果待確認</p>}
      {failures.length > 0 && <dl aria-label="未完成階段">{failures.map(([name, phase]) =>
        <div key={name}><dt>{phaseLabels[name] || "其他階段"}</dt><dd>{phase.error_type || "已中止"}</dd></div>)}</dl>}
      <dl className="reconciliation-backlog" aria-label="背景待辦數量">{query.data.backlog.map(item =>
        <div key={item.kind}><dt>{backlogLabels[item.kind] || "其他待辦"}</dt><dd>{item.count}</dd></div>)}</dl>
      {query.data.cleanup && <section className="reconciliation-retention" aria-label="監測紀錄保留">
        <h4>監測紀錄保留</h4>
        <dl>
          <div><dt>保留期間設定</dt><dd>{query.data.cleanup.retention_days} 天</dd></div>
          <div><dt>清理狀態</dt><dd>{query.data.cleanup.enabled ? cleanupLabels[query.data.cleanup.status] : "已停用"}</dd></div>
          <div><dt>最近清理時間</dt><dd>{dateLabel(query.data.cleanup.last_finished_at)}</dd></div>
          <div><dt>最近清理成功</dt><dd>{dateLabel(query.data.cleanup.last_success_at)}</dd></div>
          <div><dt>最近清理刪除</dt><dd>{query.data.cleanup.deleted_count} 筆</dd></div>
          <div><dt>待清理最舊時間</dt><dd>{query.data.cleanup.oldest_eligible_at ? dateLabel(query.data.cleanup.oldest_eligible_at) : "目前無過期紀錄"}</dd></div>
          {query.data.cleanup.error_type && <div><dt>清理未完成類別</dt><dd>{query.data.cleanup.error_type}</dd></div>}
        </dl>
      </section>}
    </>}
  </section>;
}
