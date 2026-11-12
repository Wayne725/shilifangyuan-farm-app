import * as Dialog from "@radix-ui/react-dialog";
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { apiFetch, formatMoney } from "../lib/api";
import { taipeiDate } from "../lib/meal-time";
import { OptionalMark } from "./OptionalMark";
import "./OrderCancellation.css";

type Period = "lunch" | "dinner";
type EventPreview = { id: string; title: string; period: Period; order_count: number; paid_count: number; paid_total: number; blocked: boolean };
type Preview = { events: EventPreview[]; preview_token: string };
type Result = { cancelled_event_count: number; order_count: number; paid_count: number; paid_total: number };

export function MealDisasterCancellation({ onDone }: { onDone: () => void }) {
  const [date, setDate] = useState(() => taipeiDate(Date.now()));
  const [periods, setPeriods] = useState<Period[]>(["lunch", "dinner"]);
  const [reason, setReason] = useState("颱風停止上班上課");
  const [announcement, setAnnouncement] = useState("颱風期間暫停供餐");
  const [confirm, setConfirm] = useState(false);
  const selection = { service_date: date, periods };
  const preview = useMutation({
    mutationFn: (body: typeof selection) => apiFetch<Preview>("/v1/admin/meal-disaster/preview", {
      method: "POST", body: JSON.stringify(body),
    }),
  });
  const cancel = useMutation({
    mutationFn: (token: string) => apiFetch<Result>("/v1/admin/meal-disaster/cancel", {
      method: "POST", body: JSON.stringify({ ...selection, preview_token: token, reason, announcement }),
    }),
    onSuccess: () => { setConfirm(false); preview.reset(); onDone(); },
  });
  const events = preview.data?.events || [];
  const paid = events.reduce((sum, event) => sum + event.paid_count, 0);
  const amount = events.reduce((sum, event) => sum + event.paid_total, 0);
  const canConfirm = events.length > 0 && !events.some(event => event.blocked);
  return <section className="admin-master-form social-form">
    <h2>颱風／天災停餐</h2>
    <p>停班停課尚未確認時，可到「場次」暫停新預訂。</p>
    <form onSubmit={event => { event.preventDefault(); cancel.reset(); preview.mutate(selection); }}>
      <div className="field-grid two-columns">
        <label className="field"><span>停餐日期</span><input type="date" required value={date} disabled={preview.isPending || cancel.isPending} onChange={event => { setDate(event.target.value); preview.reset(); cancel.reset(); }} /></label>
        <fieldset><legend>餐別</legend>
          {(["lunch", "dinner"] as const).map(period => <label key={period} className="check-field">
            <input type="checkbox" checked={periods.includes(period)} disabled={preview.isPending || cancel.isPending}
              onChange={event => { setPeriods(current => event.target.checked ? [...current, period] : current.filter(value => value !== period)); preview.reset(); cancel.reset(); }} />
            {period === "lunch" ? "午餐" : "晚餐"}
          </label>)}
        </fieldset>
        <label className="field"><span>停餐原因<OptionalMark /></span><input maxLength={2000} value={reason} onChange={event => setReason(event.target.value)} /></label>
        <label className="field"><span>便當頁公告<OptionalMark /></span><input maxLength={120} value={announcement} onChange={event => setAnnouncement(event.target.value)} /></label>
      </div>
      <button type="submit" className="button button-quiet" disabled={!date || !periods.length || preview.isPending || cancel.isPending}>
        {preview.isPending ? "查詢中…" : "預覽影響場次"}
      </button>
    </form>
    {preview.isError && <p className="form-error" role="alert">{preview.error.message}</p>}
    {preview.data && (!events.length ? <p>所選日期與餐別沒有可取消的場次。</p> : <ul>
      {events.map(event => <li key={event.id}>{event.title} · {event.period === "lunch" ? "午餐" : "晚餐"} · {event.order_count} 筆訂單 · 已付款 {formatMoney(event.paid_total)}{event.blocked && " · 已有取餐紀錄"}</li>)}
    </ul>)}
    {cancel.data && <p className="form-success" role="status">已取消 {cancel.data.cancelled_event_count} 個場次、{cancel.data.order_count} 筆訂單，已安排 {cancel.data.paid_count} 筆退款，共 {formatMoney(cancel.data.paid_total)}。</p>}
    {canConfirm && <Dialog.Root open={confirm} onOpenChange={open => { if (!cancel.isPending) setConfirm(open); }}>
      <Dialog.Trigger asChild><button className="button button-primary" type="button">確認停餐</button></Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="admin-cancel-overlay" />
        <Dialog.Content className="admin-cancel-dialog">
          <Dialog.Title>取消 {date} 的 {events.length} 個場次</Dialog.Title>
          <Dialog.Description>將退款 {paid} 筆、共 {formatMoney(amount)}，無法復原。所有受影響的買家會收到取消通知。</Dialog.Description>
          {cancel.isError && <p className="form-error" role="alert">{cancel.error.message}</p>}
          <div className="form-actions">
            <Dialog.Close asChild><button type="button" className="button button-quiet" disabled={cancel.isPending}>返回</button></Dialog.Close>
            <button type="button" className="button button-primary" disabled={cancel.isPending}
              onClick={() => { if (preview.data) cancel.mutate(preview.data.preview_token); }}>{cancel.isPending ? "處理中…" : "取消場次並退款"}</button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>}
  </section>;
}
