import { Money } from "@phosphor-icons/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { apiFetch, formatDateTime, formatMoney } from "../lib/api";
import type { Order } from "../lib/types";

type CashOrder = Pick<Order, "id" | "order_number" | "amount_total" | "payment_method" | "available_actions"> & { onsite_payment?: { method: string; reference: string; paid_at: string } | null };
type Operation = "cash-payment" | "cash-unpaid" | "cash-refund";

export function AdminCashPayment({ order, onDone }: { order: CashOrder; onDone?: () => void }) {
  const queryClient = useQueryClient();
  const [operation, setOperation] = useState<Operation | null>(null);
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [method, setMethod] = useState("cash");
  const [reference, setReference] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const refund = operation === "cash-refund";
  const correction = operation === "cash-unpaid";
  const canReceive = order.available_actions.includes("confirm_cash_payment");
  const canCorrect = order.available_actions.includes("mark_cash_unpaid");
  const canRefund = order.available_actions.includes("confirm_cash_refund");
  const valid = confirmed && Number(amount) === order.amount_total && order.amount_total > 0 && reason.trim().length > 0;
  const mutation = useMutation({
    mutationFn: () => {
      if (!valid || !operation) throw new Error("請核對金額與處理原因");
      return apiFetch<Order>(`/v1/orders/${order.id}/admin/${operation}`, { method: "POST", body: JSON.stringify({ amount: Number(amount), reason: reason.trim(), collection_method: method, reference: reference.trim() }) });
    },
    onSuccess: async () => {
      setOperation(null); setAmount(""); setReason(""); setReference(""); setConfirmed(false);
      queryClient.removeQueries({ queryKey: ["meal-pickup-credential"] });
      await queryClient.invalidateQueries();
      onDone?.();
    },
  });
  const open = (next: Operation) => { setOperation(next); setConfirmed(false); mutation.reset(); };
  if ((!canReceive && !canCorrect && !canRefund) || order.payment_method !== "cash_on_pickup") return null;
  return <section className="admin-cash-payment" aria-label={`現場付款對帳 ${order.order_number}`}>
    {order.onsite_payment && <p>已收款：{({ cash: "現金", pos: "POS 刷卡", qr: "現場 QR Code" } as Record<string, string>)[order.onsite_payment.method] || "現場付款"} · {formatDateTime(order.onsite_payment.paid_at)}{order.onsite_payment.reference && ` · ${order.onsite_payment.reference}`}</p>}
    {!operation ? <div className="form-actions">
      {canReceive && <button className="button button-system" type="button" onClick={() => open("cash-payment")}><Money size={18} />標記已付款</button>}
      {canCorrect && <button className="button button-quiet" type="button" onClick={() => open("cash-unpaid")}>更正為未付款</button>}
      {canRefund && <button className="button button-quiet" type="button" onClick={() => open("cash-refund")}>確認現場退款</button>}
    </div> : <form onSubmit={event => { event.preventDefault(); if (valid && !mutation.isPending) mutation.mutate(); }}>
      <strong>{correction ? "更正為未付款" : refund ? "確認現場退款" : "標記已付款"} · <span className="order-number">{order.order_number}</span> · {formatMoney(order.amount_total)}</strong>
      <div className="field-grid two-columns">
        <label className="field"><span>{correction ? "核對訂單金額" : refund ? "實退金額" : "實收金額"}</span><input type="number" min={1} step={1} required value={amount} onChange={event => setAmount(event.target.value)} /></label>
        <label className="field"><span>處理原因</span><input required maxLength={500} value={reason} onChange={event => setReason(event.target.value)} /></label>
        {!correction && !refund && <><label className="field"><span>現場收款方式</span><select aria-label="現場收款方式" value={method} onChange={event => setMethod(event.target.value)}><option value="cash">現金</option><option value="pos">POS 刷卡</option><option value="qr">現場 QR Code</option></select></label><label className="field"><span>POS 單號／收款參考（選填）</span><input maxLength={100} value={reference} onChange={event => setReference(event.target.value)} /></label></>}
      </div>
      <label className="cash-confirmation"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />{correction ? "已核對此筆誤標付款，實際尚未收款" : refund ? "已核對款項實際退還給顧客" : "已核對上述款項確實收到"}</label>
      <div className="form-actions"><button className="button button-system" type="submit" disabled={!valid || mutation.isPending}>{mutation.isPending ? "記錄中…" : correction ? "確認更正為未付款" : refund ? "確認已退款" : "確認已付款"}</button><button className="button button-quiet" type="button" disabled={mutation.isPending} onClick={() => setOperation(null)}>取消操作</button></div>
      {mutation.isError && <p className="form-error" role="alert">{mutation.error.message}</p>}
    </form>}
  </section>;
}
