import * as Dialog from "@radix-ui/react-dialog";
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { ApiError, apiFetch, formatMoney } from "../lib/api";
import { taipeiDate } from "../lib/meal-time";
import type { MealEvent } from "../lib/types";
import "./OrderCancellation.css";

type Period = "lunch" | "dinner";
type EventPreview = {
  id: string;
  title: string;
  period: Period;
  order_count: number;
  paid_count: number;
  paid_total: number;
  blocked: boolean;
  ordering_paused: boolean;
  ordering_pause_version: number;
  ordering_pause_blocked_reason: string | null;
};
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
  const changeOrderingPause = useMutation({
    mutationFn: (event: EventPreview) => apiFetch<MealEvent>(`/v1/admin/meal-events/${event.id}/ordering-pause`, {
      method: "PATCH", body: JSON.stringify({
        paused: !event.ordering_paused,
        expected_version: event.ordering_pause_version,
      }),
    }),
    onSuccess: () => { preview.mutate(selection); onDone(); },
    onError: (error) => {
      if (error instanceof ApiError && (error.status === 409 || error.status === 404)) {
        preview.mutate(selection);
        onDone();
      }
    },
  });
  const resetPreview = () => {
    preview.reset();
    cancel.reset();
    changeOrderingPause.reset();
  };
  const busy = preview.isPending || cancel.isPending || changeOrderingPause.isPending;
  const events = preview.data?.events || [];
  const paid = events.reduce((sum, event) => sum + event.paid_count, 0);
  const amount = events.reduce((sum, event) => sum + event.paid_total, 0);
  const canConfirm = events.length > 0 && !events.some(event => event.blocked);
  return <section className="admin-master-form social-form">
    <h2>颱風／天災停餐</h2>
    <p>停班停課尚未確認時，可先在預覽中暫停新預訂。</p>
    <form onSubmit={event => {
      event.preventDefault();
      if (!date || !periods.length || busy) return;
      resetPreview();
      preview.mutate(selection);
    }}>
      <div className="field-grid two-columns">
        <label className="field"><span>停餐日期</span><input type="date" required value={date} disabled={busy} onChange={event => { setDate(event.target.value); resetPreview(); }} /></label>
        <fieldset><legend>餐別</legend>
          {(["lunch", "dinner"] as const).map(period => <label key={period} className="check-field">
            <input type="checkbox" checked={periods.includes(period)} disabled={busy}
              onChange={event => { setPeriods(current => event.target.checked ? [...current, period] : current.filter(value => value !== period)); resetPreview(); }} />
            {period === "lunch" ? "午餐" : "晚餐"}
          </label>)}
        </fieldset>
        <label className="field"><span>停餐原因</span><input maxLength={2000} value={reason} onChange={event => setReason(event.target.value)} /></label>
        <label className="field"><span>便當頁公告</span><input maxLength={120} value={announcement} onChange={event => setAnnouncement(event.target.value)} /></label>
      </div>
      <div className="form-actions">
        <button type="submit" className="button button-quiet" disabled={!date || !periods.length || busy}>
          {preview.isPending ? "查詢中…" : "預覽影響場次"}
        </button>
      </div>
    </form>
    {preview.isError && <p className="form-error" role="alert">{preview.error.message}</p>}
    {changeOrderingPause.isError && <p className="form-error" role="alert">{changeOrderingPause.error.message}</p>}
    {changeOrderingPause.data && <p className="form-success" role="status">
      {changeOrderingPause.data.title} · {changeOrderingPause.data.ordering_paused ? "已暫停新預訂" : "已恢復新預訂"}
    </p>}
    {preview.data && (!events.length ? <p role="status">所選日期與餐別沒有可處理的場次。</p> : <ul className="meal-disaster-events" role="list" aria-label="影響場次">
      {events.map(event => <li key={event.id}>
        <div>
          <p id={`disaster-event-${event.id}`}>{event.title} · {event.period === "lunch" ? "午餐" : "晚餐"} · {event.order_count} 筆訂單 · 已付款 {formatMoney(event.paid_total)}{event.blocked && " · 已有取餐紀錄"}</p>
          {event.ordering_pause_blocked_reason && <small>{event.ordering_pause_blocked_reason}</small>}
        </div>
        {event.ordering_pause_blocked_reason === null && <button type="button" className="button button-quiet" disabled={busy} aria-describedby={`disaster-event-${event.id}`}
          onClick={() => changeOrderingPause.mutate(event)}>{event.ordering_paused ? "恢復新預訂" : "暫停新預訂"}</button>}
      </li>)}
    </ul>)}
    {cancel.data && <p className="form-success" role="status">已取消 {cancel.data.cancelled_event_count} 個場次、{cancel.data.order_count} 筆訂單，已安排 {cancel.data.paid_count} 筆退款，共 {formatMoney(cancel.data.paid_total)}。</p>}
    {canConfirm && <Dialog.Root open={confirm} onOpenChange={open => { if (!cancel.isPending) setConfirm(open); }}>
      <Dialog.Trigger asChild><button className="button button-primary" type="button" disabled={busy}>確認停餐</button></Dialog.Trigger>
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
