import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { apiFetch, formatMoney } from "../lib/api";
import type { Meal, MealSchedule } from "../lib/types";
import { DataState, LoadingLines } from "./Shared";

type ScheduleDraft = Omit<MealSchedule, "id" | "timezone" | "created_at">;
const weekdays = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"];
const periodDefaults = {
  lunch: { pickup_start_time: "11:00", pickup_end_time: "14:00", cutoff_time: "13:00" },
  dinner: { pickup_start_time: "17:00", pickup_end_time: "20:00", cutoff_time: "19:00" },
};

export function MealScheduleSettings({ meals, onEventsChanged }: { meals: Meal[]; onEventsChanged: () => void }) {
  const queryClient = useQueryClient();
  const schedules = useQuery({
    queryKey: ["admin-meal-schedules"],
    queryFn: () => apiFetch<MealSchedule[]>("/v1/admin/meal-schedules"),
  });
  const [editing, setEditing] = useState<MealSchedule | null>(null);
  const [formKey, setFormKey] = useState(0);
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["admin-meal-schedules"] });
    queryClient.invalidateQueries({ queryKey: ["meal-events"] });
    onEventsChanged();
  };
  const onSaved = () => { setEditing(null); setFormKey((key) => key + 1); refresh(); };
  return <>
    <p className="field-help">午餐、晚餐可各建一份週一至週六循環排程。保存時預設停用且不自動開售；啟用後每日補齊未來場次。仍可在「場次與核銷」手動新增，既有場次的價格與已成立訂單不會被改寫。</p>
    <ScheduleForm key={`${editing?.id || "new"}-${formKey}`} initial={editing} meals={meals} onDone={onSaved} onCancel={() => setEditing(null)} />
    {schedules.isPending && <LoadingLines count={2} />}
    {schedules.isError && <DataState kind="error" title="每日排程無法讀取" detail={schedules.error.message} onAction={() => schedules.refetch()} />}
    {schedules.data?.length === 0 && <DataState title="尚未設定每日排程" detail="先設定午餐或晚餐的餐點、份數與時間，再決定是否啟用。沒有設定就不會產生場次。" />}
    <div className="admin-record-grid">{schedules.data?.map((schedule) => <ScheduleCard key={schedule.id} schedule={schedule} onEdit={() => setEditing(schedule)} onDone={refresh} />)}</div>
  </>;
}

function ScheduleCard({ schedule, onEdit, onDone }: { schedule: MealSchedule; onEdit: () => void; onDone: () => void }) {
  const generate = useMutation({
    mutationFn: () => apiFetch(`/v1/admin/meal-schedules/${schedule.id}/generate`, { method: "POST" }),
    onSuccess: onDone,
  });
  return <article className="admin-record-card">
    <span className="status-chip">{schedule.enabled ? "排程啟用" : "排程停用"} · {schedule.meal_period === "lunch" ? "午餐" : "晚餐"}</span>
    <h3>{schedule.title}</h3><p>{schedule.location}</p>
    <dl>
      <div><dt>供餐</dt><dd>{schedule.pickup_start_time.slice(0, 5)}–{schedule.pickup_end_time < schedule.pickup_start_time ? "次日 " : ""}{schedule.pickup_end_time.slice(0, 5)}</dd></div>
      <div><dt>截單</dt><dd>{schedule.cutoff_days_before ? `取餐前 ${schedule.cutoff_days_before} 天` : "取餐當天"} {schedule.cutoff_time.slice(0, 5)}</dd></div>
      <div><dt>預開</dt><dd>未來 {schedule.advance_days} 天</dd></div>
      <div><dt>星期</dt><dd>{schedule.weekdays.map((day) => weekdays[day]).join("、")}</dd></div>
      <div><dt>新場次</dt><dd>{schedule.auto_publish ? "自動發布" : "僅草稿，需另行發布"}</dd></div>
    </dl>
    <div className="form-actions"><button className="button button-quiet" type="button" onClick={onEdit}>編輯排程</button><button className="button button-quiet" type="button" disabled={!schedule.enabled || generate.isPending} onClick={() => generate.mutate()}>{generate.isPending ? "補齊中…" : "立即補齊未來場次"}</button></div>
    {generate.isSuccess && <p className="form-success">已完成排程補齊，既有日期不會重複建立。</p>}
    {generate.isError && <p className="form-error">{generate.error.message}</p>}
  </article>;
}

