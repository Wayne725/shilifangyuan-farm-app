import { OrderQuantityAmendment } from "../components/OrderQuantityAmendment";
import * as Tabs from "@radix-ui/react-tabs";
import { BowlFood, Buildings, CalendarPlus, Copy, QrCode, SealCheck } from "@phosphor-icons/react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";

import { AdminCancelOrder } from "../components/AdminCancelOrder";
import { AdminCashPayment } from "../components/AdminCashPayment";
import { AdminNav } from "../components/AdminNav";
import { AdminMealCancellations } from "../components/AdminMealCancellations";
import { AdminMealOrderSearch } from "../components/AdminMealOrderSearch";
import { MealScheduleSettings } from "../components/MealScheduleSettings";
import { MealEventConflicts } from "../components/MealEventConflicts";
import { MealOptionsEditor } from "../components/MealOptionsEditor";
import { MealImageEditor } from "../components/MealImageEditor";
import { DataState, LoadingLines } from "../components/Shared";
import { InvoiceTaxSummary } from "../components/InvoiceTaxSummary";
import { useAuth } from "../context/AuthContext";
import { apiFetch, formatMoney } from "../lib/api";
import { mealEventStatusLabel } from "../lib/labels";
import { formatMealDateTime, shiftMealDate, taipeiDateTimeInput, taipeiInputToIso } from "../lib/meal-time";
import type { Meal, MealEvent, MealOrder } from "../lib/types";
import { OptionalMark } from "../components/OptionalMark";

interface EventDraft {
  title: string;
  booking_policy: "legacy" | "workday" | "special";
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
  const [meals, events] = useQueries({
    queries: [
      { queryKey: ["admin-meals"], queryFn: () => apiFetch<Meal[]>("/v1/admin/meals"), enabled: isAdmin },
      { queryKey: ["admin-meal-events"], queryFn: () => apiFetch<MealEvent[]>("/v1/admin/meal-events"), enabled: isAdmin, refetchInterval: 30000 },
    ],
  });

  if (!isAdmin) {
    return <section className="admin-gate"><div className="admin-gate-mark"><Buildings size={38} weight="light" /></div><p className="eyebrow">MEAL OPERATIONS / 便當管理</p><h1>餐點與場次管理只向管理者開放。</h1>{!user ? <button className="button button-primary" type="button" onClick={openLogin}>管理者登入</button> : <Link className="button button-quiet" to="/shop">返回生活消費</Link>}</section>;
  }

  const refreshMeals = () => {
    void queryClient.invalidateQueries({ queryKey: ["admin-meals"] });
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
  };

