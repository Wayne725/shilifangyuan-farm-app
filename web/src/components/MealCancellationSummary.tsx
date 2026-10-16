import { useMutation, useQuery } from "@tanstack/react-query";
import { apiFetch, downloadApiFile, formatMoney } from "../lib/api";
interface Summary { requests: number; orders: number; pending: number; approved: number; rejected: number; pending_amount: number; approved_amount: number; refund_pending: number; refunded: number; refund_failed: number; }
export function MealCancellationSummary({ startsOn, endsOn, search, status }: { startsOn: string; endsOn: string; search: string; status: string }) {
  const params = new URLSearchParams({ search }); if (startsOn) params.set("starts_on", startsOn); if (endsOn) params.set("ends_on", endsOn);
  const summary = useQuery({ queryKey: ["meal-cancellation-summary", startsOn, endsOn, search], queryFn: () => apiFetch<Summary>(`/v1/admin/meal-cancellation-summary?${params}`), refetchInterval: 15000 });
  const download = useMutation({ mutationFn: () => downloadApiFile(`/v1/admin/meal-cancellations.csv?${params}&review_status=${status}`, "便當退單明細.csv") });
  return <section className="meal-cancellation-summary" aria-label="退單統整">
    <div className="admin-cancellation-metrics">{summary.data && <>{[["待審核", summary.data.pending], ["已核准", summary.data.approved], ["未核准", summary.data.rejected], ["核准退單金額", formatMoney(summary.data.approved_amount)], ["退款處理中", formatMoney(summary.data.refund_pending)], ["已完成退款", formatMoney(summary.data.refunded)]].map(([label, value]) => <div key={label}><span>{label}</span><strong>{value}</strong></div>)}</>}</div>
    {summary.isError && <p className="form-error" role="alert">{summary.error.message}</p>}
    <button className="button button-quiet" type="button" disabled={download.isPending} onClick={() => download.mutate()}>匯出退單明細</button>{download.isError && <p className="form-error" role="alert">{download.error.message}</p>}
  </section>;
}