function ScheduleForm({ initial, meals, onDone, onCancel }: { initial: MealSchedule | null; meals: Meal[]; onDone: () => void; onCancel: () => void }) {
  const [draft, setDraft] = useState<ScheduleDraft>(() => initial ? {
    title: initial.title, location: initial.location, meal_period: initial.meal_period,
    pickup_start_time: initial.pickup_start_time.slice(0, 5), pickup_end_time: initial.pickup_end_time.slice(0, 5), cutoff_time: initial.cutoff_time.slice(0, 5),
    cutoff_days_before: initial.cutoff_days_before, advance_days: initial.advance_days,
    weekdays: initial.weekdays, enabled: initial.enabled, auto_publish: initial.auto_publish,
    offerings: initial.offerings,
  } : {
    title: "", location: "", meal_period: "lunch", ...periodDefaults.lunch,
    cutoff_days_before: 0, advance_days: 7, weekdays: [0, 1, 2, 3, 4, 5], enabled: false, auto_publish: false, offerings: [],
  });
  const selected = new Set(draft.offerings.map((offering) => offering.meal_id));
  const minutes = (value: string) => { const [hours, minute] = value.split(":").map(Number); return hours * 60 + minute; };
  const pickupEndMinutes = minutes(draft.pickup_end_time) + (draft.pickup_end_time < draft.pickup_start_time ? 1440 : 0);
  const cutoffMinutes = minutes(draft.cutoff_time) - draft.cutoff_days_before * 1440;
  const invalidTime = draft.pickup_start_time === draft.pickup_end_time
    || cutoffMinutes >= pickupEndMinutes;
  const invalidAdvance = draft.advance_days <= draft.cutoff_days_before;
  const changePeriod = (mealPeriod: "lunch" | "dinner") => setDraft((current) => {
    const previous = periodDefaults[current.meal_period];
    const untouched = !initial && current.pickup_start_time === previous.pickup_start_time
      && current.pickup_end_time === previous.pickup_end_time && current.cutoff_time === previous.cutoff_time;
    return { ...current, meal_period: mealPeriod, ...(untouched ? periodDefaults[mealPeriod] : {}) };
  });
  const save = useMutation({
    mutationFn: () => apiFetch<MealSchedule>(initial ? `/v1/admin/meal-schedules/${initial.id}` : "/v1/admin/meal-schedules", {
      method: initial ? "PUT" : "POST", body: JSON.stringify(draft),
    }),
    onSuccess: onDone,
  });
  return <form className="social-form admin-master-form" onSubmit={(event) => { event.preventDefault(); if (!invalidTime && !invalidAdvance) save.mutate(); }}>
    <div className="form-heading"><div><p className="eyebrow">DAILY MEAL SCHEDULE</p><h2>{initial ? "編輯每日排程" : "建立每日午晚餐排程"}</h2></div><span>全部採台灣時間（Asia/Taipei）。</span></div>
    <div className="field-grid two-columns">
      <label className="field"><span>排程／場次名稱</span><input required maxLength={120} value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} /></label>
      <label className="field"><span>取餐地點</span><input required maxLength={160} value={draft.location} onChange={(event) => setDraft({ ...draft, location: event.target.value })} /></label>
      <label className="field"><span>排程餐別</span><select aria-label="排程餐別" value={draft.meal_period} onChange={(event) => changePeriod(event.target.value as "lunch" | "dinner")}><option value="lunch">午餐</option><option value="dinner">晚餐</option></select></label>
      <label className="field"><span>提前開放天數</span><input required type="number" min={1} max={30} value={draft.advance_days} onChange={(event) => setDraft({ ...draft, advance_days: Number(event.target.value) })} /></label>
      <label className="field"><span>每日取餐開始</span><input required type="time" value={draft.pickup_start_time} onChange={(event) => setDraft({ ...draft, pickup_start_time: event.target.value })} /></label>
      <label className="field"><span>每日取餐結束</span><input required type="time" value={draft.pickup_end_time} onChange={(event) => setDraft({ ...draft, pickup_end_time: event.target.value })} /></label>
      <label className="field"><span>取餐前幾天截單（0 為當天）</span><input required type="number" min={0} max={29} value={draft.cutoff_days_before} onChange={(event) => setDraft({ ...draft, cutoff_days_before: Number(event.target.value) })} /></label>
      <label className="field"><span>每日截單時間</span><input required type="time" value={draft.cutoff_time} onChange={(event) => setDraft({ ...draft, cutoff_time: event.target.value })} /></label>
    </div>
    <button className="button button-quiet" type="button" onClick={() => setDraft({ ...draft, ...periodDefaults[draft.meal_period], cutoff_days_before: 0 })}>套用{draft.meal_period === "lunch" ? "午餐" : "晚餐"}預設時段</button>
    <p className="field-help">午餐 11:00–14:00 取餐、13:00 截單；晚餐 17:00–20:00 取餐、19:00 截單。可一邊取餐一邊接單，截單必須早於取餐結束；已自訂或既有排程的時間不會因切換餐別而覆寫。</p>
    <p className="field-help">結束時間早於開始時間，表示跨至隔日；例如 23:30–00:30。截單日期依「取餐前幾天」計算。排程修改只影響尚未產生的場次，停用不會取消已建立場次。</p>
    {invalidTime && <p className="form-error">開始與結束時間不可相同，截單時間必須早於取餐結束。</p>}
    {invalidAdvance && <p className="form-error">提前開放天數必須大於提前截單天數。</p>}
    <fieldset><legend>每週供餐日</legend><div className="meal-choice-grid">{weekdays.map((label, day) => <label className="meal-choice" key={day}><input type="checkbox" checked={draft.weekdays.includes(day)} onChange={() => setDraft({ ...draft, weekdays: draft.weekdays.includes(day) ? draft.weekdays.filter((value) => value !== day) : [...draft.weekdays, day].sort() })} /><span>{label}</span></label>)}</div></fieldset>
    <fieldset><legend>每日餐點與供應份數</legend><div className="meal-choice-grid">{meals.filter((meal) => meal.is_active || selected.has(meal.id)).map((meal) => <label className="meal-choice" key={meal.id}><input type="checkbox" checked={selected.has(meal.id)} onChange={() => setDraft({ ...draft, offerings: selected.has(meal.id) ? draft.offerings.filter((item) => item.meal_id !== meal.id) : [...draft.offerings, { meal_id: meal.id, price: meal.price, capacity: 20, position: draft.offerings.length }] })} /><span><strong>{meal.name}{!meal.is_active && "（餐點已停用）"}</strong><small>{formatMoney(meal.price)}</small></span></label>)}</div></fieldset>
    {draft.offerings.map((offering) => <div className="meal-offering-editor" key={offering.meal_id}>
      <strong>{meals.find((meal) => meal.id === offering.meal_id)?.name || "餐點暫無法讀取"}</strong>
      <label className="field"><span>每日售價</span><input required type="number" min={0} value={offering.price} onChange={(event) => setDraft({ ...draft, offerings: draft.offerings.map((item) => item.meal_id === offering.meal_id ? { ...item, price: Number(event.target.value) } : item) })} /></label>
      <label className="field"><span>每日份數</span><input required type="number" min={1} value={offering.capacity} onChange={(event) => setDraft({ ...draft, offerings: draft.offerings.map((item) => item.meal_id === offering.meal_id ? { ...item, capacity: Number(event.target.value) } : item) })} /></label>
    </div>)}
    <label className="meal-choice"><input type="checkbox" checked={draft.enabled} onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })} /><span>啟用每日自動產生場次</span></label>
    <label className="meal-choice"><input type="checkbox" checked={draft.auto_publish} onChange={(event) => setDraft({ ...draft, auto_publish: event.target.checked })} /><span>新產生場次自動發布並開放預訂（未勾選僅建立草稿）</span></label>
    <p className="field-help">{draft.enabled ? draft.auto_publish ? "儲存後會依本設定建立並發布未來場次，使用者可正式訂購。" : "儲存後會建立未來草稿場次；需至「場次與核銷」逐場發布才可訂購。" : "儲存後只保留設定，不會產生或發布場次。"}</p>
    {save.isError && <p className="form-error">{save.error.message}</p>}
    <div className="form-actions">{initial && <button className="button button-quiet" type="button" onClick={onCancel}>取消編輯</button>}<button className="button button-primary" disabled={!draft.title.trim() || !draft.location.trim() || !draft.weekdays.length || !draft.offerings.length || invalidTime || invalidAdvance || save.isPending}>{save.isPending ? "儲存中…" : "儲存每日排程"}</button></div>
  </form>;
}