  return (
    <section className="admin-module-page admin-meals-workspace">
      <header className="workspace-heading"><div><p className="eyebrow">MEAL OPERATIONS / 便當營運管理</p><h1>訂單查詢、退單與取餐</h1></div></header>
      <AdminNav />
      <Tabs.Root className="admin-social-tabs" value={activeTab} onValueChange={(tab) => void navigate({ search: { tab } })}>
        <Tabs.List className="tab-list" aria-label="便當營運分類"><Tabs.Trigger value="orders">訂單查詢與待辦</Tabs.Trigger><Tabs.Trigger value="cancellations">退單審核</Tabs.Trigger><Tabs.Trigger value="events">場次與核銷</Tabs.Trigger><Tabs.Trigger value="schedules">每日午晚餐排程</Tabs.Trigger><Tabs.Trigger value="meals">餐點主檔</Tabs.Trigger></Tabs.List>
        <Tabs.Content className="tab-content" value="orders"><AdminMealOrderSearch key={routeSearch.q || "all"} initialSearch={routeSearch.q} onReviewOrder={(q) => void navigate({ search: { tab: "cancellations", q } })} /></Tabs.Content>
        <Tabs.Content className="tab-content" value="cancellations"><AdminMealCancellations key={routeSearch.q || "all"} initialSearch={routeSearch.q} /></Tabs.Content>
        <Tabs.Content className="tab-content" value="events">
          <EventCreateForm meals={meals.data || []} events={events.data || []} onDone={refreshEvents} />
          {events.isPending && <LoadingLines count={4} />}
          {events.isError && <DataState kind="error" title="便當場次無法讀取" detail={events.error.message} />}
          {events.data?.length === 0 && <DataState title="目前沒有便當場次" detail="先建立餐點，再建立第一個預訂場次。" />}
          <div className="admin-review-list">{events.data?.map((event) => <MealEventCard key={event.id} event={event} meals={meals.data || []} onDone={refreshEvents} />)}</div>
        </Tabs.Content>
        <Tabs.Content className="tab-content" value="schedules"><MealScheduleSettings meals={meals.data || []} onEventsChanged={refreshEvents} /></Tabs.Content>
        <Tabs.Content className="tab-content" value="meals">
          <MealCreateForm onDone={refreshMeals} />
          {meals.isPending && <LoadingLines count={3} />}
          {meals.isError && <DataState kind="error" title="餐點資料無法讀取" detail={meals.error.message} />}
          <div className="admin-record-grid">{meals.data?.map((meal) => <article className="admin-record-card" key={meal.id}><BowlFood size={22} weight="light" /><span className={`status-chip ${meal.is_active ? "passed" : ""}`}>{meal.is_active ? "啟用" : "停用"}</span><h3>{meal.name}</h3><p>{meal.description || "尚未填寫餐點介紹"}</p><dl><div><dt>售價</dt><dd>{formatMoney(meal.price)}</dd></div><div><dt>選項</dt><dd>{meal.option_groups.length} 組</dd></div><div><dt>稅別</dt><dd>{meal.tax_type === "taxable" ? "應稅" : "免稅"}</dd></div></dl><MealOptionsEditor meal={meal} onDone={refreshMeals} /><MealImageEditor meal={meal} onDone={refreshMeals} /></article>)}</div>
        </Tabs.Content>
      </Tabs.Root>
    </section>
  );
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

function EventCreateForm({ meals, events = [], onDone, initial, onCancel }: { meals: Meal[]; events?: MealEvent[]; onDone: () => void; initial?: MealEvent; onCancel?: () => void }) {
  const [draft, setDraft] = useState<EventDraft>(() => initial ? eventDraft(initial) : emptyEventDraft());
  const [calendarDate, setCalendarDate] = useState<string>();
  const calendar = useQuery({
    queryKey: ["admin-meal-calendar", calendarDate],
    queryFn: () => apiFetch<{ service_date: string; is_workday: boolean; previous_workday: string; next_workday: string | null; ordering_starts_at: string; ordering_ends_at: string }>(`/v1/admin/meal-calendar?${calendarDate ? `service_date=${calendarDate}` : ""}`),
    enabled: (!initial || Boolean(calendarDate)) && draft.booking_policy === "workday",
  });
  useEffect(() => {
    if (!calendar.data || (initial && !calendarDate)) return;
    const value = calendar.data;
    setDraft((current) => current.booking_policy !== "workday" ? current : {
      ...current,
      ordering_starts_at: taipeiDateTimeInput(value.ordering_starts_at),
      ordering_ends_at: taipeiDateTimeInput(value.ordering_ends_at),
      ...(!calendarDate ? { pickup_starts_at: `${value.service_date}T11:00`, pickup_ends_at: `${value.service_date}T13:30` } : {}),
    });
  }, [calendar.data, calendarDate, initial]);
  const changePickupStart = (value: string) => {
    const date = value.slice(0, 10);
    const changedDate = date !== draft.pickup_starts_at.slice(0, 10);
    setDraft((current) => ({ ...current, pickup_starts_at: value, ...(changedDate && date ? {
      pickup_ends_at: `${date}T${current.pickup_ends_at.slice(11)}`,
      ordering_ends_at: `${shiftMealDate(date, -1)}T20:00`,
    } : {}) }));
    if (changedDate && date) setCalendarDate(date);
  };
  const activeMeals = meals.filter((meal) => meal.is_active || draft.offerings.some((offering) => offering.meal_id === meal.id));
  const selected = useMemo(() => new Set(draft.offerings.map((item) => item.meal_id)), [draft.offerings]);
  const create = useMutation({
    mutationFn: () => apiFetch<MealEvent>(initial ? `/v1/admin/meal-events/${initial.id}` : "/v1/admin/meal-events", { method: initial ? "PUT" : "POST", body: JSON.stringify(eventPayload(draft)) }),
    onSuccess: () => { setDraft((current) => ({ ...current, title: "", offerings: [] })); onDone(); },
  });
  const conflictNotice = <MealEventConflicts events={events} location={draft.location} startsAt={draft.pickup_starts_at} endsAt={draft.pickup_ends_at} editingId={initial?.id} />;
  const toggleMeal = (meal: Meal) => setDraft((current) => ({
    ...current,
    offerings: selected.has(meal.id)
      ? current.offerings.filter((item) => item.meal_id !== meal.id)
      : [...current.offerings, { meal_id: meal.id, price: meal.price, capacity: 20, position: current.offerings.length }],
  }));
  return <form className="social-form admin-master-form" onSubmit={(event) => { event.preventDefault(); create.mutate(); }}><div className="form-heading"><div><p className="eyebrow">EVENT SCHEDULE / 供餐排程</p><h2>{initial ? "手動編輯便當場次" : "建立便當場次"}</h2></div></div><fieldset><legend>供餐規則</legend><div className="form-actions" role="group" aria-label="供餐規則">{(initial && (!initial.booking_policy || initial.booking_policy === "legacy") ? ["legacy", "workday", "special"] as const : ["workday", "special"] as const).map((policy) => <button key={policy} type="button" className={`button ${draft.booking_policy === policy ? "button-primary" : "button-quiet"}`} aria-pressed={draft.booking_policy === policy} onClick={() => { setDraft({ ...draft, booking_policy: policy }); if (policy === "workday" && initial) setCalendarDate(draft.pickup_starts_at.slice(0, 10)); }}>{policy === "workday" ? "工作日供餐" : policy === "special" ? "特殊日期加開" : "沿用舊場次規則"}</button>)}</div></fieldset><div className="field-grid two-columns"><label className="field"><span>場次名稱</span><input required value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} /></label><label className="field"><span>取餐地點</span><input required value={draft.location} onChange={(event) => setDraft({ ...draft, location: event.target.value })} /></label><DateTimeField label="開放預訂" value={draft.ordering_starts_at} onChange={(value) => setDraft({ ...draft, ordering_starts_at: value })} /><DateTimeField label="預訂截止" value={draft.ordering_ends_at} onChange={(value) => setDraft({ ...draft, ordering_ends_at: value })} /><DateTimeField label="取餐開始" value={draft.pickup_starts_at} onChange={changePickupStart} /><DateTimeField label="取餐結束" value={draft.pickup_ends_at} onChange={(value) => setDraft({ ...draft, pickup_ends_at: value })} /></div>{draft.booking_policy === "workday" && calendar.isError && <p className="form-error" role="alert">{calendar.error.message}</p>}{draft.booking_policy === "workday" && calendar.data?.is_workday === false && <p className="form-error" role="alert">這一天不是工作日。{calendar.data.next_workday ? `下一工作日為 ${calendar.data.next_workday}；若需加開，請選「特殊日期加開」。` : "請選其他工作日或特殊加開。"}</p>}{conflictNotice}<div className="meal-choice-grid">{activeMeals.map((meal) => <label key={meal.id} className="meal-choice"><input type="checkbox" checked={selected.has(meal.id)} onChange={() => toggleMeal(meal)} /><span><strong>{meal.name}</strong><small>{formatMoney(meal.price)}</small></span></label>)}</div>{draft.offerings.map((offering) => { const meal = meals.find((item) => item.id === offering.meal_id); return <div className="meal-offering-editor" key={offering.meal_id}><strong>{meal?.name}</strong><label className="field"><span>本場售價</span><input min={0} type="number" value={offering.price} onChange={(event) => setDraft({ ...draft, offerings: draft.offerings.map((item) => item.meal_id === offering.meal_id ? { ...item, price: Number(event.target.value) } : item) })} /></label><label className="field"><span>份數</span><input min={1} type="number" value={offering.capacity} onChange={(event) => setDraft({ ...draft, offerings: draft.offerings.map((item) => item.meal_id === offering.meal_id ? { ...item, capacity: Number(event.target.value) } : item) })} /></label></div>; })}{create.isError && <p className="form-error">{create.error.message}</p>}<div className="form-actions"><button className="button button-primary" disabled={!draft.title.trim() || !draft.location.trim() || draft.offerings.length === 0 || create.isPending || (draft.booking_policy === "workday" && (calendar.isFetching || calendar.isError || calendar.data?.is_workday === false))}><CalendarPlus size={17} />{initial ? "儲存場次修改" : "建立草稿場次"}</button>{onCancel && <button className="button button-quiet" type="button" onClick={onCancel}>取消編輯</button>}</div></form>;
}

