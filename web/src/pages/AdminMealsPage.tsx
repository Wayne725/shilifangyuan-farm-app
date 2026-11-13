import { MealDisasterCancellation } from "../components/MealDisasterCancellation";
import { OrderQuantityAmendment } from "../components/OrderQuantityAmendment";
import * as Dialog from "@radix-ui/react-dialog";
import * as Tabs from "@radix-ui/react-tabs";
import { BowlFood, Buildings, CalendarPlus, Copy, SealCheck } from "@phosphor-icons/react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect, useId, useMemo, useRef, useState } from "react";

import { AdminCancelOrder } from "../components/AdminCancelOrder";
import { AdminCashPayment } from "../components/AdminCashPayment";
import { AdminNav } from "../components/AdminNav";
import { AdminMealCancellations } from "../components/AdminMealCancellations";
import { AdminMealOrderSearch } from "../components/AdminMealOrderSearch";
import { MealScheduleSettings } from "../components/MealScheduleSettings";
import { MealEventConflicts } from "../components/MealEventConflicts";
import { MealOptionsEditor } from "../components/MealOptionsEditor";
import { MealDetailsEditor } from "../components/MealDetailsEditor";
import { MealImageEditor } from "../components/MealImageEditor";
import { DataState, LoadingLines } from "../components/Shared";
import { InvoiceTaxSummary } from "../components/InvoiceTaxSummary";
import { useAuth } from "../context/AuthContext";
import { ApiError, apiFetch, formatMoney } from "../lib/api";
import { mealPaymentMethodLabel } from "../lib/business-meals";
import { mealEventStatusLabel } from "../lib/labels";
import { formatMealDateTime, mealCanChangeOrderingPause, mealServiceDate, shiftMealDate, taipeiDateTimeInput, taipeiInputToIso } from "../lib/meal-time";
import type { Meal, MealEvent, MealOrder } from "../lib/types";
import { OptionalMark } from "../components/OptionalMark";

interface EventDraft {
  title: string;
  booking_policy: "legacy" | "workday" | "special";
  same_day_preparation_minutes?: string | null;
  business_only?: boolean;
  allow_cash_payment?: boolean;
  location: string;
  ordering_starts_at: string;
  ordering_ends_at: string;
  pickup_starts_at: string;
  pickup_ends_at: string;
  offerings: Array<{ meal_id: string; price: number; capacity: number; position: number }>;
}

