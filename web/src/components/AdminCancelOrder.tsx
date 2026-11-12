import * as Dialog from "@radix-ui/react-dialog";
import { X } from "@phosphor-icons/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { useAuth } from "../context/AuthContext";
import { apiFetch, formatMoney } from "../lib/api";
import { adminPaymentStatusLabel, invoiceStatusLabel } from "../lib/labels";
import type { MealOrder, Order } from "../lib/types";
import "./OrderCancellation.css";
import { OptionalMark } from "./OptionalMark";

type CancelOrder = Pick<Order, "id" | "order_number" | "sales_channel" | "payment_method" | "payment_status" | "fulfillment_status" | "amount_total" | "available_actions"> & { invoice_status: string };

export function AdminCancelOrder({ order }: { order: CancelOrder }) {
  const { user } = useAuth();
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const reasonId = useId();
  const canCancel = !["expired", "failed"].includes(order.payment_status) && order.available_actions.includes("cancel");
  const cancel = useMutation({
    mutationFn: () => {
      if (!canCancel || user?.user_role !== "admin") throw new Error("請確認退單權限與訂單狀態");
      const path = order.sales_channel === "meal_preorder" ? `/v1/admin/meal-orders/${order.id}/cancel` : `/v1/orders/${order.id}/admin/cancel`;
      return apiFetch<Order | MealOrder>(path, { method: "POST", body: JSON.stringify({ reason: reason.trim() }) });
    },
    onSuccess: async () => {
      setOpen(false); setReason("");
      client.removeQueries({ queryKey: ["meal-pickup-credential"] });
      await client.invalidateQueries();
    },
  });

  if (user?.user_role !== "admin" || (!canCancel && !cancel.isSuccess)) return null;
  const result = cancel.data && order.fulfillment_status === "cancelled" ? order : cancel.data;
  const paymentResult = result?.payment_method === "cash_on_pickup" && result.payment_status === "refund_pending"
    ? "待現場退款確認" : result ? adminPaymentStatusLabel(result.payment_status) : "";

  return <section className="admin-cancel-order" aria-label={`辦理退單 ${order.order_number}`}>
    <Dialog.Root open={open} onOpenChange={(next) => { if (cancel.isPending) return; setOpen(next); if (next) { setReason(""); cancel.reset(); } }}>
      {canCancel && <Dialog.Trigger asChild><button className="button button-quiet" type="button">辦理退單</button></Dialog.Trigger>}
      <Dialog.Portal>
        <Dialog.Overlay className="admin-cancel-overlay" />
        <Dialog.Content className="admin-cancel-dialog" onEscapeKeyDown={(event) => { if (cancel.isPending) event.preventDefault(); }} onPointerDownOutside={(event) => { if (cancel.isPending) event.preventDefault(); }}>
          <Dialog.Title>確認辦理退單</Dialog.Title>
          <Dialog.Description className="order-number">{order.order_number} · {formatMoney(order.amount_total)}</Dialog.Description>
          {order.payment_status === "paid" && <p>{order.payment_method === "cash_on_pickup" ? "退單後待現場退款確認" : "取消整筆訂單並申請退款"} · {formatMoney(order.amount_total)}</p>}
          <form onSubmit={(event) => { event.preventDefault(); if (canCancel && !cancel.isPending) cancel.mutate(); }}>
            <div className="field"><label htmlFor={reasonId}>退單原因<OptionalMark /></label><textarea id={reasonId} maxLength={2000} rows={3} value={reason} disabled={cancel.isPending} onChange={(event) => setReason(event.target.value)} /></div>
            {cancel.isError && <p className="form-error" role="alert">{cancel.error.message}</p>}
            {!canCancel && <p className="form-error" role="alert">這筆訂單目前無法退單。</p>}
            <div className="form-actions"><button className="button button-danger" type="submit" disabled={!canCancel || cancel.isPending}>{cancel.isPending ? "退單處理中…" : "確認退單"}</button><Dialog.Close asChild><button className="button button-quiet" type="button" disabled={cancel.isPending}>返回</button></Dialog.Close></div>
          </form>
          <Dialog.Close asChild><button className="icon-button admin-cancel-close" type="button" disabled={cancel.isPending} aria-label="關閉退單確認"><X size={20} /></button></Dialog.Close>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
    {cancel.isSuccess && result && <p className="form-success" role="status">訂單已取消。付款狀態：{paymentResult}；發票狀態：{result.invoice_status === "external" ? "合作社現場開票" : invoiceStatusLabel(result.invoice_status)}。</p>}
  </section>;
}
