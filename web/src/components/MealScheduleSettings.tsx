import * as Dialog from "@radix-ui/react-dialog";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { ApiError, apiFetch } from "../lib/api";
import { formatMealDateTime, mealCanChangeOrderingPause } from "../lib/meal-time";
import type { Meal, MealEvent, MealSchedule } from "../lib/types";
import { DataState, LoadingLines } from "./Shared";
import { DeleteRecordAction } from "./DeleteRecordAction";
import { MealScheduleWizard, mealPeriodLabels, scheduleDayLabel } from "./MealScheduleWizard";
import "./OrderCancellation.css";

type CalendarStatus = { available: boolean; from_date: string; to_date: string; message: string; source_url: string; update_year?: number | null };
type DisableScope = "generation_only" | "pause_generated";
type ScheduleDisablePreview = {
  preview_version: string;
  events: Array<{ id: string; title: string; service_date: string; order_count: number; ordering_status: keyof typeof orderingLabels }>;
};
const orderingLabels = { draft: "草稿", paused: "已暫停預訂", closed: "已截止", open: "開放預訂", scheduled: "尚未開放" };
export function MealScheduleSettings({ meals, events, onEventsChanged }: { meals: Meal[]; events: MealEvent[]; onEventsChanged: () => void }) {
  const queryClient = useQueryClient();
  const schedules = useQuery({
    queryKey: ["admin-meal-schedules"],
    queryFn: () => apiFetch<MealSchedule[]>("/v1/admin/meal-schedules"),
  });
  const calendar = useQuery({ queryKey: ["admin-meal-calendar-status"], queryFn: () => apiFetch<CalendarStatus>("/v1/admin/meal-schedules/calendar-status") });
  const [editing, setEditing] = useState<MealSchedule | null>(null);
  const [creating, setCreating] = useState(false);
  const [notice, setNotice] = useState("");
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["admin-meal-schedules"] });
    queryClient.invalidateQueries({ queryKey: ["meal-events"] });
    onEventsChanged();
  };
  const closeWizard = () => { setEditing(null); setCreating(false); };
  const onSaved = (scheduleCount: number, updatedCount: number) => {
    setNotice(`已${editing ? "更新" : "建立"} ${scheduleCount} 個排程。${updatedCount ? `已同步 ${updatedCount} 個未有訂單的未來場次。` : ""}`);
    closeWizard();
    refresh();
  };
  const onDisabled = (scheduleId: string) => {
    setEditing(current => current?.id === scheduleId ? null : current);
    queryClient.setQueryData<MealSchedule[]>(["admin-meal-schedules"], current => current?.map(schedule =>
      schedule.id === scheduleId ? { ...schedule, enabled: false } : schedule));
    refresh();
  };
  const onDeleted = (scheduleId: string) => {
    setEditing(current => current?.id === scheduleId ? null : current);
    queryClient.setQueryData<MealSchedule[]>(["admin-meal-schedules"], current => current?.filter(schedule => schedule.id !== scheduleId));
    refresh();
  };
  return <>
    {calendar.data && <div className="meal-calendar-status"><span className={`status-chip ${calendar.data.available ? "passed" : ""}`}>{calendar.data.available ? "工作日日曆已就緒" : "工作日日曆無法使用"}</span><span>{calendar.data.from_date} 至 {calendar.data.to_date}</span></div>}
    {calendar.data && !calendar.data.available && <p className="form-error" role="alert">{calendar.data.message}</p>}
    {calendar.isError && <p className="form-error" role="alert">{calendar.error.message}</p>}
    {calendar.data?.update_year && <p role="status"><a href={calendar.data.source_url}>請更新 {calendar.data.update_year} 年官方日曆</a></p>}
    <WeekendEvents events={events} onChange={refresh} />
    {notice && <p className="form-success" role="status">{notice}</p>}
    {editing || creating ? <MealScheduleWizard key={editing?.id || "new"} initial={editing} meals={meals}
      schedules={schedules.data || []} onDone={onSaved} onCancel={closeWizard} onConflict={() => void schedules.refetch()} />
      : <div className="form-actions"><button className="button button-primary" type="button" disabled={!schedules.isSuccess}
        onClick={() => { setNotice(""); setCreating(true); }}>建立排程</button></div>}
    {schedules.isPending && <LoadingLines count={2} />}
    {schedules.isError && <DataState kind="error" title="每日排程無法讀取" detail={schedules.error.message} onAction={() => schedules.refetch()} />}
    {schedules.data?.length === 0 && <DataState title="尚未設定每日排程" detail="先設定午餐或晚餐的餐點、份數與時間，再決定是否啟用。沒有設定就不會產生場次。" />}
    <div className="admin-record-grid">{schedules.data?.map((schedule) => <ScheduleCard key={schedule.id} schedule={schedule} onEdit={() => { setNotice(""); setCreating(false); setEditing(schedule); }} onDone={refresh} onDisabled={() => onDisabled(schedule.id)} onDeleted={() => onDeleted(schedule.id)} />)}</div>
  </>;
}