export function AdminMealsPage() {
  const { user, openLogin } = useAuth();
  const isAdmin = user?.user_role === "admin";
  const queryClient = useQueryClient();
  const routeSearch = useSearch({ from: "/admin/meals" });
  const navigate = useNavigate({ from: "/admin/meals" });
  const activeTab = routeSearch.tab || "orders";
  const [editing, setEditing] = useState<MealEvent | null>(null);
  const [rebuildMessage, setRebuildMessage] = useState("");
  const editorPanel = useRef<HTMLElement>(null);
  const [meals, events] = useQueries({
    queries: [
      { queryKey: ["admin-meals"], queryFn: () => apiFetch<Meal[]>("/v1/admin/meals"), enabled: isAdmin },
      { queryKey: ["admin-meal-events"], queryFn: () => apiFetch<MealEvent[]>("/v1/admin/meal-events"), enabled: isAdmin, refetchInterval: 30000 },
    ],
  });
  const replacement = routeSearch.rebuild
    ? events.data?.find(event => event.id === routeSearch.rebuild && event.status === "cancelled")
    : undefined;
  const editorEvent = routeSearch.rebuild ? replacement : editing;
  const editorEventId = editorEvent?.id;
  useEffect(() => {
    if (activeTab === "events" && editorEventId) editorPanel.current?.focus();
  }, [activeTab, editorEventId, routeSearch.rebuild]);

  if (!isAdmin) {
    return <section className="admin-gate"><div className="admin-gate-mark"><Buildings size={38} weight="light" /></div><p className="eyebrow">MEAL OPERATIONS / 便當管理</p><h1>餐點與場次管理只向管理者開放。</h1>{!user ? <button className="button button-primary" type="button" onClick={openLogin}>管理者登入</button> : <Link className="button button-quiet" to="/shop">返回生活消費</Link>}</section>;
  }

  const refreshMeals = () => {
    void queryClient.invalidateQueries({ queryKey: ["admin-meals"] });
    void queryClient.invalidateQueries({ queryKey: ["admin-meal-events"] });
    void queryClient.invalidateQueries({ queryKey: ["meal-events"] });
    void queryClient.invalidateQueries({ queryKey: ["meal-event"] });
  };
  const refreshEvents = () => {
    queryClient.invalidateQueries({ queryKey: ["admin-meal-events"] });
    queryClient.invalidateQueries({ queryKey: ["admin-meal-event-orders"] });
    queryClient.invalidateQueries({ queryKey: ["meal-events"] });
    queryClient.invalidateQueries({ queryKey: ["meal-event"] });
    queryClient.invalidateQueries({ queryKey: ["admin-meal-orders"] });
    queryClient.invalidateQueries({ queryKey: ["admin-meal-operations-summary"] });
    queryClient.invalidateQueries({ queryKey: ["admin-meal-cancellations"] });
    queryClient.invalidateQueries({ queryKey: ["meal-announcements"] });
  };
  const closeEventEditor = (date = routeSearch.date) => {
    setEditing(null);
    if (routeSearch.rebuild) void navigate({ search: { tab: "events", date }, replace: true, resetScroll: false });
  };

  return (
    <section className="admin-module-page admin-meals-workspace">
      <header className="workspace-heading"><div><p className="eyebrow">MEAL OPERATIONS / 便當營運管理</p><h1>訂單查詢、退單與取餐</h1></div></header>
      <AdminNav />
      <Tabs.Root className="admin-social-tabs" value={activeTab} onValueChange={(tab) => {
        setEditing(null);
        setRebuildMessage("");
        void navigate({ search: { tab } });
      }}>
        <Tabs.List className="tab-list" aria-label="便當營運分類"><Tabs.Trigger value="orders">訂單查詢與待辦</Tabs.Trigger><Tabs.Trigger value="cancellations">退單審核</Tabs.Trigger><Tabs.Trigger value="events">場次</Tabs.Trigger><Tabs.Trigger value="schedules">每日午晚餐排程</Tabs.Trigger><Tabs.Trigger value="disaster">颱風／天災停餐</Tabs.Trigger><Tabs.Trigger value="meals">餐點主檔</Tabs.Trigger></Tabs.List>
        <Tabs.Content className="tab-content" value="orders"><AdminMealOrderSearch key={JSON.stringify([routeSearch.q, routeSearch.queue])} initialSearch={routeSearch.q} initialQueue={routeSearch.queue} /></Tabs.Content>
        <Tabs.Content className="tab-content" value="cancellations">
          <AdminMealCancellations key={JSON.stringify([routeSearch.order_id, routeSearch.q])} initialSearch={routeSearch.q} initialOrderId={routeSearch.order_id} />
        </Tabs.Content>
        <Tabs.Content className="tab-content" value="events">
          {editorEvent ? <section ref={editorPanel} tabIndex={-1} aria-label={replacement ? "建立補開草稿" : "編輯便當場次"} className="meal-event-create">
            <EventCreateForm key={`${replacement ? "rebuild" : "edit"}-${editorEvent.id}`}
              meals={meals.data || []} events={events.data || []} initial={replacement ? undefined : editing ?? undefined}
              replacement={replacement} onCancel={() => closeEventEditor()} onDone={(event) => {
                refreshEvents();
                if (replacement) setRebuildMessage(`已建立「${event.title}」草稿，確認後可發布。`);
                closeEventEditor(replacement ? mealServiceDate(event) : routeSearch.date);
              }} />
          </section> : <>
            {routeSearch.rebuild && events.data && <p className="form-error" role="alert">找不到可補開的取消場次。</p>}
            <details className="meal-event-create">
              <summary>建立便當場次</summary>
              <EventCreateForm meals={meals.data || []} events={events.data || []} onDone={refreshEvents} />
            </details>
          </>}
          {rebuildMessage && !editorEvent && <p className="form-success" role="status">{rebuildMessage}</p>}
          {events.isPending && <LoadingLines count={4} />}
          {events.isError && <DataState kind="error" title="便當場次無法讀取" detail={events.error.message} />}
          {events.data && <MealEventList key={routeSearch.date || "all"} initialDate={routeSearch.date} events={events.data} onDone={refreshEvents} onEdit={event => {
            if (routeSearch.rebuild) closeEventEditor();
            setRebuildMessage("");
            setEditing(event);
          }} />}
        </Tabs.Content>
        <Tabs.Content className="tab-content" value="disaster"><MealDisasterCancellation mealEvents={events.data} mealEventsError={events.error?.message} onDone={refreshEvents} /></Tabs.Content>
        <Tabs.Content className="tab-content" value="schedules"><MealScheduleSettings meals={meals.data || []} events={events.data || []} onEventsChanged={refreshEvents} /></Tabs.Content>
        <Tabs.Content className="tab-content" value="meals">
          <MealCreateForm onDone={refreshMeals} />
          {meals.isPending && <LoadingLines count={3} />}
          {meals.isError && <DataState kind="error" title="餐點資料無法讀取" detail={meals.error.message} />}
          <div className="admin-record-grid">{meals.data?.map((meal) => <article className="admin-record-card" key={meal.id}><BowlFood size={22} weight="light" /><span className={`status-chip ${meal.is_active ? "passed" : ""}`}>{meal.is_active ? "啟用" : "停用"}</span><h3>{meal.name}</h3><p>{meal.description || "尚未填寫餐點介紹"}</p><dl><div><dt>售價</dt><dd>{formatMoney(meal.price)}</dd></div><div><dt>選項</dt><dd>{meal.option_groups.length} 組</dd></div><div><dt>稅別</dt><dd>{meal.tax_type === "taxable" ? "應稅" : "免稅"}</dd></div></dl><MealDetailsEditor meal={meal} onDone={refreshMeals} /><MealOptionsEditor meal={meal} onDone={refreshMeals} /><MealImageEditor meal={meal} onDone={refreshMeals} /></article>)}</div>
        </Tabs.Content>
      </Tabs.Root>
    </section>
  );
}

const eventStatuses = ["draft", "published", "ordering_closed", "pickup_open", "completed", "cancelled"];
const eventListPageSize = 10;
const emptyEventSearch = { date: "", status: "", query: "" };

function adminMealEventLabel(status: string) {
  return status === "pickup_open" ? "供餐中" : mealEventStatusLabel(status);
}

function mealEventIsFinal(event: MealEvent) {
  return event.status === "cancelled" || event.status === "completed";
}

