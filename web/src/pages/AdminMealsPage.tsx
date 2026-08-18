import * as Tabs from "@radix-ui/react-tabs";
import { BowlFood, Buildings, CalendarPlus, Copy, QrCode, SealCheck } from "@phosphor-icons/react";
import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { type FormEvent, useMemo, useState } from "react";

import { AdminNav } from "../components/AdminNav";
import { DataState, LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import { apiFetch, formatDateTime, formatMoney } from "../lib/api";
import type { Meal, MealEvent } from "../lib/types";

interface EventDraft {
  title: string;
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
  const [meals, events] = useQueries({
    queries: [
      { queryKey: ["admin-meals"], queryFn: () => apiFetch<Meal[]>("/v1/admin/meals"), enabled: isAdmin },
      { queryKey: ["admin-meal-events"], queryFn: () => apiFetch<MealEvent[]>("/v1/admin/meal-events"), enabled: isAdmin },
    ],
  });

  if (!isAdmin) {
    return <section className="admin-gate"><div className="admin-gate-mark"><Buildings size={38} weight="light" /></div><p className="eyebrow">MEAL OPERATIONS</p><h1>餐點與場次管理只向管理者開放。</h1>{!user ? <button className="button button-primary" type="button" onClick={openLogin}>管理者登入</button> : <Link className="button button-quiet" to="/shop">返回生活消費</Link>}</section>;
  }

  const refreshMeals = () => queryClient.invalidateQueries({ queryKey: ["admin-meals"] });
  const refreshEvents = () => queryClient.invalidateQueries({ queryKey: ["admin-meal-events"] });

  return (
    <section className="admin-module-page">
      <header className="workspace-heading"><div><p className="eyebrow">MEAL OPERATIONS / 共煮預訂</p><h1>餐點、場次與現場核銷</h1></div><p>從餐點建檔、發布場次到 QR／六位碼核銷，都集中在同一個工作區。</p></header>
      <AdminNav />
      <Tabs.Root className="admin-social-tabs" defaultValue="events">
        <Tabs.List className="tab-list" aria-label="便當營運分類"><Tabs.Trigger value="events">場次與核銷</Tabs.Trigger><Tabs.Trigger value="meals">餐點主檔</Tabs.Trigger></Tabs.List>
        <Tabs.Content className="tab-content" value="events">
          <EventCreateForm meals={meals.data || []} onDone={refreshEvents} />
          {events.isPending && <LoadingLines count={4} />}
          {events.isError && <DataState kind="error" title="便當場次無法讀取" detail={events.error.message} />}
          {events.data?.length === 0 && <DataState title="目前沒有便當場次" detail="先建立餐點，再建立第一個預訂場次。" />}
          <div className="admin-review-list">{events.data?.map((event) => <MealEventCard key={event.id} event={event} onDone={refreshEvents} />)}</div>
        </Tabs.Content>
        <Tabs.Content className="tab-content" value="meals">
          <MealCreateForm onDone={refreshMeals} />
          {meals.isPending && <LoadingLines count={3} />}
          {meals.isError && <DataState kind="error" title="餐點資料無法讀取" detail={meals.error.message} />}
          <div className="admin-record-grid">{meals.data?.map((meal) => <article className="admin-record-card" key={meal.id}><BowlFood size={22} weight="light" /><span className={`status-chip ${meal.is_active ? "passed" : ""}`}>{meal.is_active ? "啟用" : "停用"}</span><h3>{meal.name}</h3><p>{meal.description || "尚未填寫餐點介紹"}</p><dl><div><dt>售價</dt><dd>{formatMoney(meal.price)}</dd></div><div><dt>稅別</dt><dd>{meal.tax_type === "taxable" ? "應稅" : "免稅"}</dd></div></dl></article>)}</div>
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
  return <form className="social-form admin-master-form" onSubmit={(event) => { event.preventDefault(); create.mutate(); }}><div className="form-heading"><div><p className="eyebrow">MEAL RECORD</p><h2>建立餐點</h2></div></div><div className="field-grid three-columns"><label className="field"><span>餐點名稱</span><input required value={name} onChange={(event) => setName(event.target.value)} /></label><label className="field"><span>單價</span><input min={0} type="number" value={price} onChange={(event) => setPrice(Number(event.target.value))} /></label><label className="field"><span>稅別</span><select value={taxType} onChange={(event) => setTaxType(event.target.value as typeof taxType)}><option value="taxable">應稅</option><option value="tax_exempt">免稅</option></select></label><label className="field wide"><span>餐點介紹</span><textarea rows={3} value={description} onChange={(event) => setDescription(event.target.value)} /></label></div>{create.isError && <p className="form-error">{create.error.message}</p>}<div className="form-actions"><button className="button button-primary" disabled={!name.trim() || create.isPending}>建立餐點</button></div></form>;
}

function EventCreateForm({ meals, onDone }: { meals: Meal[]; onDone: () => void }) {
  const [draft, setDraft] = useState<EventDraft>(() => emptyEventDraft());
  const activeMeals = meals.filter((meal) => meal.is_active);
  const selected = useMemo(() => new Set(draft.offerings.map((item) => item.meal_id)), [draft.offerings]);
  const create = useMutation({
    mutationFn: () => apiFetch<MealEvent>("/v1/admin/meal-events", { method: "POST", body: JSON.stringify(eventPayload(draft)) }),
    onSuccess: () => { setDraft(emptyEventDraft()); onDone(); },
  });
  const toggleMeal = (meal: Meal) => setDraft((current) => ({
    ...current,
    offerings: selected.has(meal.id)
      ? current.offerings.filter((item) => item.meal_id !== meal.id)
      : [...current.offerings, { meal_id: meal.id, price: meal.price, capacity: 20, position: current.offerings.length }],
  }));
  return <form className="social-form admin-master-form" onSubmit={(event) => { event.preventDefault(); create.mutate(); }}><div className="form-heading"><div><p className="eyebrow">EVENT SCHEDULE</p><h2>建立便當場次</h2></div><span>可在同一場加入多個餐點與不同容量。</span></div><div className="field-grid two-columns"><label className="field"><span>場次名稱</span><input required value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} /></label><label className="field"><span>取餐地點</span><input required value={draft.location} onChange={(event) => setDraft({ ...draft, location: event.target.value })} /></label><DateTimeField label="開放預訂" value={draft.ordering_starts_at} onChange={(value) => setDraft({ ...draft, ordering_starts_at: value })} /><DateTimeField label="預訂截止" value={draft.ordering_ends_at} onChange={(value) => setDraft({ ...draft, ordering_ends_at: value })} /><DateTimeField label="取餐開始" value={draft.pickup_starts_at} onChange={(value) => setDraft({ ...draft, pickup_starts_at: value })} /><DateTimeField label="取餐結束" value={draft.pickup_ends_at} onChange={(value) => setDraft({ ...draft, pickup_ends_at: value })} /></div><div className="meal-choice-grid">{activeMeals.map((meal) => <label key={meal.id} className="meal-choice"><input type="checkbox" checked={selected.has(meal.id)} onChange={() => toggleMeal(meal)} /><span><strong>{meal.name}</strong><small>{formatMoney(meal.price)}</small></span></label>)}</div>{draft.offerings.map((offering) => { const meal = meals.find((item) => item.id === offering.meal_id); return <div className="meal-offering-editor" key={offering.meal_id}><strong>{meal?.name}</strong><label className="field"><span>本場售價</span><input min={0} type="number" value={offering.price} onChange={(event) => setDraft({ ...draft, offerings: draft.offerings.map((item) => item.meal_id === offering.meal_id ? { ...item, price: Number(event.target.value) } : item) })} /></label><label className="field"><span>份數</span><input min={1} type="number" value={offering.capacity} onChange={(event) => setDraft({ ...draft, offerings: draft.offerings.map((item) => item.meal_id === offering.meal_id ? { ...item, capacity: Number(event.target.value) } : item) })} /></label></div>; })}{create.isError && <p className="form-error">{create.error.message}</p>}<div className="form-actions"><button className="button button-primary" disabled={!draft.title.trim() || !draft.location.trim() || draft.offerings.length === 0 || create.isPending}><CalendarPlus size={17} />建立草稿場次</button></div></form>;
}

function MealEventCard({ event, onDone }: { event: MealEvent; onDone: () => void }) {
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
  return <article className="admin-review-card meal-event-admin-card"><div className="review-card-copy"><span className="status-chip">{mealEventStatus(event.status)}</span><h3>{event.title}</h3><p>{event.location} · {formatDateTime(event.pickup_starts_at)} 至 {formatDateTime(event.pickup_ends_at)}</p><div className="meal-event-offerings">{event.offerings.map((offering) => <span key={offering.id}>{offering.meal_name} {offering.paid_quantity}/{offering.capacity}</span>)}</div></div><div className="review-card-actions"><label className="field"><span>處理原因（取消時必填）</span><input value={reason} onChange={(input) => setReason(input.target.value)} /></label><div><button type="button" disabled={action.isPending} onClick={() => action.mutate("duplicate")}><Copy size={16} />複製至下週</button>{event.status === "draft" && <button type="button" disabled={action.isPending} onClick={() => action.mutate("publish")}><SealCheck size={16} />發布</button>}{["published", "ordering_closed"].includes(event.status) && <button type="button" disabled={action.isPending} onClick={() => action.mutate("open-pickup")}>開放取餐</button>}{event.status === "pickup_open" && <button type="button" disabled={action.isPending} onClick={() => action.mutate("complete")}>結束場次</button>}{!["cancelled", "completed"].includes(event.status) && <button type="button" disabled={!reason.trim() || action.isPending} onClick={() => action.mutate("cancel")}>取消場次</button>}<Link to="/meals/$eventId" params={{ eventId: event.id }}>查看前台</Link></div></div>{event.status === "pickup_open" && <form className="meal-redeem-panel" onSubmit={(submit) => { submit.preventDefault(); redeem.mutate(); }}><QrCode size={24} weight="light" /><label className="field"><span>掃描 QR token 或輸入六位取餐碼</span><input autoComplete="off" value={credential} onChange={(input) => setCredential(input.target.value.trim())} /></label><button className="button button-system" disabled={credential.length < 6 || redeem.isPending}>確認核銷</button>{redeemed && <p className="form-success">{redeemed}</p>}{redeem.isError && <p className="form-error">{redeem.error.message}</p>}</form>}{action.isError && <p className="form-error">{action.error.message}</p>}</article>;
}

function DateTimeField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return <label className="field"><span>{label}</span><input required type="datetime-local" value={value} onChange={(event) => onChange(event.target.value)} /></label>;
}

