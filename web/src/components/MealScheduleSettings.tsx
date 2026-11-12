import * as Dialog from "@radix-ui/react-dialog";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { ApiError, apiFetch, formatMoney } from "../lib/api";
import { formatMealDateTime, mealCanChangeOrderingPause } from "../lib/meal-time";
import type { Meal, MealEvent, MealSchedule } from "../lib/types";
import { DataState, LoadingLines } from "./Shared";
import "./OrderCancellation.css";

type ScheduleDraft = Omit<MealSchedule, "id" | "timezone" | "created_at">;
type CalendarStatus = { available: boolean; from_date: string; to_date: string; message: string; source_url: string; update_year?: number | null };
type DisableScope = "generation_only" | "pause_generated";
type ScheduleDisablePreview = {
  preview_version: string;
  events: Array<{ id: string; title: string; service_date: string; order_count: number; ordering_status: keyof typeof orderingLabels }>;
};
const orderingLabels = { draft: "草稿", paused: "已暫停預訂", closed: "已截止", open: "開放預訂", scheduled: "尚未開放" };
const weekdays = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"];
function scheduleCutoff(schedule: Pick<MealSchedule, "cutoff_days_before" | "cutoff_time">) {
  return schedule.cutoff_days_before < 1
    ? { cutoff_days_before: 1, cutoff_time: "20:00" }
    : { cutoff_days_before: schedule.cutoff_days_before, cutoff_time: schedule.cutoff_days_before === 1 && schedule.cutoff_time > "20:00" ? "20:00" : schedule.cutoff_time.slice(0, 5) };
}
const periodDefaults = {
  lunch: { pickup_start_time: "11:00", pickup_end_time: "13:00", cutoff_time: "20:00" },
  dinner: { pickup_start_time: "16:30", pickup_end_time: "18:00", cutoff_time: "20:00" },
};

export function MealScheduleSettings({ meals, events, onEventsChanged }: { meals: Meal[]; events: MealEvent[]; onEventsChanged: () => void }) {
  const queryClient = useQueryClient();
  const schedules = useQuery({
    queryKey: ["admin-meal-schedules"],
    queryFn: () => apiFetch<MealSchedule[]>("/v1/admin/meal-schedules"),
  });
  const calendar = useQuery({ queryKey: ["admin-meal-calendar-status"], queryFn: () => apiFetch<CalendarStatus>("/v1/admin/meal-schedules/calendar-status") });
  const [editing, setEditing] = useState<MealSchedule | null>(null);
  const [formKey, setFormKey] = useState(0);
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["admin-meal-schedules"] });
    queryClient.invalidateQueries({ queryKey: ["meal-events"] });
    onEventsChanged();
  };
  const onSaved = () => { setEditing(null); setFormKey((key) => key + 1); refresh(); };
  const onDisabled = (scheduleId: string) => {
    setEditing(current => current?.id === scheduleId ? null : current);
    queryClient.setQueryData<MealSchedule[]>(["admin-meal-schedules"], current => current?.map(schedule =>
      schedule.id === scheduleId ? { ...schedule, enabled: false } : schedule));
    refresh();
  };
  return <>
    {calendar.data && <div className="meal-calendar-status"><span className={`status-chip ${calendar.data.available ? "passed" : ""}`}>{calendar.data.available ? "工作日日曆已就緒" : "工作日日曆無法使用"}</span><span>{calendar.data.from_date} 至 {calendar.data.to_date}</span></div>}
    {calendar.data && !calendar.data.available && <p className="form-error" role="alert">{calendar.data.message}</p>}
    {calendar.isError && <p className="form-error" role="alert">{calendar.error.message}</p>}
    {calendar.data?.update_year && <p role="status"><a href={calendar.data.source_url}>請更新 {calendar.data.update_year} 年官方日曆</a></p>}
    <SaturdayEvents events={events} onChange={refresh} />
    <ScheduleForm key={`${editing?.id || "new"}-${formKey}`} initial={editing} meals={meals} schedules={schedules.data || []} onDone={onSaved} onCancel={() => setEditing(null)} />
    {schedules.isPending && <LoadingLines count={2} />}
    {schedules.isError && <DataState kind="error" title="每日排程無法讀取" detail={schedules.error.message} onAction={() => schedules.refetch()} />}
    {schedules.data?.length === 0 && <DataState title="尚未設定每日排程" detail="先設定午餐或晚餐的餐點、份數與時間，再決定是否啟用。沒有設定就不會產生場次。" />}
    <div className="admin-record-grid">{schedules.data?.map((schedule) => <ScheduleCard key={schedule.id} schedule={schedule} onEdit={() => setEditing(schedule)} onDone={refresh} onDisabled={() => onDisabled(schedule.id)} />)}</div>
  </>;
}

