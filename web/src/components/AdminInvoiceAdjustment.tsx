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
    <p>全額退款：由管理員選擇作廢。已做折讓或跨期需會計核對時，請先在汎宇處理。</p>
    <label>作廢原因（最多 20 字）<input maxLength={20} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
    <button type="button" disabled={!reason.trim() || cancel.isPending} onClick={() => cancel.mutate()}>向汎宇作廢發票／核對結果</button>
    {cancel.isSuccess && <p>{cancel.data.message}</p>}
    {cancel.isError && <p className="form-error">{cancel.error.message}</p>}
  </div>;
}