function ScheduleCard({ schedule, onEdit, onDone, onDisabled, onDeleted }: {
  schedule: MealSchedule; onEdit: () => void; onDone: () => void; onDisabled: () => void; onDeleted: () => void;
}) {
  const editButton = useRef<HTMLButtonElement>(null);
  const focusEditOnClose = useRef(false);
  const [open, setOpen] = useState(false);
  const [scope, setScope] = useState<DisableScope>("generation_only");
  const [reviewChanged, setReviewChanged] = useState(false);
  const preview = useQuery({
    queryKey: ["admin-meal-schedule-disable-preview", schedule.id],
    queryFn: ({ signal }) => apiFetch<ScheduleDisablePreview>(`/v1/admin/meal-schedules/${schedule.id}/disable-preview`, { signal }),
    enabled: open,
    staleTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  const disable = useMutation({
    mutationFn: (request: { scope: DisableScope; preview_version: string }) =>
      apiFetch<{ paused_event_count: number }>(`/v1/admin/meal-schedules/${schedule.id}/disable`, {
        method: "POST", body: JSON.stringify(request),
      }),
    retry: false,
    onSuccess: () => {
      focusEditOnClose.current = true;
      setOpen(false);
      onDisabled();
    },
    onError: async (error) => {
      if (!(error instanceof ApiError && error.status === 409)) return;
      setScope("generation_only");
      setReviewChanged(true);
      await preview.refetch();
      onDone();
    },
  });
  const previewReady = preview.isSuccess && !preview.isFetching;
  const pausable = preview.data?.events.filter(event => event.ordering_status === "open" || event.ordering_status === "scheduled").length ?? 0;
  const scopeAllowed = scope === "generation_only" || pausable > 0;
  const disableConflict = disable.error instanceof ApiError && disable.error.status === 409;
  const generate = useMutation({
    mutationFn: () => apiFetch(`/v1/admin/meal-schedules/${schedule.id}/generate`, { method: "POST" }),
    onSuccess: onDone,
  });
  return <article className="admin-record-card">
    <span className="status-chip">{schedule.enabled ? "排程啟用" : "排程停用"} · {mealPeriodLabels[schedule.meal_period]}</span>
    <h3>{schedule.title}</h3><p>{schedule.location}</p>
    <dl>
      <div><dt>供餐</dt><dd>{schedule.pickup_start_time.slice(0, 5)}–{schedule.pickup_end_time < schedule.pickup_start_time ? "次日 " : ""}{schedule.pickup_end_time.slice(0, 5)}</dd></div>
      <div><dt>供餐日</dt><dd>{scheduleDayLabel(schedule)}</dd></div>
      <div><dt>新場次</dt><dd>{schedule.auto_publish ? "自動發布" : "僅草稿，需另行發布"}</dd></div>
    </dl>
    <div className="form-actions">
      <button ref={editButton} className="button button-quiet" type="button" onClick={onEdit}>編輯排程</button>
      <button className="button button-quiet" type="button" disabled={!schedule.enabled || generate.isPending || disable.isPending}
        onClick={() => generate.mutate()}>{generate.isPending ? "補齊中…" : "立即補齊未來場次"}</button>
      <Dialog.Root open={open} onOpenChange={next => {
        if (disable.isPending) return;
        if (next) {
          focusEditOnClose.current = false;
          setScope("generation_only");
          setReviewChanged(false);
          disable.reset();
        }
        setOpen(next);
      }}>
        {schedule.enabled && <Dialog.Trigger asChild><button className="button button-quiet" type="button" disabled={generate.isPending}>停用排程</button></Dialog.Trigger>}
        <Dialog.Portal>
          <Dialog.Overlay className="admin-cancel-overlay" />
          <Dialog.Content className="admin-cancel-dialog" onCloseAutoFocus={event => {
            if (!focusEditOnClose.current && schedule.enabled) return;
            event.preventDefault();
            editButton.current?.focus();
          }}>
            <Dialog.Title>停用排程</Dialog.Title>
            <Dialog.Description>{schedule.title} · 既有訂單保留原付款與取餐安排。</Dialog.Description>
            {preview.isPending || preview.isFetching ? <p role="status">正在讀取停用影響…</p>
              : preview.isError ? <>
                <p role="alert" className="form-error">{preview.error instanceof ApiError && preview.error.status === 404
                  ? "停用預覽暫時無法使用。" : preview.error.message}</p>
                <button className="button button-quiet" type="button" onClick={() => void preview.refetch()}>重新讀取預覽</button>
              </> : preview.data && <>
                {reviewChanged && <p role="status">場次資料已更新，請重新核對停用範圍。</p>}
                {preview.data.events.length > 0 ? <ul aria-label="已建立場次">
                  {preview.data.events.map(event => <li key={event.id}>
                    <time dateTime={event.service_date}>{event.service_date.replaceAll("-", "/")}</time> · {orderingLabels[event.ordering_status]} · {event.order_count} 筆訂單
                  </li>)}
                </ul> : <p>目前沒有已建立的未來場次。</p>}
                {pausable > 0 ? <label className="field">
                  <span>停用範圍</span>
                  <select value={scope} disabled={disable.isPending} onChange={event => setScope(event.target.value === "pause_generated" ? "pause_generated" : "generation_only")}>
                    <option value="generation_only">只停止自動建立</option>
                    <option value="pause_generated">同時暫停 {pausable} 場的新預訂</option>
                  </select>
                </label> : <p>只停止自動建立新場次。</p>}
              </>}
            {disable.isError && !disableConflict && <p className="form-error" role="alert">{disable.error.message}</p>}
            <div className="form-actions">
              <Dialog.Close asChild><button className="button button-quiet" type="button" disabled={disable.isPending}>返回</button></Dialog.Close>
              <button className="button button-primary" type="button" disabled={!previewReady || !scopeAllowed || disable.isPending}
                onClick={() => {
                  if (!previewReady || !preview.data || !scopeAllowed) return;
                  disable.mutate({ scope, preview_version: preview.data.preview_version });
                }}>{disable.isPending ? "停用中…" : "確認停用排程"}</button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
    {disable.isSuccess && <p className="form-success" role="status">{disable.data.paused_event_count
      ? `已停用排程並暫停 ${disable.data.paused_event_count} 場的新預訂。` : "已停用排程。"}</p>}
    {generate.isSuccess && <p className="form-success">已完成排程補齊，既有日期不會重複建立。</p>}
    {generate.isError && <p className="form-error">{generate.error.message}</p>}
    <DeleteRecordAction label="刪除排程" detail="建錯或不需要的排程可以直接刪除，它產生、還沒有訂單的場次會一起刪除；已有訂單的排程請改用停用。"
      remove={() => apiFetch(`/v1/admin/meal-schedules/${schedule.id}`, { method: "DELETE" })} onDeleted={onDeleted}
      disabled={generate.isPending || disable.isPending} />
  </article>;
}

function WeekendEvents({ events, onChange }: { events: MealEvent[]; onChange: () => void }) {
  const now = Date.now();
  const weekends = events.filter(event => event.business_only && event.schedule_template_id && !event.archived_at
    && event.service_date && [0, 6].includes(new Date(`${event.service_date}T00:00:00Z`).getUTCDay())
    && Date.parse(event.pickup_ends_at) > now)
    .sort((a, b) => Date.parse(a.pickup_starts_at) - Date.parse(b.pickup_starts_at));
  const change = useMutation({
    mutationFn: (event: MealEvent) => apiFetch(`/v1/admin/meal-events/${event.id}/ordering-pause`, {
      method: "PATCH", body: JSON.stringify({ paused: !event.ordering_paused, expected_version: event.ordering_pause_version ?? 0 }),
    }),
    onSuccess: onChange,
    onError: onChange,
  });
  if (!weekends.length) return null;
  return <section aria-label="接下來的週末會餐">
    <h2>接下來的週末會餐</h2>
    <ul>{weekends.map(event => <li key={event.id}>
      <strong>{event.title}</strong> · {formatMealDateTime(event.pickup_starts_at)}
      {event.status === "draft" && " · 草稿"}
      {event.status === "cancelled" && " · 已取消"}
      {mealCanChangeOrderingPause(event, now) && <button className="button button-quiet" type="button" disabled={change.isPending}
        onClick={() => change.mutate(event)}>{event.ordering_paused ? "恢復這場預訂" : "停用這場預訂"}</button>}
    </li>)}</ul>
    {change.isError && <p className="form-error" role="alert">{change.error.message}</p>}
  </section>;
}