function MealEventList({ initialDate, events, onDone, onEdit }: {
  initialDate?: string; events: MealEvent[]; onDone: () => void; onEdit: (event: MealEvent) => void;
}) {
  const [draft, setDraft] = useState({ ...emptyEventSearch, date: initialDate ?? "" });
  const [search, setSearch] = useState(draft);
  const [page, setPage] = useState(1);
  const [archiveMessage, setArchiveMessage] = useState("");
  const queryClient = useQueryClient();
  const archive = useMutation({
    mutationFn: (event: MealEvent) => apiFetch<MealEvent>(`/v1/admin/meal-events/${event.id}/archive`, {
      method: "PATCH",
      body: JSON.stringify({ archived: !event.archived_at }),
    }),
    onMutate: () => setArchiveMessage(""),
    onSuccess: async (event) => {
      await queryClient.cancelQueries({ queryKey: ["admin-meal-events"] });
      queryClient.setQueryData<MealEvent[]>(["admin-meal-events"], (entries) => entries?.map((entry) => entry.id === event.id ? event : entry));
      setArchiveMessage(event.archived_at ? `已封存「${event.title}」` : `已取消封存「${event.title}」`);
      onDone();
    },
    onError: () => onDone(),
  });
  const now = Date.now();
  const keyword = search.query.trim().toLowerCase();
  const matches = events.filter((event) => {
    if (Boolean(event.archived_at) !== (search.status === "archived")) return false;
    if (search.status === "paused" && (!event.ordering_paused || mealEventIsFinal(event))) return false;
    if (eventStatuses.includes(search.status) && search.status !== event.status) return false;
    if (search.date && mealServiceDate(event) !== search.date) return false;
    return !keyword || [event.title, event.location].some((value) => value.toLowerCase().includes(keyword));
  }).sort((left, right) => {
    const leftPast = Date.parse(left.pickup_ends_at) <= now;
    const rightPast = Date.parse(right.pickup_ends_at) <= now;
    if (leftPast !== rightPast) return leftPast ? 1 : -1;
    const byPickup = Date.parse(left.pickup_starts_at) - Date.parse(right.pickup_starts_at);
    return leftPast ? -byPickup : byPickup;
  });
  const lastPage = Math.max(1, Math.ceil(matches.length / eventListPageSize));
  const currentPage = Math.min(page, lastPage);
  const offset = (currentPage - 1) * eventListPageSize;

  return <>
    <form className="meal-event-search" aria-label="查詢場次" onSubmit={(event) => {
      event.preventDefault();
      setSearch(draft);
      setPage(1);
    }}>
      <div className="field-grid three-columns">
        <label className="field"><span>供餐日期</span><input type="date" value={draft.date} onChange={(event) => setDraft({ ...draft, date: event.target.value })} /></label>
        <label className="field"><span>場次狀態</span><select value={draft.status} onChange={(event) => setDraft({ ...draft, status: event.target.value })}>
          <option value="">全部未封存</option>
          {eventStatuses.map((status) => <option key={status} value={status}>{adminMealEventLabel(status)}</option>)}
          <option value="paused">新預訂已暫停</option>
          <option value="archived">已封存</option>
        </select></label>
        <label className="field"><span>場次名稱或地點</span><input type="search" value={draft.query} onChange={(event) => setDraft({ ...draft, query: event.target.value })} /></label>
      </div>
      <div className="form-actions">
        <button className="button button-primary" type="submit">查詢場次</button>
        <button className="button button-quiet" type="button" onClick={() => { setDraft(emptyEventSearch); setSearch(emptyEventSearch); setPage(1); }}>清除查詢</button>
      </div>
    </form>
    {archiveMessage && <p className="form-success" role="status">{archiveMessage}</p>}
    {archive.isError && <p className="form-error" role="alert">{archive.error.message}</p>}
    <div className="meal-event-results">
      <p role="status">{matches.length ? `共 ${matches.length} 個場次` : events.length ? "沒有符合條件的場次" : "目前沒有便當場次"}</p>
      {lastPage > 1 && <nav className="meal-event-pagination" aria-label="場次分頁">
        <button className="button button-quiet" type="button" disabled={currentPage === 1} onClick={() => setPage(currentPage - 1)}>上一頁</button>
        <span role="status">第 {currentPage}／{lastPage} 頁</span>
        <button className="button button-quiet" type="button" disabled={currentPage === lastPage} onClick={() => setPage(currentPage + 1)}>下一頁</button>
      </nav>}
    </div>
    {matches.length > 0 && <div className="admin-review-list">{matches.slice(offset, offset + eventListPageSize).map((event) => <MealEventCard key={event.id} event={event} onDone={onDone} onEdit={() => onEdit(event)} onArchive={() => archive.mutate(event)} archiving={archive.isPending} />)}</div>}
  </>;
}

