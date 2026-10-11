import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { apiFetch } from "../lib/api";
import type { Order } from "../lib/types";
import { AdminReasonChoices } from "./AdminReasonChoices";

export function AdminInvoiceAdjustment({ order }: { order: Order }) {
  const [reason, setReason] = useState("");
  const client = useQueryClient();
  const cancel = useMutation({
    mutationFn: () => apiFetch<{ status: string; message: string }>(`/v1/admin/orders/${order.id}/invoice/void`, { method: "POST", body: JSON.stringify({ reason }) }),
    onSuccess: () => Promise.all(["admin-orders", "admin-meal-orders", "admin-meal-operations-summary", "meal-sales-report"].map((key) => client.invalidateQueries({ queryKey: [key] }))),
  });
  if (order.payment_status !== "refunded" || order.invoice?.provider !== "fanyu" || order.invoice.status === "voided") return null;
  return <details className="admin-extra-actions"><summary>發票作廢處理</summary><div className="operation-invoice-query">
    <AdminReasonChoices label="人工處理原因" value={reason} onChange={setReason} disabled={cancel.isPending} maxLength={20} choices={[{ label: "全額退款", value: "全額退款，核對作廢" }, { label: "核對作廢", value: "客服核對作廢結果" }]} />
    <button type="button" disabled={!reason.trim() || cancel.isPending} onClick={() => cancel.mutate()}>人工作廢／核對汎宇結果</button>
    {cancel.isSuccess && <p>{cancel.data.message}</p>}
    {cancel.isError && <p className="form-error">{cancel.error.message}</p>}
  </div></details>;
}