function MealEventCard({ event, meals, onDone }: { event: MealEvent; meals: Meal[]; onDone: () => void }) {
  const [editing, setEditing] = useState(false);
  const [reason, setReason] = useState("");
  const [credential, setCredential] = useState("");
  const [redeemed, setRedeemed] = useState("");
  const action = useMutation({
    mutationFn: (name: "publish" | "open-pickup" | "cancel" | "complete" | "duplicate") => {
      if (name === "duplicate") return apiFetch<MealEvent>(`/v1/admin/meal-events/${event.id}/duplicate`, { method: "POST", body: JSON.stringify(eventPayload(shiftEvent(event))) });
      return apiFetch<MealEvent>(`/v1/admin/meal-events/${event.id}/${name}`, { method: "POST", body: JSON.stringify(name === "cancel" ? { reason } : reason.trim() ? { reason } : {}) });
    },
    onSuccess: () => { setReason(""); onDone(); },
  });
  const redeem = useMutation({
    mutationFn: () => apiFetch<{ order_number: string }>(`/v1/admin/meal-events/${event.id}/redeem`, { method: "POST", body: JSON.stringify(/^\d{6}$/.test(credential) ? { pickup_code: credential } : { qr_token: credential }) }),
    onSuccess: (result) => { setRedeemed(`${result.order_number} 已完成取餐`); setCredential(""); onDone(); },
  });
  return <article className="admin-review-card meal-event-admin-card">
    <div className="review-card-copy"><span className="status-chip">{mealEventStatusLabel(event.status)}</span><h3>{event.title}</h3><p>{event.location} · {formatMealDateTime(event.pickup_starts_at)} 至 {formatMealDateTime(event.pickup_ends_at)}</p><div className="meal-event-offerings">{event.offerings.map((offering) => <span key={offering.id}>{offering.meal_name} {offering.paid_quantity}/{offering.capacity}</span>)}</div></div>
    <div className="review-card-actions"><label className="field"><span>處理原因<OptionalMark /></span><input value={reason} onChange={(input) => setReason(input.target.value)} /></label><div><button type="button" disabled={action.isPending} onClick={() => action.mutate("duplicate")}><Copy size={16} />複製至隔日</button>{event.status === "draft" && <button type="button" disabled={action.isPending} onClick={() => action.mutate("publish")}><SealCheck size={16} />發布</button>}{["published", "ordering_closed"].includes(event.status) && <button type="button" disabled={action.isPending} onClick={() => action.mutate("open-pickup")}>開放取餐</button>}{event.status === "pickup_open" && <button type="button" disabled={action.isPending} onClick={() => action.mutate("complete")}>結束場次</button>}{!["cancelled", "completed"].includes(event.status) && <button type="button" disabled={action.isPending} onClick={() => action.mutate("cancel")}>取消場次</button>}<Link to="/meals/$eventId" params={{ eventId: event.id }}>查看前台</Link></div></div>
    {event.status === "pickup_open" && <form className="meal-redeem-panel" onSubmit={(submit) => { submit.preventDefault(); redeem.mutate(); }}><QrCode size={24} weight="light" /><label className="field"><span>掃描 QR token 或輸入六位取餐碼</span><input autoComplete="off" value={credential} onChange={(input) => setCredential(input.target.value.trim())} /></label><button className="button button-system" disabled={credential.length < 6 || redeem.isPending}>確認核銷</button>{redeemed && <p className="form-success">{redeemed}</p>}{redeem.isError && <p className="form-error">{redeem.error.message}</p>}</form>}
    {action.isError && <p className="form-error">{action.error.message}</p>}
    <div className="form-actions"><button className="button button-quiet" type="button" disabled={event.can_edit !== true || action.isPending} onClick={() => setEditing(!editing)}>{editing ? "收起場次編輯" : "手動編輯場次"}</button>{event.can_edit !== true && <span className="field-help">已有訂單或場次已截止，不能修改原場次。</span>}</div>
    {editing && <EventCreateForm key={event.id} meals={meals} initial={event} onCancel={() => setEditing(false)} onDone={() => { setEditing(false); onDone(); }} />}
    <MealPaymentOptions event={event} onDone={onDone} />
    <EventPickupOrders eventId={event.id} />
  </article>;
}