function MealCreateForm({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [price, setPrice] = useState(120);
  const [taxType, setTaxType] = useState<"taxable" | "tax_exempt">("taxable");
  const create = useMutation({
    mutationFn: () => apiFetch<Meal>("/v1/admin/meals", { method: "POST", body: JSON.stringify({ name, description, price, tax_type: taxType, is_active: true }) }),
    onSuccess: () => { setName(""); setDescription(""); setPrice(120); onDone(); },
  });
  return <form className="social-form admin-master-form" onSubmit={(event) => { event.preventDefault(); create.mutate(); }}><div className="form-heading"><div><p className="eyebrow">MEAL RECORD / 餐點資料</p><h2>建立餐點</h2></div></div><div className="field-grid three-columns"><label className="field"><span>餐點名稱</span><input required value={name} onChange={(event) => setName(event.target.value)} /></label><label className="field"><span>單價</span><input min={0} type="number" value={price} onChange={(event) => setPrice(Number(event.target.value))} /></label><label className="field"><span>稅別</span><select value={taxType} onChange={(event) => setTaxType(event.target.value as typeof taxType)}><option value="taxable">應稅</option><option value="tax_exempt">免稅</option></select></label><label className="field wide"><span>餐點介紹</span><textarea rows={3} value={description} onChange={(event) => setDescription(event.target.value)} /></label></div>{create.isError && <p className="form-error">{create.error.message}</p>}<div className="form-actions"><button className="button button-primary" disabled={!name.trim() || create.isPending}>建立餐點</button></div></form>;
}

function EventCreateForm({ meals, events = [], onDone, initial, replacement, onCancel }: {
  meals: Meal[]; events?: MealEvent[]; onDone: (event: MealEvent) => void;
  initial?: MealEvent; replacement?: MealEvent; onCancel?: () => void;
}) {
  const [draft, setDraft] = useState<EventDraft>(() =>
    initial ? eventDraft(initial) : replacement ? replacementEventDraft(replacement) : emptyEventDraft());
  const [calendarDate, setCalendarDate] = useState<string>();
  const detailFields = useRef<HTMLDetailsElement>(null);
  const cutoffErrorId = useId();
  const heading = initial ? "編輯便當場次" : replacement ? "建立補開草稿" : "建立便當場次";
  const prefilled = Boolean(initial || replacement);
  const businessOnly = draft.business_only === true;
  const sameDay = !businessOnly && draft.same_day_preparation_minutes != null;
  const pickupDate = draft.pickup_starts_at.slice(0, 10);
  const cutoff = eventDraftCutoff(draft);
  const cutoffDateInvalid = sameDay && Boolean(cutoff) && cutoff.slice(0, 10) !== pickupDate;
  const openingTooLate = businessOnly && Boolean(cutoff) && draft.ordering_starts_at >= cutoff;
  const cutoffError = cutoffDateInvalid
    ? "當日訂餐的截止日期須與取餐開始日期相同。"
    : openingTooLate ? "會餐的開放預訂時間須早於預訂截止。" : "";
  const localNow = taipeiDateTimeInput(Date.now());
  const cutoffLimit = businessOnly || (replacement && !sameDay) ? priorDayCutoff(pickupDate) : undefined;
  const effectiveCutoff = cutoffLimit && cutoff > cutoffLimit ? cutoffLimit : cutoff;
  let recoveryError = "";
  if (replacement && draft.pickup_ends_at && draft.pickup_ends_at <= localNow) {
    recoveryError = "原取餐時段已過，請選擇新的供餐日期與時間。";
  } else if (replacement && effectiveCutoff && effectiveCutoff <= localNow) {
    recoveryError = businessOnly
      ? "會餐預訂截止已過，請選擇新的供餐日期。"
      : "預訂截止已過，請改供餐日期或調整當日訂餐設定。";
  }
  const calendar = useQuery({
    queryKey: ["admin-meal-calendar", calendarDate],
    queryFn: () => apiFetch<{ service_date: string; is_workday: boolean; previous_workday: string; next_workday: string | null; ordering_starts_at: string; ordering_ends_at: string }>(`/v1/admin/meal-calendar?${calendarDate ? `service_date=${calendarDate}` : ""}`),
    enabled: (!prefilled || Boolean(calendarDate)) && draft.booking_policy === "workday",
  });
  useEffect(() => {
    if (!calendar.data || (prefilled && !calendarDate)) return;
    const value = calendar.data;
    setDraft((current) => current.booking_policy !== "workday" ? current : {
      ...current,
      ordering_starts_at: taipeiDateTimeInput(value.ordering_starts_at),
      ...(current.same_day_preparation_minutes == null ? { ordering_ends_at: taipeiDateTimeInput(value.ordering_ends_at) } : {}),
      ...(!calendarDate ? { pickup_starts_at: `${value.service_date}T11:00`, pickup_ends_at: `${value.service_date}T13:30` } : {}),
    });
  }, [calendar.data, calendarDate, prefilled]);
  const changePickupStart = (value: string) => {
    const date = value.slice(0, 10);
    const changedDate = date !== draft.pickup_starts_at.slice(0, 10);
    setDraft((current) => ({ ...current, pickup_starts_at: value, ...(changedDate && date ? {
      pickup_ends_at: `${date}T${current.pickup_ends_at.slice(11)}`,
      ordering_ends_at: current.same_day_preparation_minutes == null
        ? priorDayCutoff(date)
        : current.ordering_ends_at ? `${date}T${current.ordering_ends_at.slice(11)}` : "",
    } : {}) }));
    if (changedDate && date) setCalendarDate(date);
  };
  const changeBookingPolicy = (policy: EventDraft["booking_policy"]) => {
    setDraft((current) => ({
      ...(policy === "legacy" && current.same_day_preparation_minutes != null ? disableSameDay(current) : current),
      booking_policy: policy,
      ...(policy === "legacy" ? { business_only: undefined } : {}),
    }));
    if (policy === "workday" && (initial || replacement)) setCalendarDate(pickupDate);
  };
  const changeSameDay = (enabled: boolean) => {
    if (enabled && detailFields.current) detailFields.current.open = true;
    setDraft((current) => enabled ? {
      ...current,
      same_day_preparation_minutes: "",
      ordering_ends_at: current.ordering_ends_at.slice(0, 10) === pickupDate ? current.ordering_ends_at : "",
    } : disableSameDay(current));
  };
  const activeMeals = meals.filter((meal) => meal.is_active || draft.offerings.some((offering) => offering.meal_id === meal.id));
  const selected = useMemo(() => new Set(draft.offerings.map((item) => item.meal_id)), [draft.offerings]);
  const create = useMutation({
    mutationFn: () => apiFetch<MealEvent>(initial ? `/v1/admin/meal-events/${initial.id}` : "/v1/admin/meal-events", {
      method: initial ? "PUT" : "POST",
      body: JSON.stringify({ ...eventPayload(draft), ...(replacement && { replaces_event_id: replacement.id }) }),
    }),
    onSuccess: (event) => {
      setDraft((current) => ({
        ...(current.same_day_preparation_minutes != null ? disableSameDay(current) : current),
        title: "",
        business_only: undefined,
        offerings: [],
      }));
      onDone(event);
    },
  });
  const calendarBlocked = draft.booking_policy === "workday"
    && (calendar.isFetching || calendar.isError || calendar.data?.is_workday === false);
  const canSave = Boolean(draft.title.trim() && draft.location.trim() && draft.offerings.length
    && draft.ordering_starts_at && cutoff && draft.pickup_starts_at && draft.pickup_ends_at
    && !cutoffError && !recoveryError && !calendarBlocked && !create.isPending);
  const conflictNotice = <MealEventConflicts events={events} location={draft.location} startsAt={draft.pickup_starts_at} endsAt={draft.pickup_ends_at} editingId={initial?.id} />;
  const toggleMeal = (meal: Meal) => setDraft((current) => ({
    ...current,
    offerings: selected.has(meal.id)
      ? current.offerings.filter((item) => item.meal_id !== meal.id)
      : [...current.offerings, { meal_id: meal.id, price: meal.price, capacity: 20, position: current.offerings.length }],
  }));
  const fields = <>
    <fieldset>
      <legend>供餐規則</legend>
      <div className="form-actions" role="group" aria-label="供餐規則">
        {(initial && (!initial.booking_policy || initial.booking_policy === "legacy") ? ["legacy", "workday", "special"] as const : ["workday", "special"] as const).map((policy) => <button key={policy} type="button" className={`button ${draft.booking_policy === policy ? "button-primary" : "button-quiet"}`} aria-pressed={draft.booking_policy === policy} onClick={() => changeBookingPolicy(policy)}>{policy === "workday" ? "工作日供餐" : policy === "special" ? "特殊日期加開" : "沿用舊場次規則"}</button>)}
      </div>
    </fieldset>
    <div className="field-grid two-columns">
      <label className="field"><span>場次名稱</span><input required value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} /></label>
      <label className="field"><span>取餐地點</span><input required value={draft.location} onChange={(event) => setDraft({ ...draft, location: event.target.value })} /></label>
      <DateTimeField label="開放預訂" value={draft.ordering_starts_at} onChange={(value) => setDraft({ ...draft, ordering_starts_at: value })} invalid={openingTooLate} errorId={cutoffErrorId} />
      <DateTimeField label="預訂截止" value={cutoff} onChange={(value) => setDraft({ ...draft, ordering_ends_at: value })} max={cutoffLimit} invalid={cutoffDateInvalid} errorId={cutoffErrorId} />
      <DateTimeField label="取餐開始" value={draft.pickup_starts_at} onChange={changePickupStart} />
      <DateTimeField label="取餐結束" value={draft.pickup_ends_at} onChange={(value) => setDraft({ ...draft, pickup_ends_at: value })} />
      {sameDay && <label className="field"><span>最短備餐時間（分鐘）</span>
        <input type="number" min={1} max={1439} step={1} required value={draft.same_day_preparation_minutes ?? ""} onChange={(event) => setDraft({ ...draft, same_day_preparation_minutes: event.target.value })} />
      </label>}
    </div>
    {cutoffError && <p id={cutoffErrorId} className="form-error" role="alert">{cutoffError}</p>}
    {draft.booking_policy === "workday" && calendar.isError && <p className="form-error" role="alert">{calendar.error.message}</p>}
    {draft.booking_policy === "workday" && calendar.data?.is_workday === false && <p className="form-error" role="alert">這一天不是工作日。{calendar.data.next_workday ? `下一工作日為 ${calendar.data.next_workday}；若需加開，請選「特殊日期加開」。` : "請選其他工作日或特殊加開。"}</p>}
    {conflictNotice}
    <div className="meal-choice-grid">{activeMeals.map((meal) => <label key={meal.id} className="meal-choice">
      <input type="checkbox" checked={selected.has(meal.id)} onChange={() => toggleMeal(meal)} />
      <span><strong>{meal.name}</strong><small>{formatMoney(meal.price)}</small></span>
    </label>)}</div>
    {draft.offerings.map((offering) => {
      const meal = meals.find((item) => item.id === offering.meal_id);
      return <div className="meal-offering-editor" key={offering.meal_id}>
        <strong>{meal?.name}</strong>
        <label className="field"><span>本場售價</span><input min={0} type="number" value={offering.price} onChange={(event) => setDraft({ ...draft, offerings: draft.offerings.map((item) => item.meal_id === offering.meal_id ? { ...item, price: Number(event.target.value) } : item) })} /></label>
        <label className="field"><span>份數</span><input min={1} type="number" value={offering.capacity} onChange={(event) => setDraft({ ...draft, offerings: draft.offerings.map((item) => item.meal_id === offering.meal_id ? { ...item, capacity: Number(event.target.value) } : item) })} /></label>
      </div>;
    })}
  </>;
  return <form className="social-form admin-master-form" aria-label={heading} onSubmit={(event) => {
    event.preventDefault();
    if (canSave) create.mutate();
  }} onInvalid={() => { if (detailFields.current) detailFields.current.open = true; }}>
    <div className="form-heading"><div><p className="eyebrow">EVENT SCHEDULE / 供餐排程</p><h2>{heading}</h2></div></div>
    {initial && <p>{initial.title}</p>}
    {replacement && <p>原場次「{replacement.title}」的訂單與退款紀錄保留。</p>}
    {draft.booking_policy !== "legacy" && <fieldset>
      <legend>接單設定</legend>
      <label className="check-field"><input type="checkbox" checked={businessOnly}
        onChange={(event) => setDraft({ ...draft, business_only: event.target.checked })} />僅開放會餐</label>
      <label className="check-field"><input type="checkbox" checked={sameDay} disabled={businessOnly}
        onChange={(event) => changeSameDay(event.target.checked)} />開放當日訂餐（僅線上付款）</label>
    </fieldset>}
    {initial ? <details ref={detailFields} className="meal-event-create">
      <summary>名稱、地點、時間與餐點</summary>
      {fields}
    </details> : fields}
    {recoveryError && <p className="form-error" role="alert">{recoveryError}</p>}
    {create.isError && <p className="form-error" role="alert">{create.error.message}</p>}
    <div className="form-actions">
      <button className="button button-primary" disabled={!canSave}><CalendarPlus size={17} />{initial ? "儲存場次修改" : replacement ? "建立補開草稿" : "建立草稿場次"}</button>
      {onCancel && <button className="button button-quiet" type="button" disabled={create.isPending} onClick={onCancel}>取消編輯</button>}
    </div>
  </form>;
}

