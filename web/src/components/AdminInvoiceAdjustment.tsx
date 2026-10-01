import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { apiFetch } from "../lib/api";
import type { Order } from "../lib/types";

export function AdminInvoiceAdjustment({ order }: { order: Order }) {
  const [reason, setReason] = useState("");
  const client = useQueryClient();
  const cancel = useMutation({
    mutationFn: () => apiFetch<{ status: string; message: string }>(`/v1/admin/orders/${order.id}/invoice/void`, { method: "POST", body: JSON.stringify({ reason }) }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["admin-orders"] }),
  });
  if (order.payment_status !== "refunded" || order.invoice?.provider !== "fanyu" || order.invoice.status === "voided") return null;
  return <div className="operation-invoice-query">
    <p>新發生的全額退款確認完成後，系統會自動向汎宇申請作廢；查回確認後才顯示「已作廢」。</p>
    <p>歷史待辦或自動處理異常，可由此人工處理。送出結果不明時只核對、不重複送出；已做折讓或跨期需會計核對時，請先在汎宇處理。</p>
    <label>人工處理原因（最多 20 字）<input maxLength={20} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
    <button type="button" disabled={!reason.trim() || cancel.isPending} onClick={() => cancel.mutate()}>人工作廢／核對汎宇結果</button>
    {cancel.isSuccess && <p>{cancel.data.message}</p>}
    {cancel.isError && <p className="form-error">{cancel.error.message}</p>}
  </div>;
}