function MealPaymentOptions({ event, onDone }: { event: MealEvent; onDone: () => void }) {
  const update = useMutation({
    mutationFn: (allow: boolean) => apiFetch(`/v1/admin/meal-events/${event.id}/payment-options`, { method: "PATCH", body: JSON.stringify({ allow_cash_payment: allow }) }),
    onSuccess: onDone,
  });
  return <div className="meal-payment-options"><label className="check-field"><input type="checkbox" checked={Boolean(event.allow_cash_payment)} disabled={update.isPending || ["cancelled", "completed"].includes(event.status)} onChange={input => update.mutate(input.target.checked)} />開放合作社現場付款</label>{update.isError && <p className="form-error" role="alert">{update.error.message}</p>}</div>;
}

function EventPickupOrders({ eventId }: { eventId: string }) {
  const [open, setOpen] = useState(false);
  const orders = useQuery({
    queryKey: ["admin-meal-event-orders", eventId],
    queryFn: () => apiFetch<MealOrder[]>(`/v1/admin/meal-events/${eventId}/orders`),
    enabled: open,
    refetchInterval: open ? 30000 : false,
  });
  return <div className="meal-event-pickup-orders">
    <button className="button button-quiet" type="button" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "收起本場訂單" : "查看本場訂單與取餐時間"}</button>
    {open && <section aria-label="本場訂單與取餐時間">
      {orders.isPending && <LoadingLines count={2} />}
      {orders.isError && <DataState kind="error" title="本場訂單無法讀取" detail={orders.error.message} onAction={() => orders.refetch()} />}
      {orders.data?.length === 0 && <p>本場目前沒有訂單。</p>}
      <div className="admin-review-list">{orders.data?.map((order) => <article className="admin-record-card" key={order.id}>
        <h4 className="order-number">{order.order_number}</h4>
        <p>付款方式：{order.payment_method === "cash_on_pickup" ? "合作社現場付款" : "線上付款"}</p>
        <OrderQuantityAmendment order={order} />
        <AdminCancelOrder order={order} />
        <AdminCashPayment order={order} onDone={() => { void orders.refetch(); }} />
        {order.cancellation_request && <p className="meal-cancellation-reason"><strong>{order.cancellation_request.status === "pending" ? "退單待審，訂單尚未取消" : order.cancellation_request.status === "approved" ? "退單已核准" : "退單未核准"}</strong>{order.cancellation_request.reason && `：${order.cancellation_request.reason}`}</p>}
        <p>預計取餐：{order.pickup_at ? formatMealDateTime(order.pickup_at) : "未指定，依場次供餐時段"}</p>
        <p>取餐點：{order.venue_name}{order.pickup_address && ` · ${order.pickup_address}`}</p>
        <p>餐具：{order.needs_utensils ? "需要餐具" : "不需要餐具"}</p>
        <p>{order.items.map((item) => `${item.meal_name} × ${item.quantity}`).join("、")}</p>
        <InvoiceTaxSummary variant="card" items={order.items} memberTaxExempt={order.member_tax_exempt_snapshot} taxAmount={order.tax_amount} invoiceTax={order.invoice_tax} invoiceStatus={order.invoice_status} />
        <p>{({ paid: "已付款", pending: "待付款", refunded: "已退款", refund_pending: "退款中", late_paid_refund_required: "付款晚到，退款處理中", expired: "付款逾期", failed: "付款失敗" } as Record<string, string>)[order.payment_status] || order.payment_status} · {order.cancelled_at ? "已取消" : order.fulfillment_status === "picked_up" ? "已取餐" : order.fulfillment_status === "no_show" ? "逾時未取" : "尚未取餐"}</p>
      </article>)}</div>
    </section>}
  </div>;
}