const MAX_OFFERING_CAPACITY = 100_000;

function MealEventCard({ event, onDone, onEdit, onArchive, archiving }: {
  event: MealEvent; onDone: () => void; onEdit: () => void; onArchive: () => void; archiving: boolean;
}) {
  const queryClient = useQueryClient();
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [reason, setReason] = useState("");
  const [capacityDraft, setCapacityDraft] = useState<{ offeringId: string; expectedCapacity: number; additional: string } | null>(null);
  const action = useMutation({
    mutationFn: (name: "publish" | "cancel" | "complete" | "duplicate") => {
      if (name === "duplicate") return apiFetch<MealEvent>(`/v1/admin/meal-events/${event.id}/duplicate`, { method: "POST", body: JSON.stringify(eventPayload(shiftEvent(event))) });
      return apiFetch<MealEvent>(`/v1/admin/meal-events/${event.id}/${name}`, { method: "POST", body: JSON.stringify(name === "cancel" ? { reason } : reason.trim() ? { reason } : {}) });
    },
    onSuccess: async (updated, name) => {
      if (name === "cancel") {
        await queryClient.cancelQueries({ queryKey: ["admin-meal-events"] });
        queryClient.setQueryData<MealEvent[]>(["admin-meal-events"], (events) => events?.map((entry) => entry.id === updated.id ? updated : entry));
        setConfirmCancel(false);
      }
      setReason("");
      onDone();
    },
  });
  const isFinal = mealEventIsFinal(event);
  const orderingPaused = !isFinal && event.ordering_paused === true;
  const canChangeOrderingPause = mealCanChangeOrderingPause(event);
  const refreshOrdering = () => Promise.all([
    queryClient.invalidateQueries({ queryKey: ["admin-meal-events"] }),
    queryClient.invalidateQueries({ queryKey: ["meal-event", event.id] }),
    queryClient.invalidateQueries({ queryKey: ["meal-events"] }),
  ]);
  const applyEventUpdate = async (updated: MealEvent) => {
    await queryClient.cancelQueries({ queryKey: ["admin-meal-events"] });
    queryClient.setQueryData<MealEvent[]>(["admin-meal-events"], (events) => events?.map((entry) => entry.id === updated.id ? updated : entry));
    await refreshOrdering();
  };
  const refreshOnConflict = async (error: Error) => {
    if (error instanceof ApiError && error.status === 409) await refreshOrdering();
  };
  const changeOrderingPause = useMutation({
    mutationFn: (paused: boolean) => apiFetch<MealEvent>(`/v1/admin/meal-events/${event.id}/ordering-pause`, {
      method: "PATCH",
      body: JSON.stringify({
        paused,
        expected_version: event.ordering_pause_version ?? 0,
        ...(reason.trim() ? { reason: reason.trim() } : {}),
      }),
    }),
    onSuccess: async (updated) => {
      setReason("");
      await applyEventUpdate(updated);
    },
    onError: refreshOnConflict,
  });
  const increaseCapacity = useMutation({
    mutationFn: ({ offeringId, expectedCapacity, capacity }: { offeringId: string; expectedCapacity: number; capacity: number }) =>
      apiFetch<MealEvent>(`/v1/admin/meal-events/${event.id}/offerings/${offeringId}/capacity`, {
        method: "PATCH",
        body: JSON.stringify({ expected_capacity: expectedCapacity, capacity }),
      }),
    onSuccess: async (updated) => {
      setCapacityDraft(null);
      await applyEventUpdate(updated);
    },
    onError: refreshOnConflict,
  });
  const canIncreaseCapacity = event.can_edit === false && canChangeOrderingPause;
  const eligibleOfferings = event.offerings.filter((offering) => offering.is_active && offering.capacity < MAX_OFFERING_CAPACITY);
  const selectedOffering = event.offerings.find(({ id }) => id === capacityDraft?.offeringId);
  const selectedEligible = selectedOffering?.is_active === true && selectedOffering.capacity < MAX_OFFERING_CAPACITY;
  const capacityChanged = Boolean(selectedOffering && capacityDraft && selectedOffering.capacity !== capacityDraft.expectedCapacity);
  const capacityConflict = increaseCapacity.error instanceof ApiError && increaseCapacity.error.status === 409;
  const needsCapacityRecheck = capacityChanged || capacityConflict;
  const additional = Number(capacityDraft?.additional ?? "");
  const newCapacity = (capacityDraft?.expectedCapacity ?? 0) + additional;
  const validCapacity = canIncreaseCapacity && selectedEligible && !needsCapacityRecheck
    && Number.isInteger(additional) && additional > 0 && newCapacity <= MAX_OFFERING_CAPACITY;
  const selectCapacityOffering = (offering: MealEvent["offerings"][number]) => {
    increaseCapacity.reset();
    setCapacityDraft({ offeringId: offering.id, expectedCapacity: offering.capacity, additional: "" });
  };
  const eventActionPending = action.isPending || changeOrderingPause.isPending || increaseCapacity.isPending;
  return <article className="admin-review-card meal-event-admin-card">
    <div className="review-card-copy">
      <span className="status-chip">{adminMealEventLabel(event.status)}</span>
      {event.archived_at && <span className="status-chip">已封存</span>}
      {orderingPaused && <span className="status-chip" role="status">新預訂已暫停</span>}
      {orderingPaused && !canChangeOrderingPause && <p>目前沒有可恢復的預訂時段。</p>}
      {event.business_only && <span className="status-chip">僅開放會餐</span>}
      <h3>{event.title}</h3>
      <p>{event.location} · {formatMealDateTime(event.pickup_starts_at)} 至 {formatMealDateTime(event.pickup_ends_at)}</p>
      <div className="meal-event-offerings">
        {event.offerings.map((offering) => <span key={offering.id}>{offering.meal_name} {offering.paid_quantity}/{offering.capacity}</span>)}
        {canIncreaseCapacity && eligibleOfferings.length > 0 && <button className="button button-quiet" type="button" disabled={eventActionPending}
          aria-expanded={capacityDraft !== null} onClick={() => capacityDraft ? setCapacityDraft(null) : selectCapacityOffering(eligibleOfferings[0])}>追加份數</button>}
      </div>
      {capacityDraft && <form className="meal-capacity-form" aria-label="追加餐點份數" onSubmit={(submit) => {
        submit.preventDefault();
        if (validCapacity && !eventActionPending) increaseCapacity.mutate({
          offeringId: capacityDraft.offeringId, expectedCapacity: capacityDraft.expectedCapacity, capacity: newCapacity,
        });
      }}>
        <div className="field-grid two-columns">
          <label className="field"><span>餐點</span><select value={selectedEligible && selectedOffering ? selectedOffering.id : ""} disabled={!canIncreaseCapacity || eventActionPending} onChange={(change) => {
            const offering = eligibleOfferings.find(({ id }) => id === change.target.value);
            if (offering) selectCapacityOffering(offering);
          }}>
            <option value="" disabled>請選擇餐點</option>
            {eligibleOfferings.map((offering) => <option key={offering.id} value={offering.id}>{offering.meal_name}</option>)}
          </select></label>
          <label className="field"><span>增加份數</span><input required type="number" min={1} step={1} max={MAX_OFFERING_CAPACITY - capacityDraft.expectedCapacity}
            value={capacityDraft.additional} disabled={!canIncreaseCapacity || !selectedEligible || needsCapacityRecheck || eventActionPending}
            onChange={(change) => setCapacityDraft({ ...capacityDraft, additional: change.target.value })} /></label>
        </div>
        {selectedOffering && <p>目前總份數 {selectedOffering.capacity} · 追加後總份數 <output>{validCapacity ? newCapacity : "—"}</output></p>}
        {(!canIncreaseCapacity || !selectedEligible) && <p role="status">目前無法追加此餐點的份數。</p>}
        {(increaseCapacity.isError || capacityChanged) && <p className="form-error" role="alert">{increaseCapacity.error?.message || "供應份數已更新，請重新核對。"}</p>}
        <div className="form-actions">
          {needsCapacityRecheck && selectedOffering && selectedEligible && canIncreaseCapacity && <button className="button button-quiet" type="button" disabled={eventActionPending}
            onClick={() => selectCapacityOffering(selectedOffering)}>重新核對份數</button>}
          <button className="button button-system" type="submit" disabled={!validCapacity || eventActionPending}>{increaseCapacity.isPending ? "儲存中…" : "儲存份數"}</button>
          <button className="button button-quiet" type="button" disabled={eventActionPending} onClick={() => setCapacityDraft(null)}>取消</button>
        </div>
      </form>}
      {increaseCapacity.isSuccess && <p className="form-success" role="status">已儲存追加份數</p>}
    </div>
    <div className="review-card-actions">
      {!isFinal && <label className="field"><span>處理原因<OptionalMark /></span><input value={reason} onChange={(input) => setReason(input.target.value)} /></label>}
      <div>
        <button type="button" disabled={eventActionPending} onClick={() => action.mutate("duplicate")}><Copy size={16} />複製至隔日</button>
        {canChangeOrderingPause && <button type="button" disabled={eventActionPending} onClick={() => changeOrderingPause.mutate(!orderingPaused)}>{orderingPaused ? "恢復新預訂" : "暫停新預訂"}</button>}
        {event.status === "draft" && <button type="button" disabled={eventActionPending} onClick={() => action.mutate("publish")}><SealCheck size={16} />發布</button>}
        {!isFinal && event.status !== "draft" && Date.parse(event.pickup_ends_at) <= Date.now() && <button type="button" disabled={eventActionPending} onClick={() => action.mutate("complete")}>結束場次</button>}
        {!isFinal && <Dialog.Root open={confirmCancel} onOpenChange={open => {
          if (action.isPending) return;
          if (open) action.reset();
          setConfirmCancel(open);
        }}>
          <Dialog.Trigger asChild><button type="button" disabled={eventActionPending}>取消場次</button></Dialog.Trigger>
          <Dialog.Portal>
            <Dialog.Overlay className="admin-cancel-overlay" />
            <Dialog.Content className="admin-cancel-dialog">
              <Dialog.Title>取消「{event.title}」</Dialog.Title>
              <Dialog.Description>系統會取消本場訂單，並為已付款的訂單安排退款。此操作無法復原。</Dialog.Description>
              {action.isError && <p className="form-error" role="alert">{action.error.message}</p>}
              <div className="form-actions">
                <Dialog.Close asChild><button className="button button-quiet" type="button" disabled={action.isPending}>返回</button></Dialog.Close>
                <button className="button button-primary" type="button" disabled={action.isPending} onClick={() => action.mutate("cancel")}>{action.isPending ? "處理中…" : "取消場次並退款"}</button>
              </div>
            </Dialog.Content>
          </Dialog.Portal>
        </Dialog.Root>}
        <Link to="/meals/$eventId" params={{ eventId: event.id }}>查看前台</Link>
      </div>
    </div>
    {action.isError && !confirmCancel && <p className="form-error">{action.error.message}</p>}
    {changeOrderingPause.isError && <p className="form-error" role="alert">{changeOrderingPause.error.message}</p>}
    <div className="form-actions">
      {!isFinal && <button className="button button-quiet" type="button" disabled={event.can_edit !== true || eventActionPending} onClick={onEdit}>編輯場次</button>}
      {!isFinal && event.can_edit !== true && <span className="field-help">已有訂單或場次已截止，不能修改原場次。</span>}
      {isFinal && <button className="button button-quiet" type="button" disabled={eventActionPending || archiving} onClick={onArchive}>{event.archived_at ? "取消封存" : "封存場次"}</button>}
      {event.status === "cancelled" && <Link className="button button-quiet" to="/admin/meals"
        search={{ tab: "events", date: mealServiceDate(event), rebuild: event.id }}>建立補開草稿</Link>}
    </div>
    {event.business_only && <p className="meal-payment-options">取餐當日線上付款或付現</p>}
    {event.same_day_preparation_minutes != null && <p className="meal-payment-options">當日訂餐 · 一般便當線上付款</p>}
    <EventPickupOrders eventId={event.id} />
  </article>;
}

