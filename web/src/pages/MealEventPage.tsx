import { StorefrontClosed, useStorefrontStatus } from "../components/StorefrontStatus";
import {
  ArrowLeft,
  BowlFood,
  Minus,
  Plus,
} from "@phosphor-icons/react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";

import { DataState, LoadingLines } from "../components/Shared";
import { PaymentMethodFields } from "../components/PaymentMethodFields";
import { InvoiceTaxSummary } from "../components/InvoiceTaxSummary";
import { MealPhoto } from "../components/MealPhoto";
import {
  InvoicePreferenceFields,
  InvoicePreferenceSummary,
  invoicePreferenceIsValid,
  useInvoicePreference,
} from "../components/InvoicePreferenceFields";
import { useAuth } from "../context/AuthContext";
import { openOrderHandoff } from "../lib/checkout-navigation";
import { apiFetch, formatMoney } from "../lib/api";
import { beginMealCheckout, invoicePreferencePayload } from "../lib/commerce";
import { taxTypeLabel } from "../lib/invoice-tax";
import { saveInvoiceCarrierIfRequested } from "../lib/invoice-carrier";
import { mealSubmissionFingerprint } from "../lib/meal-submission";
import "./MealEventPage.css";
import { clearMealDraft, emptyMealDraft, mealDraftWasAdjusted, readMealDraft, reconcileMealDraft, writeMealDraft } from "../lib/meal-draft";
import { formatMealDateTime, mealBookingStatus, mealOrderingOpen, mealServiceDate, pickupTimeOptions, shiftMealDate, taipeiDate, taipeiInputToIso, validPickupTime } from "../lib/meal-time";
import type {
  MealEvent,
  MealOptionGroup,
  MealOptionSelection,
  PickupLocation,
} from "../lib/types";

interface MealQuote {
  promotion?: {name: string; discount: number};
  original_amount?: number;
  discount_amount?: number;
  amount_total: number;
  membership_type?: string;
  member_tax_exempt?: boolean;
  tax_amount?: number;
  pickup_at?: string | null;
  needs_utensils: boolean;
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
  pickup: { location: string; location_id: string | null; address: string; instructions: string; starts_at: string; ends_at: string };
}

export function MealEventPage() {
  const { eventId } = useParams({ from: "/meals/$eventId" });
  const { pickup_at } = useSearch({ from: "/meals/$eventId" });
  return <MealEventOrder key={eventId} eventId={eventId} initialPickupAt={pickup_at} />;
}