function DateTimeField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return <label className="field"><span>{label}（台灣時間）</span><input required type="datetime-local" value={value} onChange={(event) => onChange(event.target.value)} /></label>;
}

function emptyEventDraft(): EventDraft {
  const orderDate = localDateTime(0).slice(0, 10);
  const pickupDate = localDateTime(1).slice(0, 10);
  return { title: "", booking_policy: "workday", location: "", ordering_starts_at: `${orderDate}T00:00`, ordering_ends_at: `${orderDate}T20:00`, pickup_starts_at: `${pickupDate}T11:00`, pickup_ends_at: `${pickupDate}T13:30`, offerings: [] };
}

function eventDraft(event: MealEvent): EventDraft {
  return {
    title: event.title, booking_policy: event.booking_policy || "legacy", location: event.location,
    ordering_starts_at: taipeiDateTimeInput(event.ordering_starts_at),
    ordering_ends_at: taipeiDateTimeInput(event.ordering_ends_at),
    pickup_starts_at: taipeiDateTimeInput(event.pickup_starts_at),
    pickup_ends_at: taipeiDateTimeInput(event.pickup_ends_at),
    offerings: event.offerings.map((item) => ({ meal_id: item.meal_id, price: item.price, capacity: item.capacity, position: item.position })),
  };
}