function EventPickupOrders({ eventId }: { eventId: string }) {
  const [open, setOpen] = useState(false);
  const [cashUncollected, setCashUncollected] = useState(false);
  const orders = useQuery({
    queryKey: ["admin-meal-event-orders", eventId],
    queryFn: () => apiFetch<MealOrder[]>(`/v1/admin/meal-events/${eventId}/orders`),
    enabled: open,
    refetchInterval: open ? 30000 : false,
  });
  const visibleOrders = orders.data?.filter(order => !cashUncollected || (
    order.meal_booking_type === "business" && order.payment_method === "cash_on_pickup"
    && !order.cancelled_at && ["pending", "expired", "failed"].includes(order.payment_status)
  ));
  const preparationQuantity = orders.data?.reduce((total, order) => {
    const cancelled = order.cancelled_at || order.fulfillment_status === "cancelled";
    const eligible = order.payment_status === "paid"
      || (order.meal_booking_type === "business" && order.payment_status === "pending");
    return !cancelled && eligible ? total + order.items.reduce((count, item) => count + item.quantity, 0) : total;
  }, 0);
  return <div className="meal-event-pickup-orders">
    <button className="button button-quiet" type="button" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "收起本場訂單" : "查看本場訂單與取餐時間"}</button>
    {open && <section aria-label="本場訂單與取餐時間">
      {orders.isPending && <LoadingLines count={2} />}
      {orders.isError && <DataState kind="error" title="本場訂單無法讀取" detail={orders.error.message} onAction={() => orders.refetch()} />}
      {preparationQuantity !== undefined && <p><strong>備餐 {preparationQuantity} 份</strong></p>}
      {Boolean(orders.data?.length) && <label className="check-field"><input type="checkbox" checked={cashUncollected}
        onChange={event => setCashUncollected(event.target.checked)} />現金未收</label>}
      {visibleOrders?.length === 0 && <p>{cashUncollected ? "本場沒有待收現金的會餐。" : "本場目前沒有訂單。"}</p>}
      <div className="admin-review-list">{visibleOrders?.map((order) => <article className="admin-record-card" key={order.id}>
        <h4 className="order-number">{order.order_number}</h4>
        <p>{order.customer_name || "—"} · {order.customer_email || "—"} · {order.contact_phone || "未提供電話"}</p>
        <p>付款方式：{mealPaymentMethodLabel(order)}</p>
        <OrderQuantityAmendment order={order} />
        <AdminCancelOrder order={order} />
        <AdminCashPayment order={order} onDone={() => { void orders.refetch(); }} />
        {order.cancellation_request && <p className="meal-cancellation-reason"><strong>{order.cancellation_request.status === "pending" ? "退單待審，訂單尚未取消" : order.cancellation_request.status === "approved" ? "退單已核准" : "退單未核准"}</strong>{order.cancellation_request.reason && `：${order.cancellation_request.reason}`}</p>}
        <p>預計取餐：{order.pickup_at ? formatMealDateTime(order.pickup_at) : "未指定，依場次供餐時段"}</p>
        <p>取餐點：{order.venue_name}{order.pickup_address && ` · ${order.pickup_address}`}</p>
        <p>餐具：{order.needs_utensils ? "需要餐具" : "不需要餐具"}</p>
        <p>{order.items.map((item) => `${item.meal_name} × ${item.quantity}`).join("、")}</p>
        <InvoiceTaxSummary variant="card" items={order.items} memberTaxExempt={order.member_tax_exempt_snapshot} taxAmount={order.tax_amount} invoiceTax={order.invoice_tax} invoiceStatus={order.invoice_status} />
        <p>{({ paid: "已付款", pending: "待付款", refunded: "已退款", refund_pending: "退款中", late_paid_refund_required: "付款晚到，退款處理中", expired: "付款逾期", failed: "付款失敗" } as Record<string, string>)[order.payment_status] || order.payment_status} · {order.cancelled_at ? "已取消" : Date.parse(order.pickup_end) <= Date.now() ? "取餐時段已結束" : "供餐中"}</p>
      </article>)}</div>
    </section>}
  </div>;
}