function emptyEventDraft(): EventDraft {
  return { title: "", location: "", ordering_starts_at: localDateTime(1), ordering_ends_at: localDateTime(5), pickup_starts_at: localDateTime(6), pickup_ends_at: localDateTime(6, 2), offerings: [] };
}

function shiftEvent(event: MealEvent): EventDraft {
  const shift = (value: string) => localInput(new Date(value).getTime() + 7 * 86400000);
  return { title: `${event.title}（下週）`, location: event.location, ordering_starts_at: shift(event.ordering_starts_at), ordering_ends_at: shift(event.ordering_ends_at), pickup_starts_at: shift(event.pickup_starts_at), pickup_ends_at: shift(event.pickup_ends_at), offerings: event.offerings.map((item) => ({ meal_id: item.meal_id, price: item.price, capacity: item.capacity, position: item.position })) };
}

function eventPayload(draft: EventDraft) {
  return { ...draft, ordering_starts_at: new Date(draft.ordering_starts_at).toISOString(), ordering_ends_at: new Date(draft.ordering_ends_at).toISOString(), pickup_starts_at: new Date(draft.pickup_starts_at).toISOString(), pickup_ends_at: new Date(draft.pickup_ends_at).toISOString() };
}

function localDateTime(days: number, hours = 0) { return localInput(Date.now() + (days * 24 + hours) * 3600000); }
function localInput(timestamp: number) { const value = new Date(timestamp); return new Date(value.getTime() - value.getTimezoneOffset() * 60000).toISOString().slice(0, 16); }
function mealEventStatus(status: string) { return { draft: "草稿", published: "開放預訂", ordering_closed: "預訂截止", pickup_open: "開放取餐", cancelled: "已取消", completed: "已完成" }[status] || status; }
