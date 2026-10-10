import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { apiFetch } from "../lib/api";
import type { Order } from "../lib/types";

export function AdminInvoiceAdjustment({ order }: { order: Order }) {
  const [reason, setReason] = useState("");
  const client = useQueryClient();
  const cancel = useMutation({
    mutationFn: () => apiFetch<{ status: string; message: string }>(`/v1/admin/orders/${order.id}/invoice/void`, { method: "POST", body: JSON.stringify({ reason }) }),
    onSuccess: () => Promise.all(["admin-orders", "admin-meal-orders", "admin-meal-operations-summary", "meal-sales-report"].map((key) => client.invalidateQueries({ queryKey: [key] }))),
  });
  if (order.payment_status !== "refunded" || order.invoice?.provider !== "fanyu" || order.invoice.status === "voided") return null;
  return <div className="operation-invoice-query">
    <label>人工處理原因（最多 20 字）<input maxLength={20} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
    <button type="button" disabled={!reason.trim() || cancel.isPending} onClick={() => cancel.mutate()}>人工作廢／核對汎宇結果</button>
    {cancel.isSuccess && <p>{cancel.data.message}</p>}
    {cancel.isError && <p className="form-error">{cancel.error.message}</p>}
  </div>;
}