function DateTimeField({ label, value, onChange, invalid, errorId, max }: {
  label: string; value: string; onChange: (value: string) => void; invalid?: boolean; errorId?: string; max?: string;
}) {
  return <label className="field"><span>{label}（台灣時間）</span>
    <input required type="datetime-local" value={value} max={max} aria-invalid={invalid || undefined}
      aria-describedby={invalid ? errorId : undefined} onChange={(event) => onChange(event.target.value)} />
  </label>;
}

function priorDayCutoff(serviceDate: string): string {
  return serviceDate ? `${shiftMealDate(serviceDate, -1)}T20:00` : "";
}

function eventDraftCutoff(draft: EventDraft): string {
  const prior = priorDayCutoff(draft.pickup_starts_at.slice(0, 10));
  return draft.business_only && prior && draft.ordering_ends_at > prior ? prior : draft.ordering_ends_at;
}

function disableSameDay(draft: EventDraft): EventDraft {
  const cutoff = priorDayCutoff(draft.pickup_starts_at.slice(0, 10));
  return {
    ...draft,
    same_day_preparation_minutes: null,
    ordering_ends_at: cutoff,
    ordering_starts_at: cutoff && draft.ordering_starts_at >= cutoff ? `${cutoff.slice(0, 10)}T00:00` : draft.ordering_starts_at,
  };
}

