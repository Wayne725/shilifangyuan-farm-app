import { useMutation, useQuery } from "@tanstack/react-query";
import { ApiError, apiFetch, downloadApiFile, formatMoney } from "../lib/api";
interface Summary { order_id?: string; requests: number; orders: number; pending: number; approved: number; rejected: number; pending_amount: number; approved_amount: number; refund_pending: number; refunded: number; refund_failed: number; }
export function MealCancellationSummary({ filters, status, orderId }: { filters: string; status: string; orderId?: string }) {
  const summary = useQuery({ queryKey: ["meal-cancellation-summary", filters], queryFn: () => apiFetch<Summary>(`/v1/admin/meal-cancellation-summary?${filters}`), refetchInterval: 15000 });
  const download = useMutation({ mutationFn: async () => {
    const params = new URLSearchParams(filters);
    params.set("review_status", status);
    if (!orderId) return downloadApiFile(`/v1/admin/meal-cancellations.csv?${params}`, "便當退單明細.csv");
    params.delete("order_id");
    params.delete("search");
    try {
      // Older APIs ignore the exact filter on the general report.
      await downloadApiFile(`/v1/admin/meal-cancellations/orders/${encodeURIComponent(orderId)}.csv?${params}`, "便當退單明細.csv");
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) throw new Error("單筆退單匯出暫時無法使用，請稍後重試。");
      throw error;
    }
  } });
  const data = summary.isError || (orderId && summary.data?.order_id !== orderId) ? undefined : summary.data;
  return <section className="meal-cancellation-summary" aria-label="退單統整">
    <div className="admin-cancellation-metrics">{data && <>{[["待審核", data.pending], ["已核准", data.approved], ["未核准", data.rejected], ["核准退單金額", formatMoney(data.approved_amount)], ["退款處理中", formatMoney(data.refund_pending)], ["已完成退款", formatMoney(data.refunded)]].map(([label, value]) => <div key={label}><span>{label}</span><strong>{value}</strong></div>)}</>}</div>
    {summary.isError && <p className="form-error" role="alert">{summary.error.message}</p>}
    {!summary.isError && summary.data && !data && <p className="form-error" role="alert">單筆退單統整暫時無法讀取。</p>}
    <button className="button button-quiet" type="button" disabled={download.isPending} onClick={() => download.mutate()}>匯出退單明細</button>{download.isError && <p className="form-error" role="alert">{download.error.message}</p>}
  </section>;
}