function shiftEvent(event: MealEvent): EventDraft {
  const shift = (value: string) => localInput(new Date(value).getTime() + 86400000);
  return { title: event.title.replace(/(?:（隔日）)+$/u, ""), booking_policy: event.booking_policy === "special" ? "special" : "workday", allow_cash_payment: Boolean(event.allow_cash_payment), location: event.location, ordering_starts_at: shift(event.ordering_starts_at), ordering_ends_at: shift(event.ordering_ends_at), pickup_starts_at: shift(event.pickup_starts_at), pickup_ends_at: shift(event.pickup_ends_at), offerings: event.offerings.map((item) => ({ meal_id: item.meal_id, price: item.price, capacity: item.capacity, position: item.position })) };
}

function eventPayload(draft: EventDraft) {
  return { ...draft, ordering_starts_at: taipeiInputToIso(draft.ordering_starts_at), ordering_ends_at: taipeiInputToIso(draft.ordering_ends_at), pickup_starts_at: taipeiInputToIso(draft.pickup_starts_at), pickup_ends_at: taipeiInputToIso(draft.pickup_ends_at) };
}

function localDateTime(days: number, hours = 0) { return localInput(Date.now() + (days * 24 + hours) * 3600000); }
function localInput(timestamp: number) { return taipeiDateTimeInput(timestamp); }