function emptyEventDraft(): EventDraft {
  const orderDate = localDateTime(0).slice(0, 10);
  const pickupDate = localDateTime(1).slice(0, 10);
  return { title: "", booking_policy: "workday", location: "", ordering_starts_at: `${orderDate}T00:00`, ordering_ends_at: `${orderDate}T20:00`, pickup_starts_at: `${pickupDate}T11:00`, pickup_ends_at: `${pickupDate}T13:30`, offerings: [] };
}

function eventDraft(event: MealEvent): EventDraft {
  return {
    title: event.title, booking_policy: event.booking_policy || "legacy", location: event.location,
    same_day_preparation_minutes: event.same_day_preparation_minutes == null ? undefined : String(event.same_day_preparation_minutes),
    business_only: event.business_only ? true : undefined,
    allow_cash_payment: event.allow_cash_payment,
    ordering_starts_at: taipeiDateTimeInput(event.ordering_starts_at),
    ordering_ends_at: taipeiDateTimeInput(event.ordering_ends_at),
    pickup_starts_at: taipeiDateTimeInput(event.pickup_starts_at),
    pickup_ends_at: taipeiDateTimeInput(event.pickup_ends_at),
    offerings: event.offerings.map((item) => ({ meal_id: item.meal_id, price: item.price, capacity: item.capacity, position: item.position })),
  };
}

function replacementEventDraft(event: MealEvent): EventDraft {
  return {
    ...eventDraft({ ...event, offerings: event.offerings.filter(offering => offering.is_active) }),
    booking_policy: event.booking_policy === "special" ? "special" : "workday",
    allow_cash_payment: false,
  };
}

function shiftEvent(event: MealEvent): EventDraft {
  const shift = (value: string) => localInput(new Date(value).getTime() + 86400000);
  const shifted: EventDraft = {
    ...eventDraft(event), title: event.title.replace(/(?:（隔日）)+$/u, ""),
    booking_policy: event.booking_policy === "special" ? "special" : "workday",
    allow_cash_payment: false,
    ordering_starts_at: shift(event.ordering_starts_at), ordering_ends_at: shift(event.ordering_ends_at),
    pickup_starts_at: shift(event.pickup_starts_at), pickup_ends_at: shift(event.pickup_ends_at),
  };
  return shifted.same_day_preparation_minutes != null ? disableSameDay(shifted) : shifted;
}

function eventPayload(draft: EventDraft) {
  const preparation = draft.business_only ? null : draft.same_day_preparation_minutes;
  return {
    ...draft,
    same_day_preparation_minutes: preparation == null ? preparation : Number(preparation),
    ...(draft.business_only || preparation != null ? { allow_cash_payment: false } : {}),
    ordering_starts_at: taipeiInputToIso(draft.ordering_starts_at),
    ordering_ends_at: taipeiInputToIso(eventDraftCutoff(draft)),
    pickup_starts_at: taipeiInputToIso(draft.pickup_starts_at),
    pickup_ends_at: taipeiInputToIso(draft.pickup_ends_at),
  };
}

function localDateTime(days: number, hours = 0) { return localInput(Date.now() + (days * 24 + hours) * 3600000); }
function localInput(timestamp: number) { return taipeiDateTimeInput(timestamp); }
