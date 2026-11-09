import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { apiFetch } from "../lib/api";
import { adminPaymentStatusLabel, invoiceStatusLabel } from "../lib/labels";

interface QueryResult {
  message: string;
  status?: string;
  invoice_number?: string | null;
}

export function AdminStatusQuery({ orderId, kind, disabled = false }: {
  orderId: string;
  kind: "invoice" | "payment";
  disabled?: boolean;
}) {
  const [reason, setReason] = useState("");
  const queryClient = useQueryClient();
  const invoice = kind === "invoice";
  const query = useMutation({
    mutationFn: (queryReason: string) => apiFetch<QueryResult>(invoice
      ? `/v1/admin/orders/${orderId}/invoice/query`
      : `/v1/admin/orders/${orderId}/payment/query`, {
      method: "POST", body: JSON.stringify({ reason: queryReason }),
    }),
    onSuccess: () => Promise.all(["admin-orders", "admin-meal-orders", "admin-meal-operations-summary", "meal-sales-report"].map((key) => queryClient.invalidateQueries({ queryKey: [key] }))),
  });
  const busy = disabled || query.isPending;
  const choices = invoice
    ? [{ label: "查詢開立狀態", reason: "客服核對發票狀態" }, { label: "查詢作廢狀態", reason: "客服核對發票作廢狀態" }]
    : [{ label: "查詢付款狀態", reason: "客服核對付款狀態" }, { label: "查詢退款狀態", reason: "客服核對退款狀態" }];

  return <details className="admin-extra-actions"><summary>{invoice ? "查詢發票" : "查詢付款"}</summary>
    <form className="operation-invoice-query" aria-label={invoice ? "發票狀態查詢" : "付款狀態查詢"} onSubmit={(event) => {
      event.preventDefault();
      if (!busy) query.mutate(reason.trim());
    }}>
      <div className="admin-quick-buttons">
        {choices.map((choice) => <button className="button button-quiet" key={choice.reason} type="button" disabled={busy} onClick={() => query.mutate(choice.reason)}>{choice.label}</button>)}
      </div>
      <details className="admin-query-custom"><summary>其他查詢原因</summary>
        <label className="field"><span>{invoice ? "發票查詢原因" : "金流查詢原因"}</span>
          <textarea aria-label={invoice ? "發票查詢原因" : "金流查詢原因"} maxLength={1000} rows={2} value={reason} disabled={busy} onChange={(event) => setReason(event.target.value)} />
        </label>
        <button className="button button-quiet" type="submit" disabled={busy}>{invoice ? "重新查詢電子發票" : "重新查詢雷門付款"}</button>
      </details>
      {query.isPending && <p className="admin-query-result" role="status">{invoice ? "正在查詢汎宇發票…" : "正在查詢雷門付款／退款…"}</p>}
      {query.isSuccess && <div className="admin-query-result form-success" role="status">
        {query.data.status && <strong>{invoice ? invoiceStatusLabel(query.data.status) : adminPaymentStatusLabel(query.data.status)}{query.data.invoice_number ? ` · ${query.data.invoice_number}` : ""}</strong>}
        <p>{query.data.message}</p>
      </div>}
      {query.isError && <p className="admin-query-result form-error" role="alert">{query.error.message}</p>}
    </form>
  </details>;
}