function ScheduleCard({ schedule, onEdit, onDone, onDisabled }: {
  schedule: MealSchedule; onEdit: () => void; onDone: () => void; onDisabled: () => void;
}) {
  const cutoff = scheduleCutoff(schedule);
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
    <span className="status-chip">{schedule.enabled ? "排程啟用" : "排程停用"} · {schedule.meal_period === "lunch" ? "午餐" : "晚餐"}</span>
    <h3>{schedule.title}</h3><p>{schedule.location}</p>
    <dl>
      <div><dt>供餐</dt><dd>{schedule.pickup_start_time.slice(0, 5)}–{schedule.pickup_end_time < schedule.pickup_start_time ? "次日 " : ""}{schedule.pickup_end_time.slice(0, 5)}</dd></div>
      <div><dt>截單</dt><dd>{`取餐前 ${cutoff.cutoff_days_before} 天`} {cutoff.cutoff_time}</dd></div>
      <div><dt>預先建立</dt><dd>未來 {schedule.advance_days} 天</dd></div>
      <div><dt>星期</dt><dd>{[...schedule.weekdays.filter(day => day < 5).map(day => weekdays[day]), ...(schedule.saturday_business_only ? ["週六・只接受會餐"] : [])].join("、")}</dd></div>
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
  </article>;
}

function SaturdayEvents({ events, onChange }: { events: MealEvent[]; onChange: () => void }) {
  const now = Date.now();
  const saturdays = events.filter(event => event.business_only && event.schedule_template_id
    && event.service_date && new Date(`${event.service_date}T00:00:00Z`).getUTCDay() === 6
    && Date.parse(event.pickup_ends_at) > now)
    .sort((a, b) => Date.parse(a.pickup_starts_at) - Date.parse(b.pickup_starts_at));
  const change = useMutation({
    mutationFn: (event: MealEvent) => apiFetch(`/v1/admin/meal-events/${event.id}/ordering-pause`, {
      method: "PATCH", body: JSON.stringify({ paused: !event.ordering_paused, expected_version: event.ordering_pause_version ?? 0 }),
    }),
    onSuccess: onChange,
    onError: onChange,
  });
  if (!saturdays.length) return null;
  return <section aria-label="接下來的週六會餐">
    <h2>接下來的週六會餐</h2>
    <ul>{saturdays.map(event => <li key={event.id}>
      <strong>{event.title}</strong> · {formatMealDateTime(event.pickup_starts_at)}
      {event.status === "draft" && " · 草稿"}
      {event.status === "cancelled" && " · 已取消"}
      {mealCanChangeOrderingPause(event, now) && <button className="button button-quiet" type="button" disabled={change.isPending}
        onClick={() => change.mutate(event)}>{event.ordering_paused ? "恢復這週預訂" : "停用這週預訂"}</button>}
    </li>)}</ul>
    {change.isError && <p className="form-error" role="alert">{change.error.message}</p>}
  </section>;
}

