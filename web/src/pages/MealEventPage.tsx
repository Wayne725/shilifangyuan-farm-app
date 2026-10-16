import {
  ArrowLeft,
  BowlFood,
  MapPin,
  Minus,
  Plus,
} from "@phosphor-icons/react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";

import { DataState, LoadingLines } from "../components/Shared";
import { InvoiceTaxSummary } from "../components/InvoiceTaxSummary";
import { MealPhoto } from "../components/MealPhoto";
import {
  defaultInvoicePreference,
  InvoicePreferenceFields,
  InvoicePreferenceSummary,
  invoicePreferenceIsValid,
} from "../components/InvoicePreferenceFields";
import { useAuth } from "../context/AuthContext";
import { openOrderHandoff } from "../lib/checkout-navigation";
import { apiFetch, formatMoney } from "../lib/api";
import { beginMealCheckout, invoicePreferencePayload } from "../lib/commerce";
import { taxTypeLabel } from "../lib/invoice-tax";
import { clearMealDraft, emptyMealDraft, mealDraftWasAdjusted, readMealDraft, reconcileMealDraft, writeMealDraft } from "../lib/meal-draft";
import { formatMealDateTime, mealBookingStatus, mealOrderingOpen, mealServiceDate, pickupTimeOptions, shiftMealDate, taipeiDate, taipeiInputToIso, validPickupTime } from "../lib/meal-time";
import type {
  MealEvent,
  MealOptionGroup,
  MealOptionSelection,
} from "../lib/types";

interface MealQuote {
  amount_total: number;
  membership_type?: string;
  member_tax_exempt?: boolean;
  tax_amount?: number;
  pickup_at?: string | null;
  items: Array<{
    offering_id: string;
    meal_name: string;
    quantity: number;
    base_price: number;
    option_price: number;
    unit_price: number;
    subtotal: number;
    tax_type?: string;
    selections: MealOptionSelection[];
  }>;
  pickup: { location: string; starts_at: string; ends_at: string };
}

export function MealEventPage() {
  const { eventId } = useParams({ from: "/meals/$eventId" });
  const { pickup_at } = useSearch({ from: "/meals/$eventId" });
  return <MealEventOrder key={eventId} eventId={eventId} initialPickupAt={pickup_at} />;
}

