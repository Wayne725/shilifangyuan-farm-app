import { useMutation, useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";

import { ApiError, apiFetch, formatMoney } from "../lib/api";
import type { Meal, MealSchedule } from "../lib/types";
import { PickupLocationPicker, UNLIMITED_MEAL_CAPACITY, pickupLocationSummary, usePickupLocations } from "./PickupLocationPicker";
import "./MealScheduleWizard.css";

type Period = MealSchedule["meal_period"];
type Scope = "empty_future" | "new_only";
type MenuItem = { meal_id: string; capacity: number };
type ServingDays = Pick<MealSchedule, "weekdays" | "saturday_business_only" | "sunday_business_only">;
type ScheduleInput = ServingDays & {
  id?: string;
  expected_updated_at?: string;
  title: string;
  location: string;
  meal_period: Period;
  pickup_start_time: string;
  pickup_end_time: string;
  enabled: boolean;
  auto_publish: boolean;
  offerings: MenuItem[];
  pickup_location_ids: string[];
};
type EventPreview = { id: string; title: string; service_date: string; meal_period: Period };
type SchedulePreview = {
  preview_version: string;
  updated_events: EventPreview[];
  protected_events: EventPreview[];
  canonical_offerings: Array<MenuItem & { price: number }>;
};
type PeriodDraft = {
  title: string; location: string; start: string; end: string; enabled: boolean; autoPublish: boolean;
};

const steps = ["餐別與供餐日", "選餐點", "取餐時間與地區", "統整確認"] as const;
const reviewStep = steps.length - 1;
const periods: Period[] = ["lunch", "dinner"];
const weekdays = [0, 1, 2, 3, 4];
const weekdayNames = ["週一", "週二", "週三", "週四", "週五"];
export const mealPeriodLabels = { lunch: "午餐", dinner: "晚餐" };
const weekdayRule = "平日：前一個工作日 00:00 開放，取餐前一天 20:00 截止";
const weekendRule = "週六、週日：週五 00:00–20:00";
type WeekendMode = "off" | "business" | "all";
const weekendModeLabels: Record<WeekendMode, string> = { off: "不供餐", business: "只接受會餐", all: "一般便當與會餐" };
const weekendDays = [{ day: 5, name: "週六" }, { day: 6, name: "週日" }] as const;

function weekendMode(days: ServingDays, day: 5 | 6): WeekendMode {
  if (days.weekdays.includes(day)) return "all";
  return days[day === 5 ? "saturday_business_only" : "sunday_business_only"] ? "business" : "off";
}

function withWeekendMode(days: ServingDays, day: 5 | 6, mode: WeekendMode): ServingDays {
  const others = days.weekdays.filter(item => item !== day);
  return {
    ...days,
    weekdays: mode === "all" ? [...others, day].sort((a, b) => a - b) : others,
    [day === 5 ? "saturday_business_only" : "sunday_business_only"]: mode === "business",
  };
}

function servesDays(days: ServingDays) {
  return days.weekdays.length > 0 || days.saturday_business_only || days.sunday_business_only;
}

export function scheduleDayLabel(days: ServingDays) {
  const weekdaysLabel = days.weekdays.filter(day => day < 5);
  return [
    weekdaysLabel.length === weekdays.length ? "平日" : weekdaysLabel.map(day => weekdayNames[day]).join("、"),
    weekendMode(days, 5) === "business" && "週六・只接受會餐",
    weekendMode(days, 5) === "all" && "週六・一般便當與會餐",
    weekendMode(days, 6) === "business" && "週日・會議餐",
    weekendMode(days, 6) === "all" && "週日・一般便當與會餐",
  ].filter(Boolean).join("、");
}

function periodDraft(period: Period, schedule?: MealSchedule): PeriodDraft {
  return {
    title: schedule?.title ?? "",
    location: schedule?.location ?? "清華大學",
    start: schedule?.pickup_start_time.slice(0, 5) ?? (period === "lunch" ? "11:00" : "16:30"),
    end: schedule?.pickup_end_time.slice(0, 5) ?? (period === "lunch" ? "13:00" : "18:00"),
    enabled: schedule?.enabled ?? false,
    autoPublish: schedule?.auto_publish ?? false,
  };
}

function defaultTitle(location: string, period: Period) {
  const suffix = `${mealPeriodLabels[period]}預訂`;
  return location.trim().slice(0, 140 - suffix.length) + suffix;
}

function settingsLabel(input: ScheduleInput) {
  return `${input.location} · ${input.pickup_start_time}–${input.pickup_end_time < input.pickup_start_time ? "次日 " : ""}${input.pickup_end_time}`;
}

export function MealScheduleWizard({ initial, meals, schedules, onDone, onCancel, onConflict }: {
  initial: MealSchedule | null;
  meals: Meal[];
  schedules: MealSchedule[];
  onDone: (scheduleCount: number, updatedCount: number) => void;
  onCancel: () => void;
  onConflict: () => void;
}) {
  const [step, setStep] = useState(initial ? reviewStep : 0);
  const [furthestStep, setFurthestStep] = useState(initial ? reviewStep : 0);
  const [reviewReturn, setReviewReturn] = useState(false);
  const stepHeading = useRef<HTMLHeadingElement>(null);
  const [periodChoices, setPeriodChoices] = useState<Period[]>([initial?.meal_period ?? "lunch"]);
  const [days, setDays] = useState<ServingDays>({
    weekdays: initial?.weekdays ?? weekdays,
    saturday_business_only: initial?.saturday_business_only ?? false,
    sunday_business_only: initial?.sunday_business_only ?? false,
  });
  const selectedWeekdays = days.weekdays.filter(day => day < 5);
  const [periodSettings, setPeriodSettings] = useState<Record<Period, PeriodDraft>>({
    lunch: periodDraft("lunch", initial?.meal_period === "lunch" ? initial : undefined),
    dinner: periodDraft("dinner", initial?.meal_period === "dinner" ? initial : undefined),
  });
  const [counterpartId, setCounterpartId] = useState("");
  const [offerings, setOfferings] = useState<MenuItem[]>(initial?.offerings.map(({ meal_id }) => ({ meal_id, capacity: UNLIMITED_MEAL_CAPACITY })) ?? []);
  const [pickupLocationIds, setPickupLocationIds] = useState<string[]>(initial?.pickup_location_ids ?? []);
  const locations = usePickupLocations(pickupLocationIds.length > 0 || Boolean(initial?.pickup_location_ids?.length));
  const [scope, setScope] = useState<Scope>("empty_future");
  const [reviewChanged, setReviewChanged] = useState(false);
  const byMeal = new Map(meals.map(meal => [meal.id, meal]));
  const otherPeriod: Period = initial?.meal_period === "lunch" ? "dinner" : "lunch";
  const counterparts = initial ? schedules.filter(schedule => schedule.meal_period === otherPeriod
    && schedule.location.trim() === initial.location.trim()) : [];
  const counterpart = counterparts.find(schedule => schedule.id === counterpartId);
  const selectedBoth = periodChoices.length === 2;
  const missingCounterpart = Boolean(initial && selectedBoth && counterparts.length && !counterpart);
  const inputs: ScheduleInput[] = periods.filter(period => periodChoices.includes(period)).map(period => {
    const current = initial?.meal_period === period ? initial : counterpart;
    const settings = periodSettings[period];
    return {
      ...days,
      ...(current ? { id: current.id, expected_updated_at: current.updated_at } : {}),
      title: settings.title.trim() || defaultTitle(settings.location, period),
      location: settings.location.trim(), meal_period: period, pickup_start_time: settings.start, pickup_end_time: settings.end,
      enabled: settings.enabled, auto_publish: settings.autoPublish, offerings, pickup_location_ids: pickupLocationIds,
    };
  });
  const problems = [
    !periodChoices.length ? "請選擇餐別。" : !servesDays(days) ? "請選擇供餐日。" : missingCounterpart ? "請選擇要一起更新的排程。" : "",
    offerings.length === 0 ? "請至少選擇一道餐點。" : offerings.length > 100 ? "每個排程最多選擇 100 道餐點。" : offerings.some(item => !byMeal.get(item.meal_id)?.is_active) ? "請取消勾選已停售或無法讀取的餐點。" : "",
    inputs.some(item => !item.location || !item.pickup_start_time || !item.pickup_end_time || item.pickup_start_time === item.pickup_end_time) ? "請填寫供餐地區，並設定不同的取餐開始與結束時間。" : "",
  ];
  const firstProblem = problems.findIndex(Boolean);
  const preview = useQuery({
    queryKey: ["meal-schedule-wizard-preview", inputs, scope],
    queryFn: ({ signal }) => apiFetch<SchedulePreview>("/v1/admin/meal-schedules/wizard/preview", {
      method: "POST", signal, body: JSON.stringify({ schedules: inputs, scope }),
    }),
    enabled: step === reviewStep && firstProblem === -1,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  const save = useMutation({
    mutationFn: (version: string) => apiFetch<{ schedules: MealSchedule[]; updated_event_count: number }>("/v1/admin/meal-schedules/wizard", {
      method: "POST", body: JSON.stringify({ schedules: inputs, scope, preview_version: version }),
    }),
    onSuccess: result => onDone(result.schedules.length, result.updated_event_count),
    onError: async error => {
      if (!(error instanceof ApiError && error.status === 409)) return;
      onConflict();
      const refreshed = await preview.refetch();
      setReviewChanged(refreshed.isSuccess);
    },
  });
  useEffect(() => { stepHeading.current?.focus(); }, [step]);

  function goTo(next: number, returnToReview = false) {
    save.reset();
    setReviewChanged(false);
    setStep(next);
    setFurthestStep(value => Math.max(value, next));
    setReviewReturn(returnToReview);
  }
  function editPeriod(period: Period, fields: Partial<PeriodDraft>) {
    setPeriodSettings(current => ({ ...current, [period]: { ...current[period], ...fields } }));
  }
  function toggleOffering(id: string) {
    setOfferings(items => items.some(item => item.meal_id === id) ? items.filter(item => item.meal_id !== id) : [...items, { meal_id: id, capacity: UNLIMITED_MEAL_CAPACITY }]);
  }
  function selectCounterpart(id: string) {
    setCounterpartId(id);
    const schedule = counterparts.find(item => item.id === id);
    setPeriodSettings(current => ({ ...current, [otherPeriod]: periodDraft(otherPeriod, schedule) }));
  }

  const prices = new Map(preview.data?.canonical_offerings.map(item => [item.meal_id, item.price]));
  const displayPrice = (id: string) => prices.get(id) ?? byMeal.get(id)?.price;
  const menuOptions = meals.filter(meal => meal.is_active || offerings.some(item => item.meal_id === meal.id));
  const showImpact = initial && inputs.some(item => item.id);
  return <form className="social-form admin-master-form meal-schedule-wizard" onSubmit={event => {
    event.preventDefault();
    if (step < reviewStep && !problems[step]) goTo(reviewReturn ? reviewStep : step + 1);
    if (step === reviewStep && firstProblem === -1 && preview.isSuccess && !preview.isFetching && !save.isPending) {
      save.mutate(preview.data.preview_version);
    }
  }}>
    <div className="form-heading"><h2>{initial ? "編輯每日排程" : "建立每日排程"}</h2></div>
    {initial && <nav className="meal-schedule-steps" aria-label="排程步驟">
      {steps.map((label, index) => <button key={label} type="button" className="button button-quiet" aria-current={step === index ? "step" : undefined}
        disabled={save.isPending || index > furthestStep} onClick={() => goTo(index)}>{label}</button>)}
    </nav>}
    <h3 ref={stepHeading} tabIndex={-1}>{step + 1}／{steps.length} {steps[step]}</h3>
    <fieldset className="meal-schedule-step-fields" disabled={save.isPending}>
      {step === 0 && <>
        <fieldset><legend>餐別</legend><div className="meal-choice-grid">
          {periods.map(period => <label key={period} className="meal-choice"><input type="checkbox" checked={periodChoices.includes(period)}
            disabled={initial?.meal_period === period} onChange={event => setPeriodChoices(current => event.target.checked ? [...current, period] : current.filter(item => item !== period))} />{mealPeriodLabels[period]}</label>)}
        </div></fieldset>
        {initial && selectedBoth && counterparts.length > 0 && <label className="field"><span>一併更新的{mealPeriodLabels[otherPeriod]}排程</span>
          <select value={counterpart?.id ?? ""} onChange={event => selectCounterpart(event.target.value)}>
            <option value="">請選擇排程</option>
            {counterparts.map(schedule => <option key={schedule.id} value={schedule.id}>{schedule.title}</option>)}
          </select></label>}
        <fieldset><legend>供餐日</legend><div className="meal-choice-grid">
          <label className="meal-choice"><input type="checkbox" checked={selectedWeekdays.length > 0} onChange={event => setDays({ ...days, weekdays: [...(event.target.checked ? weekdays : []), ...days.weekdays.filter(day => day >= 5)] })} />平日（週一到週五）</label>
          {weekendDays.map(({ day, name }) => <label key={day} className="field"><span>{name}</span>
            <select aria-label={name} value={weekendMode(days, day)} onChange={event => setDays(withWeekendMode(days, day, event.target.value as WeekendMode))}>
              {(Object.keys(weekendModeLabels) as WeekendMode[]).map(mode => <option key={mode} value={mode}>{weekendModeLabels[mode]}</option>)}
            </select></label>)}
        </div>{selectedWeekdays.length > 0 && selectedWeekdays.length < 5 && <p>目前平日供餐日為 {selectedWeekdays.map(day => weekdayNames[day]).join("、")}。</p>}</fieldset>
      </>}
      {step === 1 && <>
        <div className="meal-choice-grid">{menuOptions.map(meal => <label key={meal.id} className="meal-choice">
          <input type="checkbox" checked={offerings.some(item => item.meal_id === meal.id)} onChange={() => toggleOffering(meal.id)} />
          <span><strong>{meal.name}{!meal.is_active && "（已停售）"}</strong><small>{formatMoney(meal.price)}</small></span>
        </label>)}</div>
        {offerings.filter(item => !byMeal.has(item.meal_id)).map(item => <button key={item.meal_id} className="button button-quiet" type="button" onClick={() => toggleOffering(item.meal_id)}>移除無法讀取的餐點</button>)}
        <Link to="/admin/meals" search={{ tab: "meals" }} target="_blank" rel="noopener" className="text-link">找不到餐點？去新增餐點</Link>
      </>}
      {step === 2 && <>
        {periods.filter(period => periodChoices.includes(period)).map(period => {
          const current = periodSettings[period];
          return <fieldset key={period}><legend>{mealPeriodLabels[period]}</legend><div className="field-grid two-columns">
            <label className="field"><span>取餐開始</span><input type="time" value={current.start} onChange={event => editPeriod(period, { start: event.target.value })} /></label>
            <label className="field"><span>取餐結束</span><input type="time" value={current.end} onChange={event => editPeriod(period, { end: event.target.value })} /></label>
            <label className="field"><span>供餐地區</span><input maxLength={240} value={current.location} onChange={event => editPeriod(period, { location: event.target.value })} /></label>
            <label className="field"><span>排程名稱</span><input maxLength={140} placeholder={defaultTitle(current.location, period)} value={current.title} onChange={event => editPeriod(period, { title: event.target.value })} /></label>
          </div></fieldset>;
        })}
        <PickupLocationPicker legend="開放的取餐點" value={pickupLocationIds} onChange={setPickupLocationIds} />
        <p>要新增或修改取餐點，到「<Link to="/admin/catalog" search={{ tab: "locations" }} target="_blank" rel="noopener" className="text-link">商品 → 取餐地點</Link>」管理。</p>
      </>}
      {step === reviewStep && <>
        <div className="meal-schedule-review" aria-label="排程統整確認">
          <dl>
            <div><dt>餐別與供餐日</dt><dd>{inputs.map(item => mealPeriodLabels[item.meal_period]).join("、")} · {scheduleDayLabel(days)}</dd><EditStep label="餐別與供餐日" onClick={() => goTo(0, true)} /></div>
            <div><dt>餐點（預訂不限份數）</dt><dd><ul>{offerings.map(item => {
              const price = displayPrice(item.meal_id);
              return <li key={item.meal_id}>{byMeal.get(item.meal_id)?.name || "餐點"} · {price == null ? "—" : formatMoney(price)}</li>;
            })}</ul></dd>
              <span className="meal-schedule-edit-links"><EditStep label="餐點" text="修改餐點" onClick={() => goTo(1, true)} /></span></div>
            <div><dt>取餐時間與地區</dt><dd><ul>{inputs.map(item => <li key={item.meal_period}>{item.title} · {settingsLabel(item)}</li>)}</ul></dd><EditStep label="取餐時間與地區" onClick={() => goTo(2, true)} /></div>
            <div><dt>開放的取餐點</dt><dd>{pickupLocationSummary(pickupLocationIds, locations.data)}</dd><EditStep label="開放的取餐點" onClick={() => goTo(2, true)} /></div>
            <div><dt>預訂規則</dt><dd>{selectedWeekdays.length > 0 && <div>{weekdayRule}</div>}{weekendDays.some(({ day }) => weekendMode(days, day) !== "off") && <div>{weekendRule}</div>}</dd></div>
          </dl>
        </div>
        {inputs.map(input => {
          const previous = input.id === initial?.id ? initial : counterpart;
          const period = input.meal_period;
          return <fieldset key={period}><legend>{mealPeriodLabels[period]}排程</legend>
            <label className="meal-choice"><input type="checkbox" disabled={previous?.enabled === true} checked={input.enabled} onChange={event => editPeriod(period, { enabled: event.target.checked })} />啟用自動排程</label>
            <label className="meal-choice"><input type="checkbox" checked={input.auto_publish} onChange={event => editPeriod(period, { autoPublish: event.target.checked })} />新場次自動發布</label>
            {initial && <ScheduleChanges input={input} previous={previous} mealLabel={id => byMeal.get(id)?.name ?? "已移除的餐點"} mealPrice={displayPrice}
              locationLabel={ids => pickupLocationSummary(ids, locations.data)} />}
            {previous && weekendDays.some(({ day }) => weekendMode(previous, day) !== "off" && weekendMode(input, day) === "off") &&
              <p>已建立的週末場次可在「接下來的週末會餐」逐場停用。</p>}
            {previous && weekendDays.some(({ day }) => weekendMode(previous, day) !== weekendMode(input, day) && weekendMode(input, day) !== "off") &&
              <p>已建立的週末場次會一起改成新的接單方式；改成只接受會餐時，已有訂單的場次維持原樣。</p>}
          </fieldset>;
        })}
        {showImpact && <fieldset><legend>套用範圍</legend>
          <label className="meal-choice"><input type="radio" name="meal-schedule-scope" checked={scope === "empty_future"} onChange={() => setScope("empty_future")} />還沒有人訂的未來場次一起換成新菜單</label>
          <label className="meal-choice"><input type="radio" name="meal-schedule-scope" checked={scope === "new_only"} onChange={() => setScope("new_only")} />只影響之後新建的場次</label>
        </fieldset>}
        {firstProblem !== -1 && <p className="form-error" role="alert">{problems[firstProblem]} <EditStep label={steps[firstProblem]} onClick={() => goTo(firstProblem, true)} /></p>}
        {firstProblem === -1 && preview.isFetching && <p role="status">核對餐點與場次…</p>}
        {preview.isSuccess && !preview.isFetching && <ScheduleImpact preview={preview.data} />}
        {preview.isError && <p className="form-error" role="alert"><span>{preview.error.message}</span> <button type="button" className="text-link" onClick={() => void preview.refetch()}>重新核對</button></p>}
        {reviewChanged && <p role="status">預覽已重新核對，請確認後再儲存。</p>}
        {save.isError && save.error.message !== preview.error?.message && <p className="form-error" role="alert">{save.error.message}</p>}
      </>}
    </fieldset>
    {step < reviewStep && problems[step] && <p className="form-error">{problems[step]}</p>}
    <div className="form-actions">
      <button type="button" className="button button-quiet" disabled={save.isPending} onClick={onCancel}>取消</button>
      {step > 0 && <button type="button" className="button button-quiet" disabled={save.isPending} onClick={() => goTo(step - 1)}>上一步</button>}
      <button key={step} type="submit" className="button button-primary" disabled={step < reviewStep ? Boolean(problems[step]) : firstProblem !== -1 || !preview.isSuccess || preview.isFetching || save.isPending}>
        {step < reviewStep ? reviewReturn ? "返回確認" : "下一步" : save.isPending ? "儲存中…" : initial ? "確認修改" : "確認建立"}
      </button>
    </div>
  </form>;
}

function EditStep({ label, onClick, text = "修改" }: { label: string; onClick: () => void; text?: string }) {
  return <button type="button" className="text-link" aria-label={`修改${label}`} onClick={onClick}>{text}</button>;
}

function ScheduleImpact({ preview }: { preview: SchedulePreview }) {
  return <>
    {preview.updated_events.length > 0 && <p>將同步 {preview.updated_events.length} 個未有訂單的未來場次。</p>}
    {preview.protected_events.length > 0 && <div>
      <p>以下 {preview.protected_events.length} 個場次已有訂單，維持原菜單。</p>
      <ul>{preview.protected_events.map(event => <li key={event.id}>{event.service_date} · {event.title}</li>)}</ul>
    </div>}
  </>;
}

function ScheduleChanges({ input, previous, mealLabel, mealPrice, locationLabel }: {
  input: ScheduleInput; previous?: MealSchedule | null; mealLabel: (id: string) => string; mealPrice: (id: string) => number | undefined;
  locationLabel: (ids: string[]) => string;
}) {
  if (!previous) return <p>新增{mealPeriodLabels[input.meal_period]}排程</p>;
  const changes: string[] = [];
  const newDays = scheduleDayLabel(input);
  const oldDays = scheduleDayLabel(previous);
  if (newDays !== oldDays) changes.push(`供餐日：${oldDays || "—"} → ${newDays}`);
  const oldPickup = settingsLabel({ ...input, location: previous.location, pickup_start_time: previous.pickup_start_time.slice(0, 5), pickup_end_time: previous.pickup_end_time.slice(0, 5) });
  if (oldPickup !== settingsLabel(input)) changes.push(`${oldPickup} → ${settingsLabel(input)}`);
  if (input.title !== previous.title) changes.push(`${previous.title} → ${input.title}`);
  const oldMenu = new Map(previous.offerings.map(item => [item.meal_id, item]));
  const menu = new Set(input.offerings.map(item => item.meal_id));
  for (const item of input.offerings) {
    const current = oldMenu.get(item.meal_id);
    const price = mealPrice(item.meal_id);
    const name = mealLabel(item.meal_id);
    if (!current) changes.push(`新增：${name}`);
    else {
      if (current.capacity < item.capacity) changes.push(`${name} 改為不限份數`);
      if (price != null && current.price !== price) changes.push(`${name} ${formatMoney(current.price)} → ${formatMoney(price)}`);
    }
  }
  for (const item of previous.offerings) {
    if (!menu.has(item.meal_id)) changes.push(`移除：${mealLabel(item.meal_id)}`);
  }
  const oldLocations = locationLabel(previous.pickup_location_ids ?? []);
  const newLocations = locationLabel(input.pickup_location_ids);
  if (oldLocations !== newLocations) changes.push(`開放取餐點：${oldLocations} → ${newLocations}`);
  if (input.enabled !== previous.enabled) changes.push("啟用自動排程");
  if (input.auto_publish !== previous.auto_publish) changes.push(input.auto_publish ? "新場次改為自動發布" : "新場次改為草稿");
  return changes.length > 0 ? <ul aria-label={`${mealPeriodLabels[input.meal_period]}變更`}>{changes.map(change => <li key={change}>{change}</li>)}</ul> : null;
}