function ScheduleForm({ initial, meals, schedules, onDone, onCancel }: { initial: MealSchedule | null; meals: Meal[]; schedules: MealSchedule[]; onDone: () => void; onCancel: () => void }) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<ScheduleDraft>(() => initial ? {
    title: initial.title, location: initial.location, meal_period: initial.meal_period,
    pickup_start_time: initial.pickup_start_time.slice(0, 5), pickup_end_time: initial.pickup_end_time.slice(0, 5), ...scheduleCutoff(initial),
    advance_days: initial.advance_days,
    weekdays: initial.weekdays.filter(day => day < 5), saturday_business_only: initial.saturday_business_only ?? false, enabled: initial.enabled, auto_publish: initial.auto_publish,
    offerings: initial.offerings,
  } : {
    title: "", location: "", meal_period: "lunch", ...periodDefaults.lunch,
    cutoff_days_before: 1, advance_days: 7, weekdays: [0, 1, 2, 3, 4], saturday_business_only: false, enabled: false, auto_publish: false, offerings: [],
  });
  const selected = new Set(draft.offerings.map((offering) => offering.meal_id));
  const minutes = (value: string) => { const [hours, minute] = value.split(":").map(Number); return hours * 60 + minute; };
  const pickupEndMinutes = minutes(draft.pickup_end_time) + (draft.pickup_end_time < draft.pickup_start_time ? 1440 : 0);
  const cutoffMinutes = minutes(draft.cutoff_time) - draft.cutoff_days_before * 1440;
  const invalidTime = draft.pickup_start_time === draft.pickup_end_time
    || cutoffMinutes >= pickupEndMinutes || draft.cutoff_days_before < 1
    || (draft.cutoff_days_before === 1 && draft.cutoff_time > "20:00");
  const invalidAdvance = draft.advance_days <= draft.cutoff_days_before;
  const counterpart = schedules.find((schedule) => schedule.id !== initial?.id
    && schedule.meal_period !== draft.meal_period
    && schedule.location.trim().toLocaleLowerCase() === draft.location.trim().toLocaleLowerCase()
    && schedule.weekdays.some((day) => draft.weekdays.includes(day)));
  const syncCounterpartMenu = () => {
    if (!counterpart) return;
    const currentByMeal = new Map(draft.offerings.map((offering) => [offering.meal_id, offering]));
    setDraft({ ...draft, offerings: counterpart.offerings.map((offering, position) => ({
      ...offering,
      capacity: currentByMeal.get(offering.meal_id)?.capacity ?? offering.capacity,
      position,
    })) });
  };
  const changePeriod = (mealPeriod: "lunch" | "dinner") => setDraft((current) => {
    const previous = periodDefaults[current.meal_period];
    const untouched = !initial && current.pickup_start_time === previous.pickup_start_time
      && current.pickup_end_time === previous.pickup_end_time && current.cutoff_time === previous.cutoff_time;
    return { ...current, meal_period: mealPeriod, ...(untouched ? periodDefaults[mealPeriod] : {}) };
  });
  const save = useMutation({
    mutationFn: () => apiFetch<MealSchedule>(initial ? `/v1/admin/meal-schedules/${initial.id}` : "/v1/admin/meal-schedules", {
      method: initial ? "PUT" : "POST",
      body: JSON.stringify({ ...draft, ...(initial?.updated_at ? { expected_updated_at: initial.updated_at } : {}) }),
    }),
    onSuccess: onDone,
    onError: error => {
      if (error instanceof ApiError && error.status === 409) {
        void queryClient.invalidateQueries({ queryKey: ["admin-meal-schedules"] });
      }
    },
  });
  return <form className="social-form admin-master-form" onSubmit={(event) => { event.preventDefault(); if (!invalidTime && !invalidAdvance) save.mutate(); }}>
    <div className="form-heading"><div><p className="eyebrow">DAILY MEAL SCHEDULE / 每日供餐排程</p><h2>{initial ? "編輯每日排程" : "建立每日午晚餐排程"}</h2></div><span>全部採台灣時間（Asia/Taipei）。</span></div>
    <div className="field-grid two-columns">
      <label className="field"><span>排程／場次名稱</span><input required maxLength={120} value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} /></label>
      <label className="field"><span>取餐地點</span><input required maxLength={160} value={draft.location} onChange={(event) => setDraft({ ...draft, location: event.target.value })} /></label>
      <label className="field"><span>排程餐別</span><select aria-label="排程餐別" value={draft.meal_period} onChange={(event) => changePeriod(event.target.value as "lunch" | "dinner")}><option value="lunch">午餐</option><option value="dinner">晚餐</option></select></label>
      <label className="field"><span>預先建立未來幾天場次</span><input required type="number" min={1} max={30} value={draft.advance_days} onChange={(event) => setDraft({ ...draft, advance_days: Number(event.target.value) })} /></label>
      <label className="field"><span>每日取餐開始</span><input required type="time" value={draft.pickup_start_time} onChange={(event) => setDraft({ ...draft, pickup_start_time: event.target.value })} /></label>
      <label className="field"><span>每日取餐結束</span><input required type="time" value={draft.pickup_end_time} onChange={(event) => setDraft({ ...draft, pickup_end_time: event.target.value })} /></label>
      <label className="field"><span>取餐前幾天截單</span><input required type="number" min={1} max={29} value={draft.cutoff_days_before} onChange={(event) => setDraft({ ...draft, cutoff_days_before: Number(event.target.value) })} /></label>
      <label className="field"><span>每日截單時間</span><input required type="time" value={draft.cutoff_time} onChange={(event) => setDraft({ ...draft, cutoff_time: event.target.value })} /></label>
    </div>
    <button className="button button-quiet" type="button" onClick={() => setDraft({ ...draft, ...periodDefaults[draft.meal_period], cutoff_days_before: 1 })}>套用{draft.meal_period === "lunch" ? "午餐" : "晚餐"}預設時段</button>
    {invalidTime && <p className="form-error">開始與結束時間不可相同；預訂最晚於取餐前一天 20:00 截止。</p>}
    {invalidAdvance && <p className="form-error">預先建立天數必須大於提前截單天數。</p>}
    <fieldset><legend>每週供餐日</legend><div className="meal-choice-grid">{weekdays.map((label, day) => day < 5 && <label className="meal-choice" key={day}><input type="checkbox" checked={draft.weekdays.includes(day)} onChange={() => setDraft({ ...draft, weekdays: draft.weekdays.includes(day) ? draft.weekdays.filter((value) => value !== day) : [...draft.weekdays, day].sort() })} /><span>{label}</span></label>)}<label className="meal-choice"><input type="checkbox" checked={draft.saturday_business_only} onChange={event => setDraft({ ...draft, saturday_business_only: event.target.checked })} /><span>週六・只接受會餐</span></label></div></fieldset>
    <fieldset><legend>每日餐點與供應份數</legend>{counterpart && <button className="button button-quiet" type="button" onClick={syncCounterpartMenu}>同步另一餐別菜單</button>}<div className="meal-choice-grid">{meals.filter((meal) => meal.is_active || selected.has(meal.id)).map((meal) => <label className="meal-choice" key={meal.id}><input type="checkbox" checked={selected.has(meal.id)} onChange={() => setDraft({ ...draft, offerings: selected.has(meal.id) ? draft.offerings.filter((item) => item.meal_id !== meal.id) : [...draft.offerings, { meal_id: meal.id, price: meal.price, capacity: 20, position: draft.offerings.length }] })} /><span><strong>{meal.name}{!meal.is_active && "（餐點已停用）"}</strong><small>{formatMoney(meal.price)}</small></span></label>)}</div></fieldset>
    {draft.offerings.map((offering) => <div className="meal-offering-editor" key={offering.meal_id}>
      <strong>{meals.find((meal) => meal.id === offering.meal_id)?.name || "餐點暫無法讀取"}</strong>
      <label className="field"><span>每日售價</span><input required type="number" min={0} value={offering.price} onChange={(event) => setDraft({ ...draft, offerings: draft.offerings.map((item) => item.meal_id === offering.meal_id ? { ...item, price: Number(event.target.value) } : item) })} /></label>
      <label className="field"><span>每日份數</span><input required type="number" min={1} value={offering.capacity} onChange={(event) => setDraft({ ...draft, offerings: draft.offerings.map((item) => item.meal_id === offering.meal_id ? { ...item, capacity: Number(event.target.value) } : item) })} /></label>
    </div>)}
    <label className="meal-choice"><input type="checkbox" disabled={initial?.enabled === true} checked={draft.enabled} onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })} /><span>啟用每日自動產生場次</span></label>
    <label className="meal-choice"><input type="checkbox" checked={draft.auto_publish} onChange={(event) => setDraft({ ...draft, auto_publish: event.target.checked })} /><span>新產生場次自動發布並開放預訂（未勾選僅建立草稿）</span></label>
    {save.isError && <p className="form-error">{save.error.message}</p>}
    <div className="form-actions">{initial && <button className="button button-quiet" type="button" onClick={onCancel}>取消編輯</button>}<button className="button button-primary" disabled={!draft.title.trim() || !draft.location.trim() || (!draft.weekdays.length && !draft.saturday_business_only) || !draft.offerings.length || invalidTime || invalidAdvance || save.isPending}>{save.isPending ? "儲存中…" : "儲存每日排程"}</button></div>
  </form>;
}