function MealEventOrder({ eventId, initialPickupAt }: { eventId: string; initialPickupAt?: string }) {
  const navigate = useNavigate({ from: "/meals/$eventId" });
  const { user, openLogin } = useAuth();
  const notificationEmail = user?.notification_email || user?.email || "";
  const [draft, setDraft] = useState(() => ({ ...readMealDraft(eventId), ...(initialPickupAt ? { pickupAt: initialPickupAt } : {}) }));
  const [draftAdjusted, setDraftAdjusted] = useState(false);
  const [now, setNow] = useState(Date.now);
  const [invoicePreference, setInvoicePreference] = useState(
    () => defaultInvoicePreference(notificationEmail),
  );
  const event = useQuery({
    queryKey: ["meal-event", eventId],
    queryFn: () => apiFetch<MealEvent>(`/v1/meal-events/${eventId}`),
    refetchInterval: 30000,
  });
  const currentDraft = useMemo(
    () => event.data ? reconcileMealDraft(draft, event.data, now) : draft,
    [draft, event.data, now],
  );
  const { quantities, selections, pickupAt } = currentDraft;
  const pickupReady = Boolean(event.data && validPickupTime(pickupAt, event.data, now));
  const orderingOpen = Boolean(event.data && mealOrderingOpen(event.data, now));
  const activeOfferings = event.data?.offerings.filter((item) => item.is_active) || [];
  const items = useMemo(
    () => activeOfferings
      .filter((offering) => (quantities[offering.id] || 0) > 0)
      .map((offering) => ({
        offering_id: offering.id,
        quantity: quantities[offering.id],
        option_ids: offering.option_groups.filter((group) => group.is_active).flatMap(
          (group) => selections[offering.id]?.[group.id] || [],
        ),
      })),
    [activeOfferings, quantities, selections],
  );
  const selectionsReady = items.every((item) => {
    const offering = activeOfferings.find((candidate) => candidate.id === item.offering_id);
    return offering?.option_groups.filter((group) => group.is_active).every((group) => {
      const count = selections[offering.id]?.[group.id]?.length || 0;
      return count >= group.min_selections && count <= group.max_selections;
    });
  });
  const itemKey = items
    .map((item) => `${item.offering_id}:${item.quantity}:${[...item.option_ids].sort().join(",")}`)
    .join("|");
  const quote = useQuery({
    queryKey: ["meal-quote", eventId, itemKey, pickupAt, invoicePreference, user?.id],
    queryFn: () => apiFetch<MealQuote>(`/v1/meal-events/${eventId}/quote`, {
      method: "POST",
      body: JSON.stringify({
        items,
        pickup_at: taipeiInputToIso(pickupAt),
        contact_email: invoicePreference.buyerEmail.trim(),
        ...invoicePreferencePayload(invoicePreference),
      }),
    }),
    enabled:
      items.length > 0 &&
      orderingOpen &&
      pickupReady &&
      selectionsReady &&
      invoicePreferenceIsValid(invoicePreference),
  });
  const checkout = useMutation({
    mutationFn: () => {
      if (!user) throw new Error("請先登入");
      if (!event.data || !mealOrderingOpen(event.data)) throw new Error("只能提前一天預訂，且須在本場次的開放時間內送出");
      if (!event.data || !validPickupTime(pickupAt, event.data)) throw new Error("請重新選擇可預訂的半小時取餐時段");
      return beginMealCheckout({
        eventId,
        pickupAt: taipeiInputToIso(pickupAt),
        items,
        contactEmail: invoicePreference.buyerEmail.trim(),
        invoicePreference,
      });
    },
    onSuccess: (outcome) => {
      clearMealDraft(eventId);
      setDraft(emptyMealDraft());
      setDraftAdjusted(false);
      if (outcome.kind === "redirect") return openOrderHandoff(outcome.url, outcome.orderId);
      const search = new URLSearchParams({ order_id: outcome.orderId, result: "resume", message: outcome.message });
      window.location.assign(`/orders?${search.toString()}`);
    },
  });

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    if (initialPickupAt) setDraft((current) => ({ ...current, pickupAt: initialPickupAt }));
  }, [initialPickupAt]);
  useEffect(() => {
    if (!event.data) return;
    if (mealDraftWasAdjusted(draft, currentDraft)) setDraftAdjusted(true);
    if (JSON.stringify(draft) !== JSON.stringify(currentDraft)) setDraft(currentDraft);
    writeMealDraft(eventId, currentDraft);
  }, [currentDraft, draft, event.data, eventId]);

  useEffect(() => {
    if (!notificationEmail) return;
    setInvoicePreference((current) =>
      current.buyerEmail ? current : { ...current, buyerEmail: notificationEmail },
    );
  }, [notificationEmail]);

  if (event.isPending) return <section className="offer-page"><LoadingLines count={4} /></section>;
  if (event.isError || !event.data) {
    return <section className="offer-page"><DataState kind="error" title="餐期資料無法讀取" detail={event.error?.message || "找不到這個餐期"} /></section>;
  }

  const mealEvent = event.data;
  const pickupOptions = pickupTimeOptions(mealEvent, now);
  const tomorrow = shiftMealDate(taipeiDate(now), 1);
  const canOrder =
    orderingOpen &&
    pickupReady &&
    items.length > 0 &&
    selectionsReady &&
    invoicePreferenceIsValid(invoicePreference);
  const selectedMeals = activeOfferings
    .filter((offering) => (quantities[offering.id] || 0) > 0)
    .map((offering) => {
      const selectedOptions = offering.option_groups.filter((group) => group.is_active).flatMap((group) =>
        group.options
          .filter((option) => option.is_active && selections[offering.id]?.[group.id]?.includes(option.id))
          .map((option) => ({ label: `${group.name}：${option.name}`, price: option.price_delta })),
      );
      const quantity = quantities[offering.id];
      return {
        offering_id: offering.id,
        meal_name: offering.meal_name,
        quantity,
        options: selectedOptions.map((option) => option.label),
        subtotal: (offering.price + selectedOptions.reduce((sum, option) => sum + option.price, 0)) * quantity,
      };
    });
  const estimatedTotal = selectedMeals.reduce((sum, item) => sum + item.subtotal, 0);
  const confirmedQuote = quote.isError ? undefined : quote.data;
  const selectedMealQuantity = selectedMeals.reduce((sum, item) => sum + item.quantity, 0);
  const totalLabel = confirmedQuote ? "合計" : "預估合計";
  const totalDisplay = confirmedQuote
    ? formatMoney(confirmedQuote.amount_total)
    : selectedMeals.length > 0 ? formatMoney(estimatedTotal) : "尚未選餐";

  const changeQuantity = (offeringId: string, next: number, maximum: number) => {
    setDraftAdjusted(false);
    setDraft((current) => ({
      ...current,
      quantities: { ...current.quantities, [offeringId]: Math.max(0, Math.min(next, maximum, 99)) },
    }));
  };

  const toggleOption = (offeringId: string, group: MealOptionGroup, optionId: string) => {
    setDraftAdjusted(false);
    setDraft((current) => {
      const selected = current.selections[offeringId]?.[group.id] || [];
      const next = selected.includes(optionId)
        ? selected.filter((id) => id !== optionId)
        : group.max_selections === 1
          ? [optionId]
          : selected.length < group.max_selections
            ? [...selected, optionId]
            : selected;
      return {
        ...current,
        selections: {
          ...current.selections,
          [offeringId]: { ...current.selections[offeringId], [group.id]: next },
        },
      };
    });
  };

  return (
    <section className="offer-page meal-offer-page">
      <Link className="text-link" to="/shop"><ArrowLeft size={17} />回到便當預購</Link>
      <div className="offer-hero meal-offer-hero">
        <div className="offer-copy">
          <p className="meal-event-status">{mealBookingStatus(mealEvent, now)}</p>
          <p className="eyebrow">MEAL PREORDER / 便當預訂</p>
          <h1>選餐點與份數</h1>
          <dl>
            <div><dt>取餐日期</dt><dd>{mealServiceDate(mealEvent).replaceAll("-", "/")}{mealServiceDate(mealEvent) === tomorrow ? "（明天）" : ""}</dd></div>
            <div><dt>取餐地點</dt><dd>{mealEvent.location}</dd></div>
            <div><dt>預訂截止</dt><dd>{formatMealDateTime(mealEvent.booking_ends_at || mealEvent.ordering_ends_at)}</dd></div>
            <div><dt>取餐方式</dt><dd>到指定地點現場領餐</dd></div>
          </dl>
        </div>
      </div>

      <div className="offer-checkout meal-checkout">
        <div>
          <section className="meal-selected-date" aria-label="已選取餐日期">
            <div className="checkout-step-title"><span>01</span><div><h2>取餐日期</h2></div></div>
            <p><strong>{mealServiceDate(mealEvent).replaceAll("-", "/")}{mealServiceDate(mealEvent) === tomorrow ? "（明天）" : ""}{pickupReady && ` · ${pickupAt.slice(11)}`}</strong><Link className="text-link" to="/shop">更換取餐時間</Link></p>
          </section>
          {draftAdjusted && <p className="meal-selection-notice" role="status">選餐已依最新庫存或可選時段調整，請再次確認餐點、份數與取餐時間。</p>}
          <div className="checkout-step-title"><span>02</span><div><h2>選擇餐點與份數</h2></div></div>
          <div className="meal-offering-list">
            {activeOfferings.map((offering) => {
              const quantity = quantities[offering.id] || 0;
              return (
                <article className={quantity > 0 ? "meal-offering selected" : "meal-offering"} key={offering.id}>
                  <div className="meal-offering-summary">
                    <MealPhoto className="meal-offering-photo" src={offering.image_url} alt={`${offering.meal_name}照片`} />
                    <div className="meal-offering-copy"><small>剩餘 {offering.available_quantity} 份</small><h3>{offering.meal_name}</h3>{offering.description && <p>{offering.description}</p>}<strong>{formatMoney(offering.price)} 起</strong></div>
                    <div className="quantity-control">
                      <button type="button" disabled={quantity === 0} onClick={() => changeQuantity(offering.id, quantity - 1, offering.available_quantity)} aria-label={`減少${offering.meal_name}`}><Minus size={20} /></button>
                      <span aria-label={`${offering.meal_name} ${quantity} 份`}>{quantity}</span>
                      <button type="button" disabled={quantity >= Math.min(offering.available_quantity, 99)} onClick={() => changeQuantity(offering.id, quantity + 1, offering.available_quantity)} aria-label={`增加${offering.meal_name}`}><Plus size={20} /></button>
                    </div>
                  </div>
                  {quantity > 0 && offering.option_groups.some((group) => group.is_active) && (
                    <div className="meal-option-groups">
                      {offering.option_groups.filter((group) => group.is_active).map((group) => {
                        const selected = selections[offering.id]?.[group.id] || [];
                        const valid = selected.length >= group.min_selections && selected.length <= group.max_selections;
                        return (
                          <fieldset className="meal-option-group" key={group.id}>
                            <legend><span>{group.name}</span><small>{selectionRule(group)}</small></legend>
                            <div className="meal-option-list">
                              {group.options.filter((option) => option.is_active).map((option) => (
                                <button
                                  aria-pressed={selected.includes(option.id)}
                                  aria-label={`${option.name}${option.price_delta > 0 ? ` +${formatMoney(option.price_delta)}` : ""}`}
                                  className={selected.includes(option.id) ? "selected" : ""}
                                  key={option.id}
                                  onClick={() => toggleOption(offering.id, group, option.id)}
                                  type="button"
                                >
                                  <MealPhoto className="meal-option-photo" src={option.image_url} alt={`${option.name}照片`} />
                                  <span className="meal-option-label"><span>{option.name}</span>{option.price_delta > 0 && <small>+{formatMoney(option.price_delta)}</small>}</span>
                                </button>
                              ))}
                            </div>
                            {!valid && <p className="meal-option-error">請完成此項選擇</p>}
                          </fieldset>
                        );
                      })}
                    </div>
                  )}
                </article>
              );
            })}
          </div>
          {activeOfferings.length === 0 && <DataState title="菜單待公告" detail="管理員公布餐點後即可選餐，請稍後再查看。" />}
        </div>

        <aside className="offer-summary meal-summary" id="meal-order-summary" tabIndex={-1} aria-labelledby="meal-summary-heading">
          <BowlFood size={29} weight="light" />
          <h2 id="meal-summary-heading">確認訂單明細</h2>
          <div className="pickup-window"><MapPin size={19} /><div><strong>{mealEvent.location}</strong><small>取餐日期：{mealServiceDate(mealEvent).replaceAll("-", "/")}</small>{pickupReady && <small>預計取餐：{formatMealDateTime(taipeiInputToIso(pickupAt))}</small>}</div></div>
          {quote.isFetching && <LoadingLines count={2} />}
          {quote.isError && <p className="form-error">{quote.error.message}</p>}
          {items.length > 0 && !selectionsReady && <p className="meal-selection-notice">請完成餐點的必選項目。</p>}
          {!pickupAt && <p className="meal-selection-notice">請選擇預計取餐時間。</p>}
          {!orderingOpen && <p className="meal-selection-notice">今天僅可預訂明天的餐點；本場次也須在開放時間內才能送出。</p>}
          <dl>
            {confirmedQuote ? confirmedQuote.items.map((item) => (
              <div className="meal-quote-line" key={item.offering_id}>
                <dt><strong>{item.meal_name} × {item.quantity}</strong><small>{taxTypeLabel(item.tax_type)}</small>{item.selections.length > 0 && <small>{item.selections.map((selection) => `${selection.group_name}：${selection.option_name}`).join(" · ")}</small>}</dt>
                <dd>{formatMoney(item.subtotal)}</dd>
              </div>
            )) : selectedMeals.map((item) => (
              <div className="meal-quote-line" key={item.offering_id}>
                <dt><strong>{item.meal_name} × {item.quantity}</strong>{item.options.length > 0 && <small>{item.options.join(" · ")}</small>}</dt>
                <dd>{formatMoney(item.subtotal)}</dd>
              </div>
            ))}
            <InvoicePreferenceSummary value={invoicePreference} />
            {confirmedQuote && <InvoiceTaxSummary items={confirmedQuote.items} memberTaxExempt={confirmedQuote.member_tax_exempt} taxAmount={confirmedQuote.tax_amount} />}
            <div className="offer-total"><dt>{totalLabel}</dt><dd>{totalDisplay}</dd></div>
          </dl>
          <div className="meal-checkout-settings">
            <section className="meal-pickup-selection" aria-label="選擇取餐時間">
              <div className="checkout-step-title"><span>03</span><div><h2>選擇取餐時間</h2></div></div>
              <div className="meal-pickup-options" role="group" aria-label="預計取餐時間（台灣時間）">{pickupOptions.map((option) => <button key={option.value} type="button" aria-pressed={pickupAt === option.value} disabled={!orderingOpen} onClick={() => { setDraftAdjusted(false); setDraft((current) => ({ ...current, pickupAt: option.value })); void navigate({ search: { pickup_at: option.value }, replace: true }); }}>{option.label}</button>)}</div>
              {!pickupOptions.length && <p className="meal-selection-notice">目前沒有可預訂的取餐時段。請回到便當預訂選擇明天的餐點。</p>}
              {pickupReady && <p className="meal-pickup-confirmation" role="status">已選取餐時間：{formatMealDateTime(taipeiInputToIso(pickupAt))}</p>}
            </section>
            <InvoicePreferenceFields
              onChange={setInvoicePreference}
              sectionNumber="04"
              value={invoicePreference}
            />
          </div>
          <button className="button button-quiet full-width meal-draft-reset" type="button" disabled={checkout.isPending || (!selectedMealQuantity && !pickupAt)} onClick={() => { clearMealDraft(eventId); setDraft(emptyMealDraft()); setDraftAdjusted(false); void navigate({ search: { pickup_at: undefined }, replace: true }); }}>清空選餐與取餐時間</button>
          {!user ? (
            <button className="button button-primary full-width" type="button" onClick={openLogin}>登入後預訂</button>
          ) : (
            <button className="button button-system full-width" type="button" disabled={!canOrder || !quote.data || quote.isFetching || quote.isError || checkout.isPending} onClick={() => checkout.mutate()}>
              {checkout.isPending ? "建立餐點訂單中…" : "預訂並前往線上付款"}
            </button>
          )}
          {checkout.isError && <p className="form-error">{checkout.error.message}</p>}
        </aside>
      </div>
      <nav className="meal-summary-shortcut" aria-label="餐點金額與明細入口">
        <div aria-live="polite" aria-atomic="true"><span>已選 {selectedMealQuantity} 份・{totalLabel}</span><strong>{totalDisplay}</strong></div>
        <a className="button button-system" href="#meal-order-summary">查看明細</a>
      </nav>
    </section>
  );
}

function selectionRule(group: MealOptionGroup): string {
  if (group.min_selections === group.max_selections) return `必選 ${group.min_selections} 項`;
  if (group.min_selections === 0) return `最多選 ${group.max_selections} 項`;
  return `選 ${group.min_selections}–${group.max_selections} 項`;
}