function MealEventOrder({ eventId, initialPickupAt }: { eventId: string; initialPickupAt?: string }) {
  const platform = useStorefrontStatus();
  const navigate = useNavigate({ from: "/meals/$eventId" });
  const { user, openLogin, saveMobileBarcode } = useAuth();
  const notificationEmail = user?.notification_email || user?.email || "";
  const [kitchenNote, setKitchenNote] = useState("");
  const submission = useRef<{storageKey: string; id: string} | null>(null);
  const [draft, setDraft] = useState(() => ({ ...readMealDraft(eventId), ...(initialPickupAt ? { pickupAt: initialPickupAt } : {}) }));
  const [paymentMethod, setPaymentMethod] = useState<"online" | "cash_on_pickup">("online");
  const [couponInput, setCouponInput] = useState("");
  const [couponCode, setCouponCode] = useState("");
  const [draftAdjusted, setDraftAdjusted] = useState(false);
  const [now, setNow] = useState(Date.now);
  const [invoicePreference, setInvoicePreference] = useInvoicePreference(notificationEmail);
  const invoiceReady = paymentMethod === "cash_on_pickup" ? /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(invoicePreference.buyerEmail.trim()) : invoicePreferenceIsValid(invoicePreference);
  const [editingInvoice, setEditingInvoice] = useState(false);
  const event = useQuery({
    queryKey: ["meal-event", eventId],
    queryFn: () => apiFetch<MealEvent>(`/v1/meal-events/${eventId}`),
    refetchInterval: 30000,
  });
  const locations = useQuery({
    queryKey: ["pickup-locations"],
    queryFn: () => apiFetch<PickupLocation[]>("/v1/pickup-locations"),
    refetchInterval: 30000,
  });
  const activeLocations = useMemo(() => locations.data?.filter((location) => location.is_active), [locations.data]);
  const currentDraft = useMemo(
    () => event.data ? reconcileMealDraft(draft, event.data, now, activeLocations) : draft,
    [draft, event.data, now, activeLocations],
  );
  const { quantities, selections, pickupAt, pickupLocationId, needsUtensils } = currentDraft;
  const selectedPickupLocation = activeLocations?.find((location) => location.id === pickupLocationId);
  const pickupLocationReady = !locations.isError && Boolean(activeLocations && (!activeLocations.length || selectedPickupLocation));
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
    queryKey: ["meal-quote", eventId, itemKey, pickupAt, pickupLocationId, needsUtensils, invoicePreference, user?.id, couponCode, paymentMethod],
    queryFn: () => apiFetch<MealQuote>(`/v1/meal-events/${eventId}/quote`, {
      method: "POST",
      body: JSON.stringify({
        items,
        coupon_code: couponCode,
        pickup_at: taipeiInputToIso(pickupAt),
        pickup_location_id: pickupLocationId || null,
        needs_utensils: needsUtensils,
        contact_email: invoicePreference.buyerEmail.trim(),
        payment_method: paymentMethod,
        ...(paymentMethod === "online" ? invoicePreferencePayload(invoicePreference) : {}),
      }),
    }),
    enabled:
      items.length > 0 &&
      orderingOpen &&
      pickupReady &&
      pickupLocationReady &&
      selectionsReady &&
      invoiceReady,
  });
  const checkout = useMutation({
    mutationFn: async () => {
      if (platform.data?.orders_open === false) throw new Error(platform.data.closed_message);
      if (!user) throw new Error("請先登入");
      if (!event.data || !mealOrderingOpen(event.data)) throw new Error("只能提前一天預訂，且須在本場次的開放時間內送出");
      if (!event.data || !validPickupTime(pickupAt, event.data)) throw new Error("請重新選擇可預訂的半小時取餐時段");
      if (!pickupLocationReady) throw new Error("請選擇可用的取餐點");
      if (paymentMethod === "online") {
        await saveInvoiceCarrierIfRequested(invoicePreference, user.saved_mobile_barcode, saveMobileBarcode);
      }
      const input = {
        eventId,
        couponCode,
        expectedTotal: quote.data?.amount_total,
        pickupAt: taipeiInputToIso(pickupAt),
        pickupLocationId,
        needsUtensils,
        items,
        contactEmail: invoicePreference.buyerEmail.trim(),
        invoicePreference,
        paymentMethod,
        kitchenNote: kitchenNote.trim(),
      };
      const fingerprint = mealSubmissionFingerprint(user.id, input);
      const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(fingerprint));
      const storageKey = `meal-submission:${Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, "0")).join("")}`;
      const id = sessionStorage.getItem(storageKey) || crypto.randomUUID();
      sessionStorage.setItem(storageKey, id);
      submission.current = {storageKey, id};
      return beginMealCheckout({...input, clientRequestId: submission.current.id});
    },
    onSuccess: (outcome) => {
      if (submission.current) sessionStorage.removeItem(submission.current.storageKey);
      clearMealDraft(eventId);
      setDraft(emptyMealDraft());
      setDraftAdjusted(false);
      if (outcome.kind === "redirect") return openOrderHandoff(outcome.url, outcome.orderId);
      if (outcome.kind === "reserved") {
        window.location.assign(`/meal-orders?${new URLSearchParams({ order_id: outcome.orderId })}`);
        return;
      }
      const search = new URLSearchParams({ order_id: outcome.orderId, result: "resume", message: outcome.message });
      window.location.assign(`/orders?${search.toString()}`);
    },
  });

  useEffect(() => {
    if (event.data && !event.data.allow_cash_payment) setPaymentMethod("online");
  }, [event.data?.allow_cash_payment]);

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

  if (platform.data?.orders_open === false) return <StorefrontClosed message={platform.data.closed_message} />;
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
    pickupLocationReady &&
    items.length > 0 &&
    selectionsReady &&
    invoiceReady;
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
  const confirmedQuote = quote.isError || !canOrder ? undefined : quote.data;
  const pickupAddress = confirmedQuote?.pickup.address || selectedPickupLocation?.address;
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
      <Link className="text-link" to="/shop" search={{ tab: "meals" }}><ArrowLeft size={17} />回到便當預購</Link>
      <div className="offer-hero meal-offer-hero">
        <div className="offer-copy">
          <p className="eyebrow">MEAL PREORDER / 便當預訂</p>
          <h1>選餐點與份數</h1>
          <div className="meal-booking-meta"><span className="meal-event-status">{mealBookingStatus(mealEvent, now)}</span><p className="meal-booking-deadline">預訂截止：{formatMealDateTime(mealEvent.booking_ends_at || mealEvent.ordering_ends_at)}</p></div>
        </div>
      </div>

      <div className="offer-checkout meal-checkout">
        <div>
          {draftAdjusted && <p className="meal-selection-notice" role="status">選餐已依最新庫存、取餐點或可選時段調整，請再次確認餐點、份數與取餐安排。</p>}
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
                        const activeOptions = group.options.filter((option) => option.is_active);
                        const compactChoice = group.max_selections === 1 && activeOptions.length >= 3 && activeOptions.every((option) => !option.image_url);
                        const fieldId = `meal-option-${offering.id}-${group.id}`;
                        return (
                          <fieldset className="meal-option-group" key={group.id}>
                            <legend id={`${fieldId}-label`}><span>{group.name}</span><small>{selectionRule(group)}</small></legend>
                            {compactChoice ? (
                              <div className="field">
                                <select
                                  aria-label={`${offering.meal_name} ${group.name}`}
                                  aria-describedby={!valid ? `${fieldId}-error` : undefined}
                                  aria-invalid={!valid}
                                  value={selected[0] || ""}
                                  required={group.min_selections > 0}
                                  disabled={checkout.isPending}
                                  onChange={(event) => {
                                    const optionId = event.target.value;
                                    setDraftAdjusted(false);
                                    setDraft((current) => ({ ...current, selections: { ...current.selections, [offering.id]: { ...current.selections[offering.id], [group.id]: optionId ? [optionId] : [] } } }));
                                  }}
                                >
                                  <option value="" disabled={group.min_selections > 0}>{group.min_selections > 0 ? "請選擇" : "不選擇"}</option>
                                  {activeOptions.map((option) => <option key={option.id} value={option.id}>{option.name}{option.price_delta > 0 ? ` +${formatMoney(option.price_delta)}` : option.price_delta < 0 ? ` −${formatMoney(Math.abs(option.price_delta))}` : ""}</option>)}
                                </select>
                              </div>
                            ) : <div className="meal-option-list">
                              {activeOptions.map((option) => (
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
                            </div>}
                            {!valid && <p id={`${fieldId}-error`} className="meal-option-error">請完成此項選擇</p>}
                          </fieldset>
                        );
                      })}
                    </div>
                  )}
                </article>
              );
            })}
          </div>
          <PaymentMethodFields value={paymentMethod} onChange={setPaymentMethod} allowCash={Boolean(mealEvent.allow_cash_payment)} disabled={checkout.isPending} />
          {activeOfferings.length === 0 && <DataState title="菜單待公告" detail="管理員公布餐點後即可選餐，請稍後再查看。" />}
        </div>

        <aside className="offer-summary meal-summary" id="meal-order-summary" tabIndex={-1} aria-labelledby="meal-summary-heading">
          <BowlFood size={29} weight="light" />
          <h2 id="meal-summary-heading">確認訂單明細</h2>
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
            {confirmedQuote?.promotion && <div><dt>{confirmedQuote.promotion.name}</dt><dd>已折抵 {formatMoney(confirmedQuote.promotion.discount)}</dd></div>}
            {confirmedQuote && <InvoiceTaxSummary items={confirmedQuote.items} memberTaxExempt={confirmedQuote.member_tax_exempt} taxAmount={confirmedQuote.tax_amount} />}
          </dl>
          <section className="meal-pickup-settings" aria-labelledby="meal-pickup-heading">
            <div className="meal-pickup-heading"><h3 id="meal-pickup-heading">取餐安排</h3><Link className="text-link" to="/shop" search={{ tab: "meals" }}>更換取餐日期</Link></div>
            <p className="meal-pickup-date"><strong>{mealServiceDate(mealEvent).replaceAll("-", "/")}{mealServiceDate(mealEvent) === tomorrow ? "（明天）" : ""}</strong></p>
            <label className="field"><span>預計取餐時間（台灣時間）</span>
              <select value={pickupReady ? pickupAt : ""} required disabled={!orderingOpen || checkout.isPending || !pickupOptions.length} onChange={(event) => { const value = event.target.value; setDraftAdjusted(false); setDraft((current) => ({ ...current, pickupAt: value })); void navigate({ search: { pickup_at: value }, replace: true }); }}>
                <option value="" disabled>請選擇取餐時間</option>
                {pickupOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            </label>
            {!pickupOptions.length && <p className="meal-selection-notice">目前沒有可預訂的取餐時段。請回到便當預訂選擇明天的餐點。</p>}
            <label className="field"><span id="meal-pickup-location-label">取餐點</span>
              <select aria-labelledby="meal-pickup-location-label" value={pickupLocationId} required={Boolean(activeLocations?.length)} disabled={checkout.isPending || locations.isPending || locations.isError || !activeLocations?.length} onChange={(event) => { setDraftAdjusted(false); setDraft((current) => ({ ...current, pickupLocationId: event.target.value })); }}>
                <option value="" disabled={Boolean(activeLocations?.length)}>{locations.isPending ? "載入取餐點中…" : locations.isError ? "取餐點暫時無法使用" : activeLocations?.length ? "請選擇取餐點" : mealEvent.location}</option>
                {!locations.isError && activeLocations?.map((location) => <option key={location.id} value={location.id}>{location.name}</option>)}
              </select>
            </label>
            {pickupAddress && <p className="meal-pickup-address">{pickupAddress}</p>}
            {locations.isError && <DataState kind="error" title="取餐點暫時無法使用" detail={locations.error.message} onAction={() => locations.refetch()} />}
            <label className="field"><span id="meal-utensils-label">是否需要餐具</span>
              <select aria-labelledby="meal-utensils-label" value={needsUtensils ? "yes" : "no"} disabled={checkout.isPending} onChange={(event) => setDraft((current) => ({ ...current, needsUtensils: event.target.value === "yes" }))}>
                <option value="no">不需要餐具</option><option value="yes">需要餐具</option>
              </select>
            </label>
          </section>
          <details className="meal-coupon-disclosure">
            <summary>{couponCode ? `優惠碼：${couponCode}` : "使用優惠碼"}</summary>
            <div className="meal-coupon-field"><label className="field"><span>優惠碼</span><input maxLength={32} value={couponInput} onChange={event => setCouponInput(event.target.value.toUpperCase())} /></label><button className="button button-quiet" type="button" onClick={() => setCouponCode(couponInput.trim())}>套用優惠碼</button>{couponCode && <button type="button" className="text-link" onClick={() => {setCouponCode(""); setCouponInput("");}}>移除優惠碼</button>}</div>
          </details>
          <div className="meal-checkout-settings">
            {paymentMethod === "cash_on_pickup" ? <section className="meal-invoice-settings"><h3>合作社現場開票</h3><label className="field"><span>訂單通知 Email</span><input type="email" autoComplete="email" required value={invoicePreference.buyerEmail} onChange={event => setInvoicePreference({ ...invoicePreference, buyerEmail: event.target.value })} /></label></section> : <section className="meal-invoice-settings" aria-label="電子發票設定">
              <div className="meal-invoice-heading"><h3>電子發票</h3>
                {invoicePreferenceIsValid(invoicePreference) && <button type="button" className="text-link" aria-expanded={editingInvoice} aria-controls="meal-invoice-fields" onClick={() => setEditingInvoice(!editingInvoice)}>{editingInvoice ? "完成發票設定" : "修改發票設定"}</button>}
              </div>
              <div id="meal-invoice-fields" hidden={!editingInvoice && invoicePreferenceIsValid(invoicePreference)}><InvoicePreferenceFields onChange={(value) => { setEditingInvoice(true); setInvoicePreference(value); }} sectionNumber="02" showHeading={false} value={invoicePreference} /></div>
              {!editingInvoice && invoicePreferenceIsValid(invoicePreference) && <dl className="meal-invoice-summary"><InvoicePreferenceSummary value={invoicePreference} typeLabel="發票形式" /></dl>}
            </section>}
          </div>
          <label className="field"><span>餐點備註（選填）</span><textarea maxLength={500} value={kitchenNote} onChange={event => setKitchenNote(event.target.value)} /></label>
          <div className="meal-payment-action">
          <dl className="meal-payment-total"><div className="offer-total"><dt>{totalLabel}</dt><dd>{totalDisplay}</dd></div></dl>
          {!user ? (
            <button className="button button-primary full-width" type="button" onClick={openLogin}>登入後預訂</button>
          ) : (
            <button className="button button-system full-width" type="button" disabled={!canOrder || !quote.data || quote.isFetching || quote.isError || checkout.isPending} onClick={() => checkout.mutate()}>
              {checkout.isPending ? "建立餐點訂單中…" : paymentMethod === "cash_on_pickup" ? "確認預訂・現場付款" : "預訂並前往線上付款"}
            </button>
          )}
          {checkout.isError && <p className="form-error">{checkout.error.message}</p>}
          </div>
          <button className="text-link meal-draft-reset" type="button" disabled={checkout.isPending || (!selectedMealQuantity && !pickupAt)} onClick={() => { clearMealDraft(eventId); setDraft(emptyMealDraft()); setDraftAdjusted(false); void navigate({ search: { pickup_at: undefined }, replace: true }); }}>清空選餐與取餐時間</button>
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
