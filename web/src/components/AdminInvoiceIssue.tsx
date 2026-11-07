import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch, formatDateTime } from "../lib/api";
import { invoicePreferencePayload, type InvoicePreference } from "../lib/commerce";
import type { Order } from "../lib/types";
import { defaultInvoicePreference, InvoicePreferenceFields, invoicePreferenceIsValid } from "./InvoicePreferenceFields";

export function AdminInvoiceIssue({ order }: { order: Order }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [handling, setHandling] = useState<"on_payment" | "pickup_day">(order.invoice_handling === "pickup_day" ? "pickup_day" : "on_payment");
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [editPreferences, setEditPreferences] = useState(false);
  const [preference, setPreference] = useState<InvoicePreference>(() => ({
    ...defaultInvoicePreference(order.invoice_buyer_email || order.contact_email),
    buyerType: order.invoice_buyer_type,
    buyerName: order.invoice_buyer_name || "",
    buyerTaxId: order.invoice_buyer_tax_id || "",
  }));
  const valid = confirmed && reason.trim().length > 0 && (!editPreferences || invoicePreferenceIsValid(preference));
  const issue = useMutation({
    mutationFn: () => apiFetch(`/v1/admin/orders/${order.id}/invoice/issue`, { method: "POST", body: JSON.stringify({
      handling, reason: reason.trim(), confirm_not_invoiced_elsewhere: confirmed,
      ...(editPreferences ? { preferences: invoicePreferencePayload(preference) } : {}),
    }) }),
    onSuccess: async () => { setOpen(false); setConfirmed(false); await queryClient.invalidateQueries(); },
  });
  if (order.payment_status !== "paid" || order.cancelled_at || order.invoice || ["issued", "void_pending", "voided"].includes(order.invoice_status)) return null;
  return <section aria-label={`開票安排 ${order.order_number}`}>
    {order.invoice_issue_after && order.invoice_status === "pending" && <p role="status">預定開票日：{formatDateTime(order.invoice_issue_after)}</p>}
    {!open ? <button type="button" className="button button-quiet" onClick={() => { issue.reset(); setOpen(true); }}>手動安排開票</button> : <form onSubmit={event => { event.preventDefault(); if (valid && !issue.isPending) issue.mutate(); }}>
      <label className="field"><span>開票時機</span><select aria-label="開票時機" value={handling} onChange={event => setHandling(event.target.value as typeof handling)}><option value="on_payment">現在開立</option>{order.sales_channel === "meal_preorder" && <option value="pickup_day">取餐日開立</option>}</select></label>
      <label className="check-field"><input type="checkbox" checked={editPreferences} onChange={event => setEditPreferences(event.target.checked)} />修改發票資料</label>
      {editPreferences && <InvoicePreferenceFields value={preference} onChange={setPreference} showHeading={false} allowSavedCarrier={false} sectionNumber={`admin-${order.id}`} />}
      <label className="field"><span>處理原因</span><input required maxLength={500} value={reason} onChange={event => setReason(event.target.value)} /></label>
      <label className="check-field"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />已確認此筆未由其他系統開立發票</label>
      <div className="form-actions"><button type="submit" className="button button-system" disabled={!valid || issue.isPending}>{issue.isPending ? "記錄中…" : "確認開票安排"}</button><button type="button" className="button button-quiet" disabled={issue.isPending} onClick={() => setOpen(false)}>取消</button></div>
      {issue.isError && <p className="form-error" role="alert">{issue.error.message}</p>}
    </form>}
  </section>;
}
