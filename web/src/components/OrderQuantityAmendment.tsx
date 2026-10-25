import * as Dialog from "@radix-ui/react-dialog";
import { X } from "@phosphor-icons/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";

import { ApiError, apiFetch, formatDateTime, formatMoney } from "../lib/api";
import { amendmentItems, canAmendOrder, replaceAmendedOrder, type AmendableOrder } from "../lib/order-quantity-amendments";
import type { Order, OrderQuantityAmendmentQuote } from "../lib/types";
import "./OrderQuantityAmendment.css";

export function OrderQuantityAmendment({ order, disabled = false }: { order: AmendableOrder; disabled?: boolean }) {
  const client = useQueryClient();
  const id = useId();
  const [open, setOpen] = useState(false);
  const [base, setBase] = useState<AmendableOrder>(order);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [custom, setCustom] = useState<Record<string, boolean>>({});
  const [preview, setPreview] = useState<OrderQuantityAmendmentQuote | null>(null);
  const [error, setError] = useState("");
  const [needsRefresh, setNeedsRefresh] = useState(false);
  const [result, setResult] = useState("");
  const requestId = useRef("");
  const busyRef = useRef(false);
  const lines = amendmentItems(base);
  const maxQuantity = base.sales_channel === "meal_preorder" ? 99 : 999;
  const changed = lines.some((line) => Number(draft[line.id || ""]) !== line.quantity);
  const valid = lines.every((line) => /^\d+$/.test(draft[line.id || ""] || "") && Number(draft[line.id || ""]) <= maxQuantity)
    && lines.some((line) => Number(draft[line.id || ""]) > 0);
  const stale = needsRefresh || (order.revision || 0) > (base.revision || 0);
  const permitted = canAmendOrder(base) && !((order.revision || 0) >= (base.revision || 0) && !canAmendOrder(order));
  const payload = () => ({ expected_revision: base.revision, items: lines.map((line) => ({ order_item_id: line.id, quantity: Number(draft[line.id || ""]) })) });
  const handleError = (failure: Error) => {
    setError(failure.message);
    if (failure instanceof ApiError && failure.status === 409) {
      setNeedsRefresh(true); setPreview(null);
      void client.invalidateQueries();
    }
  };
  const quote = useMutation({
    mutationFn: () => apiFetch<OrderQuantityAmendmentQuote>(`/v1/orders/${order.id}/quantity-amendments/quote`, { method: "POST", body: JSON.stringify(payload()) }),
    onSuccess: (value) => { setPreview(value); requestId.current = crypto.randomUUID(); setError(""); },
    onError: handleError,
    onSettled: () => { busyRef.current = false; },
  });
  const refresh = useMutation({
    mutationFn: () => apiFetch<AmendableOrder>("meal_event_title" in order ? `/v1/meal-orders/${order.id}` : `/v1/orders/${order.id}`),
    onSuccess: async (value) => {
      setBase(value);
      setDraft((previous) => Object.fromEntries(amendmentItems(value).map((line) => [line.id!, previous[line.id!] ?? String(line.quantity)])));
      setPreview(null); setNeedsRefresh(false);
      setError(canAmendOrder(value) ? "" : value.amendment_blocked_reason || "這筆訂單目前無法修改數量。");
      await client.invalidateQueries();
    },
    onError: handleError,
    onSettled: () => { busyRef.current = false; },
  });
  const confirm = useMutation({
    mutationFn: () => {
      if (!preview || !requestId.current) throw new Error("請先重新計算金額。");
      return apiFetch<Order>(`/v1/orders/${order.id}/quantity-amendments`, { method: "POST", body: JSON.stringify({ ...payload(), expected_total: preview.amount_total, client_request_id: requestId.current }) });
    },
    onSuccess: async (value) => {
      const accepted = preview!;
      for (const key of ["orders", "meal-orders", "returned-meal-order", "admin-orders", "admin-meal-orders", "admin-meal-event-orders"]) {
        client.setQueriesData({ queryKey: [key] }, (data: unknown) => replaceAmendedOrder(data, value, accepted));
      }
      await client.invalidateQueries();
      setResult(`數量已更新，訂單總額 ${formatMoney(value.amount_total)}。`);
      setOpen(false); setPreview(null); setError("");
    },
    onError: handleError,
    onSettled: () => { busyRef.current = false; },
  });
  const busy = quote.isPending || confirm.isPending || refresh.isPending;
  const operate = (action: () => void) => { if (busyRef.current) return; busyRef.current = true; action(); };
  const show = (next: boolean) => {
    if (busyRef.current) return;
    setOpen(next);
    if (next) {
      setBase(order); setDraft(Object.fromEntries(amendmentItems(order).map((line) => [line.id!, String(line.quantity)])));
      setCustom({}); setPreview(null); setError(""); setNeedsRefresh(false); setResult(""); requestId.current = "";
    }
  };
  const history = order.amendments || [];
  if (!canAmendOrder(order) && !open && !result && !history.length) return null;

  return <section className="order-quantity-amendment" aria-label={`訂單數量 ${order.order_number}`}>
    <Dialog.Root open={open} onOpenChange={show}>
      {canAmendOrder(order) && <Dialog.Trigger asChild><button className="button button-quiet" type="button" disabled={disabled}>修改數量</button></Dialog.Trigger>}
      <Dialog.Portal>
        <Dialog.Overlay className="quantity-amendment-overlay" />
        <Dialog.Content className="quantity-amendment-dialog" onEscapeKeyDown={(event) => { if (busyRef.current) event.preventDefault(); }} onPointerDownOutside={(event) => { if (busyRef.current) event.preventDefault(); }}>
          <Dialog.Title>{preview ? "確認數量與金額" : "修改訂單數量"}</Dialog.Title>
          <Dialog.Description className="order-number">{order.order_number}</Dialog.Description>
          {!preview ? <form onSubmit={(event) => { event.preventDefault(); if (valid && changed && permitted && !stale) operate(() => quote.mutate()); }}>
            <div className="quantity-amendment-lines">{lines.map((line, index) => {
              const value = draft[line.id!] ?? String(line.quantity);
              const isCustom = custom[line.id!] || Number(value) > 10;
              return <div className="quantity-amendment-line" key={line.id}>
                <div><strong>{line.name}</strong><span>{formatMoney(line.unit_price)}／{line.unit}</span>{line.selections && <small>{line.selections}</small>}</div>
                <div className="field"><label htmlFor={`${id}-${index}`}>{line.name}數量</label><select id={`${id}-${index}`} value={isCustom ? "custom" : value} disabled={busy || stale || !permitted} onChange={(event) => { const next = event.target.value; setCustom((current) => ({ ...current, [line.id!]: next === "custom" })); if (next !== "custom") setDraft((current) => ({ ...current, [line.id!]: next })); setError(""); }}>
                  <option value="0">移除此項</option>{Array.from({ length: 10 }, (_, number) => <option key={number + 1} value={number + 1}>{number + 1} {line.unit}</option>)}<option value="custom">自訂數量</option>
                </select></div>
                {isCustom && <label className="field quantity-amendment-custom"><span>{line.name}自訂數量</span><input type="number" min={0} max={maxQuantity} step={1} inputMode="numeric" value={value} disabled={busy || stale || !permitted} onChange={(event) => { setDraft((current) => ({ ...current, [line.id!]: event.target.value })); setError(""); }} /></label>}
              </div>;
            })}</div>
            {!valid && <p className="form-error" role="alert">數量須為 0～{maxQuantity} 的整數，且至少保留一項商品。</p>}
            <div className="form-actions"><button className="button button-primary" type="submit" disabled={busy || !valid || !changed || stale || !permitted}>{quote.isPending ? "重新計算中…" : "查看修改後金額"}</button><Dialog.Close asChild><button className="button button-quiet" type="button" disabled={busy}>取消修改</button></Dialog.Close></div>
          </form> : <div className="quantity-amendment-preview">
            <div className="quantity-amendment-lines">{preview.items.map((line) => <div className="quantity-amendment-preview-line" key={line.order_item_id}><strong>{line.product_name}</strong><span>{line.previous_quantity} → {line.quantity === 0 ? "移除" : `${line.quantity} ${line.unit_label}`}</span><b>{formatMoney(line.subtotal)}</b></div>)}</div>
            <dl className="quantity-amendment-totals"><div><dt>原訂單總額</dt><dd>{formatMoney(preview.previous_total)}</dd></div><div><dt>優惠折抵</dt><dd>−{formatMoney(preview.discount_amount)}</dd></div><div><dt>運費</dt><dd>{formatMoney(preview.shipping_fee)}</dd></div><div><dt>內含稅額</dt><dd>{formatMoney(preview.tax_amount)}</dd></div><div className="quantity-amendment-total"><dt>修改後總額</dt><dd>{formatMoney(preview.amount_total)}</dd></div></dl>
            <div className="form-actions"><button className="button button-primary" type="button" disabled={busy || stale || !permitted} onClick={() => operate(() => confirm.mutate())}>{confirm.isPending ? "儲存修改中…" : "確認修改"}</button><button className="button button-quiet" type="button" disabled={busy} onClick={() => { setPreview(null); setError(""); requestId.current = ""; }}>返回調整</button></div>
          </div>}
          {error && <p className="form-error" role="alert">{error}</p>}
          {stale && <p className="form-error" role="alert">訂單資料已變動，請重新核對後再計算金額。</p>}
          {!permitted && !error && <p className="form-error" role="alert">{order.amendment_blocked_reason || base.amendment_blocked_reason || "這筆訂單目前無法修改數量。"}</p>}
          {(stale || !permitted) && <button className="button button-system" type="button" disabled={busy} onClick={() => operate(() => refresh.mutate())}>{refresh.isPending ? "核對中…" : "重新核對訂單"}</button>}
          <Dialog.Close asChild><button className="icon-button quantity-amendment-close" type="button" disabled={busy} aria-label="關閉數量修改"><X size={20} /></button></Dialog.Close>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
    {result && <p className="form-success" role="status">{result}</p>}
    {history.length > 0 && <details className="quantity-amendment-history"><summary>數量修改紀錄（{history.length} 次）</summary><ol>{history.map((entry) => <li key={entry.id}><strong>第 {entry.revision} 版 · {formatDateTime(entry.created_at)}</strong>{entry.before_snapshot.amount_total !== undefined && entry.after_snapshot.amount_total !== undefined && <span>{formatMoney(entry.before_snapshot.amount_total)} → {formatMoney(entry.after_snapshot.amount_total)}</span>}{entry.after_snapshot.items && <span>{entry.after_snapshot.items.map((item) => `${item.product_name || "商品"} × ${item.quantity ?? "—"}`).join("、")}</span>}</li>)}</ol></details>}
  </section>;
}
