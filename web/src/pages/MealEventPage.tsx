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
import { sessionStore } from "../lib/browser-storage";
import { ApiError, apiFetch, captureSessionIdentityGuard, formatMoney, isCurrentSessionUser } from "../lib/api";
import { beginMealCheckout, invoicePreferencePayload, type InvoicePreference } from "../lib/commerce";
import { contactPhoneIsValid } from "../lib/contact-phone";
import { taxTypeLabel } from "../lib/invoice-tax";
import { saveInvoiceCarrierIfRequested } from "../lib/invoice-carrier";
import { forgetMealSubmission, mealSubmissionStorageKey, rememberMealSubmission, unmatchedMealSubmissions } from "../lib/meal-submission";
import "./MealEventPage.css";
import { clearMealDraft, emptyMealDraft, mealDraftWasAdjusted, readMealDraft, reconcileMealDraft, writeMealDraft } from "../lib/meal-draft";
import { formatMealDateTime, mealBookingDeadline, mealBookingStatus, mealOrderingOpen, mealPickupDeadline, mealPickupSchedule, mealServiceDate, pickupTimeOptions, shiftMealDate, taipeiDate, taipeiInputToIso, validPickupTime } from "../lib/meal-time";
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

interface MealSubmissionReview {
  userId: string;
  storageKey: string;
  unmatched: string;
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
  const [emailInput, setEmailInput] = useState<{ userId: string; value: string }>();
  const [phoneInput, setPhoneInput] = useState<{ userId: string; value: string }>();
  const savedPhone = useQuery({
    queryKey: ["meal-checkout-contact", user?.id],
    queryFn: ({ signal }) => apiFetch<{ phone: string | null }>("/v1/meal-checkout/contact", { signal }),
    enabled: Boolean(user),
    retry: false,
  });
  const contactEmail = emailInput?.userId === user?.id ? emailInput?.value ?? "" : notificationEmail;
  const contactPhone = phoneInput?.userId === user?.id ? phoneInput?.value ?? "" : savedPhone.data?.phone || "";
  const checkoutInFlight = useRef(false);
  const contactEmailReady = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(contactEmail.trim()) && contactEmail.trim().length <= 320;
  const contactPhoneReady = Boolean(contactPhone.trim()) && contactPhoneIsValid(contactPhone);
  const [draft, setDraft] = useState(() => ({ ...readMealDraft(eventId), ...(initialPickupAt ? { pickupAt: initialPickupAt } : {}) }));
  const setMealBookingType = (value: "standard" | "business") => setDraft(current => ({ ...current, mealBookingType: value === "business" ? "business" : undefined }));
  const event = useQuery({
    queryKey: ["meal-event", eventId],
    queryFn: () => apiFetch<MealEvent>(`/v1/meal-events/${eventId}`),
    refetchInterval: 30000,
  });
  const businessOnly = event.data?.business_only === true;
  const mealBookingType = businessOnly ? "business" : draft.mealBookingType || "standard";
  const business = mealBookingType === "business";
  const [couponInput, setCouponInput] = useState("");
  const [couponCode, setCouponCode] = useState("");
  const [draftAdjusted, setDraftAdjusted] = useState(false);
  const [now, setNow] = useState(Date.now);
  const [standardPreference, setStandardPreference] = useInvoicePreference(notificationEmail);
  const [businessPreference, setBusinessPreference] = useInvoicePreference(notificationEmail);
  const invoicePreference: InvoicePreference = business ? { ...businessPreference, buyerType: "company", buyerTaxId: businessPreference.buyerType === "company" ? businessPreference.buyerTaxId : "", buyerName: businessPreference.buyerType === "company" ? businessPreference.buyerName : "" } : standardPreference;
  const setInvoicePreference = (value: InvoicePreference) => business ? setBusinessPreference(value) : setStandardPreference(value);
  const [submitted, setSubmitted] = useState(false);
  const [touched, setTouched] = useState<Record<string, boolean>>({});
  const contactEmailError = !contactEmailReady && Boolean(submitted || touched.email);
  const contactPhoneError = !contactPhoneReady && Boolean(submitted || touched.phone);
  const invoiceReady = invoicePreferenceIsValid(invoicePreference);
  const [editingInvoice, setEditingInvoice] = useState(false);
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
  const orderingPaused = event.data?.ordering_paused === true;
  const pickupOptions = event.data
    ? (orderingPaused ? mealPickupSchedule : pickupTimeOptions)(event.data, now, mealBookingType)
    : [];
  const pickupReady = pickupOptions.some((option) => option.value === pickupAt);
  const orderingOpen = Boolean(event.data && mealOrderingOpen(event.data, now, mealBookingType));
  const businessClosed = business && event.data && (businessOnly || event.data.same_day_preparation_minutes != null)
    && now >= mealBookingDeadline(event.data, mealBookingType);
  const activeOfferings = event.data?.offerings.filter((item) => item.is_active) || [];
  const selectionLines = activeOfferings.flatMap(offering => {
    const quantity = quantities[offering.id] || 0;
    if (!quantity) return [];
    const portions = business ? currentDraft.portionSelections?.[offering.id] : undefined;
    const groups = offering.option_groups.filter(group => group.is_active);
    const grouped = new Map<string, { optionIds: string[]; quantity: number; valid: boolean }>();
    for (const selection of portions || [selections[offering.id] || {}]) {
      const optionIds = groups.flatMap(group => selection[group.id] || []);
      const key = JSON.stringify([...optionIds].sort());
      const line = grouped.get(key);
      if (line) line.quantity += 1;
      else grouped.set(key, { optionIds, quantity: portions ? 1 : quantity, valid: groups.every(group => {
        const count = selection[group.id]?.length || 0;
        return count >= group.min_selections && count <= group.max_selections;
      }) });
    }
    return [...grouped.values()].map(line => ({ ...line, offering }));
  });
  const items = selectionLines.map(({ offering, quantity, optionIds }) => ({
    offering_id: offering.id, quantity, option_ids: optionIds,
  }));
  const selectionsReady = selectionLines.every(line => line.valid);
  const lineLimitExceeded = items.length > 20;
  const itemKey = items
    .map((item) => `${item.offering_id}:${item.quantity}:${[...item.option_ids].sort().join(",")}`)
    .join("|");
  const totalQuantity = items.reduce((sum, item) => sum + item.quantity, 0);
  const quantityReady = !business || totalQuantity >= 2;
  const quote = useQuery({
    queryKey: ["meal-quote", eventId, itemKey, pickupAt, pickupLocationId, needsUtensils, invoicePreference, user?.id, couponCode, contactEmail.trim(), mealBookingType],
    queryFn: () => apiFetch<MealQuote>(`/v1/meal-events/${eventId}/quote`, {
      method: "POST",
      body: JSON.stringify({
        items,
        coupon_code: couponCode,
        pickup_at: taipeiInputToIso(pickupAt),
        pickup_location_id: pickupLocationId || null,
        needs_utensils: needsUtensils,
        contact_email: contactEmail.trim(),
        payment_method: "online",
        ...(business ? { meal_booking_type: "business", invoice_handling: "pickup_day" } : {}),
        ...invoicePreferencePayload(invoicePreference),
      }),
    }),
    enabled:
      items.length > 0 &&
      quantityReady &&
      orderingOpen &&
      pickupReady &&
      pickupLocationReady &&
      selectionsReady && !lineLimitExceeded &&
      invoiceReady && contactEmailReady,
  });
  const checkout = useMutation({
    onMutate: captureSessionIdentityGuard,
    mutationFn: async (confirmation: MealSubmissionReview | undefined) => {
      if (platform.data?.orders_open === false) throw new Error(platform.data.closed_message);
      if (!user || !isCurrentSessionUser(user.id)) throw new Error("請先登入");
      const sameIdentity = captureSessionIdentityGuard();
      if (!quantityReady) throw new Error("會餐至少需訂 2 份");
      if (!contactEmailReady) throw new Error("請輸入有效的聯絡 Email");
      if (!contactPhoneReady) throw new Error("請輸入完整手機號碼或含區碼的市話");
      const submittedAt = Date.now();
      if (!event.data || !mealOrderingOpen(event.data, submittedAt, mealBookingType)) {
        throw new Error(event.data?.new_orders_blocked_reason || "請在本場次的預訂開放時間內送出");
      }
      if (!validPickupTime(pickupAt, event.data, submittedAt, mealBookingType)) throw new Error("請重新選擇可預訂的半小時取餐時段");
      if (!pickupLocationReady) throw new Error("請選擇可用的取餐點");
      const input = {
        ...(business ? { mealBookingType: "business" as const, invoiceHandling: "pickup_day" as const } : {}),
        eventId,
        couponCode,
        expectedTotal: quote.data?.amount_total,
        pickupAt: taipeiInputToIso(pickupAt),
        pickupLocationId,
        needsUtensils,
        items,
        contactEmail: contactEmail.trim(),
        invoicePreference,
        paymentMethod: "online" as const,
        kitchenNote: kitchenNote.trim(),
        contactPhone: contactPhone.replace(/\s/g, ""),
      };
      const [storageKey, ...legacyKeys] = await Promise.all([
        mealSubmissionStorageKey(user.id, input),
        ...(!business ? [mealSubmissionStorageKey(user.id, { ...input, invoiceHandling: "pickup_day" })] : []),
      ]);
      const pendingLegacy = legacyKeys.flatMap(key => {
        const id = sessionStore.getItem(key);
        return id ? [{ key, id }] : [];
      });
      const legacyId = pendingLegacy[0]?.id;
      const currentId = sessionStore.getItem(storageKey);
      if (!sameIdentity()) throw new Error("請重新登入後確認便當訂單");
      const reviewUnmatchedSubmission = () => {
        const unmatched = unmatchedMealSubmissions([storageKey, ...legacyKeys]);
        const needsReview = confirmation
          ? confirmation.userId !== user.id || confirmation.storageKey !== storageKey || confirmation.unmatched !== unmatched
          : Boolean(unmatched);
        if (needsReview) {
          return { kind: "submission_review" as const, review: { userId: user.id, storageKey, unmatched }, confirmAgain: Boolean(confirmation) };
        }
      };
      if (!business && (confirmation || (!currentId && !legacyId))) {
        const review = reviewUnmatchedSubmission();
        if (review) return review;
        if (confirmation && (currentId || legacyId)) return { kind: "review" as const };
      }
      if (legacyId && (pendingLegacy.some(({ id }) => id !== legacyId) || (currentId && currentId !== legacyId))) {
        return { kind: "review" as const };
      }
      const resolvedKeys = [storageKey, ...pendingLegacy.map(({ key }) => key)];
      const findLegacyOrder = (id: string) => apiFetch<{ order_id: string | null }>(
        `/v1/meal-events/${eventId}/order-submissions/${encodeURIComponent(id)}`,
      );
      if (legacyId) {
        try {
          const existing = await findLegacyOrder(legacyId);
          if (existing.order_id) {
            return { kind: "reserved" as const, orderId: existing.order_id, storageKeys: resolvedKeys, submissionId: legacyId };
          }
        } catch (error) {
          // During a staggered release the old API has no read-only lookup route.
          if (error instanceof ApiError && error.status === 404) return { kind: "review" as const };
          throw error;
        }
      }
      await saveInvoiceCarrierIfRequested(invoicePreference, user.saved_mobile_barcode, saveMobileBarcode);
      if (!sameIdentity()) throw new Error("請重新登入後確認便當訂單");
      const latestId = sessionStore.getItem(storageKey);
      if (latestId && (confirmation || (legacyId && latestId !== legacyId))) return { kind: "review" as const };
      if (legacyKeys.some(key => {
        const id = sessionStore.getItem(key);
        return id && id !== legacyId;
      })) return { kind: "review" as const };
      if (!business && !latestId && !legacyId) {
        const review = reviewUnmatchedSubmission();
        if (review) return review;
      }
      // Reuse the old ID even after an empty lookup. An earlier request may still be committing.
      const previousId = latestId || legacyId;
      const id = previousId || crypto.randomUUID();
      rememberMealSubmission(sessionStore, storageKey, id, !previousId);
      try {
        return { ...await beginMealCheckout({ ...input, clientRequestId: id }), storageKeys: resolvedKeys, submissionId: id };
      } catch (error) {
        if (legacyId && error instanceof ApiError && error.status === 409 && sameIdentity()) {
          const existing = await findLegacyOrder(legacyId);
          if (existing.order_id) return { kind: "reserved" as const, orderId: existing.order_id, storageKeys: resolvedKeys, submissionId: legacyId };
        }
        throw error;
      }
    },
    onSuccess: (outcome, _variables, sameIdentity) => {
      if (!sameIdentity?.() || outcome.kind === "submission_review") return;
      if (outcome.kind === "review") {
        window.location.assign("/meal-orders");
        return;
      }
      for (const key of outcome.storageKeys) forgetMealSubmission(sessionStore, key, outcome.submissionId);
      clearMealDraft(eventId);
      setDraft(emptyMealDraft());
      setDraftAdjusted(false);
      if (outcome.kind === "redirect") return openOrderHandoff(outcome.url, outcome.orderId);
      if (outcome.kind === "reserved") {
        window.location.assign(`/meal-orders?${new URLSearchParams({ order_id: outcome.orderId })}`);
        return;
      }
      if (user) sessionStore.setItem(`slf_meal_payment_issue:${user.id}:${outcome.orderId}`, outcome.message.slice(0, 500));
      window.location.assign(`/meal-orders?${new URLSearchParams({ order_id: outcome.orderId })}`);
    },
    onSettled: () => { checkoutInFlight.current = false; },
  });
  const submissionReview = !business && checkout.data?.kind === "submission_review" && checkout.data.review.userId === user?.id
    ? checkout.data : undefined;

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
  const today = taipeiDate(now);
  const tomorrow = shiftMealDate(today, 1);
  const serviceDate = mealServiceDate(mealEvent);
  const dateLabel = serviceDate === today ? "（今天）" : serviceDate === tomorrow ? "（明天）" : "";
  const deadline = !businessOnly && mealEvent.same_day_preparation_minutes == null
    ? mealEvent.booking_ends_at || mealEvent.ordering_ends_at
    : new Date(pickupReady
      ? mealPickupDeadline(mealEvent, Date.parse(taipeiInputToIso(pickupAt)), mealBookingType)
      : mealBookingDeadline(mealEvent, mealBookingType)).toISOString();
  const bookingStatus = mealBookingStatus(mealEvent, now, mealBookingType);
  const bookingBlockedReason = orderingPaused && bookingStatus === "已截止" ? "本場次預訂已截止。"
    : mealEvent.new_orders_blocked_reason
      || (orderingPaused ? "此場次暫停接受新訂單" : businessClosed ? "會餐預訂已截止。" : "本場次目前不在預訂開放時間內。");
  const canOrder =
    orderingOpen &&
    pickupReady &&
    pickupLocationReady &&
    items.length > 0 &&
    quantityReady &&
    selectionsReady && !lineLimitExceeded &&
    invoiceReady && contactEmailReady && contactPhoneReady;
  const checkoutDisabled = checkout.isPending || quote.isFetching || quote.isError
    || (business ? !orderingOpen : !canOrder || !quote.data);
  const selectedMeals = selectionLines.map(({ offering, quantity, optionIds }) => {
    const options = offering.option_groups.filter(group => group.is_active).flatMap(group => group.options
      .filter(option => option.is_active && optionIds.includes(option.id))
      .map(option => ({ label: `${group.name}：${option.name}`, price: option.price_delta })));
    return {
      meal_name: offering.meal_name,
      quantity,
      options: options.map(option => option.label),
      subtotal: (offering.price + options.reduce((sum, option) => sum + option.price, 0)) * quantity,
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

  const changeOption = (offeringId: string, group: MealOptionGroup, optionId: string, portionIndex?: number, single = false) => {
    setDraftAdjusted(false);
    setDraft((current) => {
      const portions = current.portionSelections?.[offeringId];
      const selected = (portionIndex === undefined ? current.selections[offeringId] : portions?.[portionIndex])?.[group.id] || [];
      const next = single ? (optionId ? [optionId] : []) : selected.includes(optionId)
        ? selected.filter((id) => id !== optionId)
        : group.max_selections === 1
          ? [optionId]
          : selected.length < group.max_selections
            ? [...selected, optionId]
            : selected;
      if (portionIndex !== undefined && portions) {
        return { ...current, portionSelections: { ...current.portionSelections,
          [offeringId]: portions.map((selection, index) => index === portionIndex ? { ...selection, [group.id]: next } : selection),
        } };
      }
      return {
        ...current,
        selections: {
          ...current.selections,
          [offeringId]: { ...current.selections[offeringId], [group.id]: next },
        },
      };
    });
  };
  const togglePortions = (offeringId: string, quantity: number) => setDraft(current => {
    const portions = { ...current.portionSelections };
    if (portions[offeringId]) delete portions[offeringId];
    else portions[offeringId] = Array.from({ length: quantity }, () => current.selections[offeringId] || {});
    return { ...current, portionSelections: Object.keys(portions).length ? portions : undefined };
  });
  const attemptCheckout = (confirmation?: MealSubmissionReview) => {
    setSubmitted(true);
    if (canOrder && quote.data && !checkoutDisabled && !checkoutInFlight.current) {
      checkoutInFlight.current = true;
      checkout.mutate(confirmation);
    }
  };

  return (
    <section className="offer-page meal-offer-page">
      <Link className="text-link" to="/shop" search={{ tab: "meals" }}><ArrowLeft size={17} />回到便當預購</Link>
      <div className="offer-hero meal-offer-hero">
        <div className="offer-copy">
          <p className="eyebrow">MEAL PREORDER / 便當預訂</p>
          <h1>選餐點與份數</h1>
          <div className="meal-booking-meta"><span className="meal-event-status">{bookingStatus}</span><p className="meal-booking-deadline">預訂截止：{formatMealDateTime(deadline)}</p></div>
        </div>
      </div>

      {businessOnly ? <p className="meal-booking-type">會餐（統編報帳）</p> : <fieldset className="meal-booking-type" disabled={checkout.isPending}>
        <legend>訂餐方式</legend>
        {([['standard', '一般餐點'], ['business', '會餐（統編報帳）']] as const).map(([value, label]) => <label key={value} className={mealBookingType === value ? "selected" : ""}><input type="radio" name="meal-booking-type" value={value} checked={mealBookingType === value} onChange={() => { setMealBookingType(value); setEditingInvoice(false); setSubmitted(false); setTouched({}); }} />{label}</label>)}
      </fieldset>}

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
                    <div className="quantity-control" onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setTouched(current => ({ ...current, quantity: true })); }}>
                      <button type="button" disabled={quantity === 0} onClick={() => changeQuantity(offering.id, quantity - 1, offering.available_quantity)} aria-label={`減少${offering.meal_name}`}><Minus size={20} /></button>
                      <span aria-label={`${offering.meal_name} ${quantity} 份`}>{quantity}</span>
                      <button type="button" disabled={quantity >= Math.min(offering.available_quantity, 99)} onClick={() => changeQuantity(offering.id, quantity + 1, offering.available_quantity)} aria-label={`增加${offering.meal_name}`}><Plus size={20} /></button>
                    </div>
                  </div>
                  {quantity > 0 && offering.option_groups.some((group) => group.is_active) && (
                    <div className="meal-option-groups">
                      {business && quantity > 1 && <label className="check-field"><input type="checkbox"
                        checked={Boolean(currentDraft.portionSelections?.[offering.id])}
                        onChange={() => togglePortions(offering.id, quantity)} />每份分開選</label>}
                      {(business ? currentDraft.portionSelections?.[offering.id] : undefined)
                        ? currentDraft.portionSelections?.[offering.id].map((selection, index) => <fieldset key={index} className="meal-option-group">
                          <legend>第 {index + 1} 份</legend>
                          {offering.option_groups.filter(group => group.is_active).map(group => {
                            const selected = selection[group.id] || [];
                            const choices = group.options.filter(option => option.is_active);
                            return <div className="field" key={group.id}><span>{group.name}{group.min_selections > 0 && `（${selectionRule(group)}）`}</span>
                              {group.max_selections === 1 ? <select aria-label={`${offering.meal_name} 第 ${index + 1} 份 ${group.name}`} value={selected[0] || ""}
                                onChange={event => changeOption(offering.id, group, event.target.value, index, true)}>
                                <option value="">請選擇</option>
                                {choices.map(option => <option key={option.id} value={option.id}>{option.name}{option.price_delta !== 0 && `（${option.price_delta > 0 ? "+" : "−"}${formatMoney(Math.abs(option.price_delta))}）`}</option>)}
                              </select> : <div className="meal-option-list">
                                {choices.map(option => <button className={selected.includes(option.id) ? "selected" : ""} key={option.id}
                                  type="button" aria-pressed={selected.includes(option.id)}
                                  onClick={() => changeOption(offering.id, group, option.id, index)}>{option.name}{option.price_delta !== 0 && `（${option.price_delta > 0 ? "+" : "−"}${formatMoney(Math.abs(option.price_delta))}）`}</button>)}
                              </div>}
                            </div>;
                          })}
                        </fieldset>)
                        : offering.option_groups.filter((group) => group.is_active).map((group) => {
                        const selected = selections[offering.id]?.[group.id] || [];
                        const valid = selected.length >= group.min_selections && selected.length <= group.max_selections;
                        const activeOptions = group.options.filter((option) => option.is_active);
                        const compactChoice = group.max_selections === 1 && activeOptions.length >= 3 && activeOptions.every((option) => !option.image_url);
                        const fieldId = `meal-option-${offering.id}-${group.id}`;
                        return (
                          <fieldset className="meal-option-group" key={group.id} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setTouched(current => ({ ...current, [fieldId]: true })); }}>
                            <legend id={`${fieldId}-label`}><span>{group.name}</span><small>{selectionRule(group)}</small></legend>
                            {compactChoice ? (
                              <div className="field">
                                <select
                                  aria-label={`${offering.meal_name} ${group.name}`}
                                  aria-describedby={!valid && (submitted || touched[fieldId]) ? `${fieldId}-error` : undefined}
                                  aria-invalid={!valid && Boolean(submitted || touched[fieldId])}
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
                                  onClick={() => changeOption(offering.id, group, option.id)}
                                  type="button"
                                >
                                  <MealPhoto className="meal-option-photo" src={option.image_url} alt={`${option.name}照片`} />
                                  <span className="meal-option-label"><span>{option.name}</span>{option.price_delta > 0 && <small>+{formatMoney(option.price_delta)}</small>}</span>
                                </button>
                              ))}
                            </div>}
                            {!valid && (submitted || touched[fieldId]) && <p id={`${fieldId}-error`} className="meal-option-error">請完成此項選擇</p>}
                          </fieldset>
                        );
                      })}
                    </div>
                  )}
                </article>
              );
            })}
          </div>
          {business && <p className="meal-business-payment">付款方式：線上付款 · 取餐當日付款</p>}
          {activeOfferings.length === 0 && <DataState title="菜單待公告" detail="管理員公布餐點後即可選餐，請稍後再查看。" />}
        </div>

        <aside className="offer-summary meal-summary" id="meal-order-summary" tabIndex={-1} aria-labelledby="meal-summary-heading">
          <BowlFood size={29} weight="light" />
          <h2 id="meal-summary-heading">確認訂單明細</h2>
          {quote.isFetching && <LoadingLines count={2} />}
          {quote.isError && <p className="form-error">{quote.error.message}</p>}
          {lineLimitExceeded && <p className="form-error" role="alert">每筆訂單最多 20 種餐點與選項組合。</p>}
          {items.length > 0 && !selectionsReady && <p className="meal-selection-notice">請完成餐點的必選項目。</p>}
          {!pickupAt && !orderingPaused && <p className="meal-selection-notice">請選擇預計取餐時間。</p>}
          {!orderingOpen && <p className="meal-selection-notice">{bookingBlockedReason}{orderingPaused && <> · <Link className="text-link" to="/meal-orders">查看我的便當訂單</Link></>}</p>}
          <dl>
            {confirmedQuote ? confirmedQuote.items.map((item, index) => (
              <div className="meal-quote-line" key={`${item.offering_id}-${index}`}>
                <dt><strong>{item.meal_name} × {item.quantity}</strong><small>{taxTypeLabel(item.tax_type)}</small>{item.selections.length > 0 && <small>{item.selections.map((selection) => `${selection.group_name}：${selection.option_name}`).join(" · ")}</small>}</dt>
                <dd>{formatMoney(item.subtotal)}</dd>
              </div>
            )) : selectedMeals.map((item, index) => (
              <div className="meal-quote-line" key={`${item.meal_name}-${index}`}>
                <dt><strong>{item.meal_name} × {item.quantity}</strong>{item.options.length > 0 && <small>{item.options.join(" · ")}</small>}</dt>
                <dd>{formatMoney(item.subtotal)}</dd>
              </div>
            ))}
            {confirmedQuote?.promotion && <div><dt>{confirmedQuote.promotion.name}</dt><dd>已折抵 {formatMoney(confirmedQuote.promotion.discount)}</dd></div>}
            {confirmedQuote && <InvoiceTaxSummary items={confirmedQuote.items} memberTaxExempt={confirmedQuote.member_tax_exempt} taxAmount={confirmedQuote.tax_amount} />}
          </dl>
          <section className="meal-pickup-settings" aria-labelledby="meal-pickup-heading">
            <div className="meal-pickup-heading"><h3 id="meal-pickup-heading">取餐安排</h3><Link className="text-link" to="/shop" search={{ tab: "meals" }}>更換取餐日期</Link></div>
            <p className="meal-pickup-date"><strong>{serviceDate.replaceAll("-", "/")}{dateLabel}</strong></p>
            <label className="field"><span>預計取餐時間（台灣時間）</span>
              <select value={pickupReady ? pickupAt : ""} required disabled={!orderingOpen || checkout.isPending || !pickupOptions.length} onChange={(event) => { const value = event.target.value; setDraftAdjusted(false); setDraft((current) => ({ ...current, pickupAt: value })); void navigate({ search: { pickup_at: value }, replace: true }); }}>
                <option value="" disabled>請選擇取餐時間</option>
                {pickupOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            </label>
            {!pickupOptions.length && !orderingPaused && <p className="meal-selection-notice">目前沒有可預訂的取餐時段。請回到便當預訂選擇可預訂的供餐日期。</p>}
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
          <label className="field"><span>聯絡 Email</span><input type="email" required autoComplete="email" maxLength={320} value={contactEmail} aria-invalid={contactEmailError} aria-describedby={contactEmailError ? "meal-contact-email-error" : undefined} onBlur={() => {
            setTouched(current => ({ ...current, email: true }));
            if (contactEmailReady) {
              const fillInvoiceEmail = (preference: InvoicePreference) => preference.buyerEmail ? preference : { ...preference, buyerEmail: contactEmail.trim() };
              setStandardPreference(fillInvoiceEmail);
              setBusinessPreference(fillInvoiceEmail);
            }
          }} onChange={event => setEmailInput({ userId: user?.id || "", value: event.target.value })} /></label>
          {contactEmailError && <p id="meal-contact-email-error" className="form-error" role="alert">請輸入有效的聯絡 Email</p>}
          <label className="field"><span>聯絡電話</span><input type="tel" required autoComplete="tel" maxLength={40} value={contactPhone} aria-invalid={contactPhoneError} aria-describedby={contactPhoneError ? "meal-contact-phone-error" : undefined} onBlur={() => setTouched(current => ({ ...current, phone: true }))} onChange={event => setPhoneInput({ userId: user?.id || "", value: event.target.value })} /></label>
          {contactPhoneError && <p id="meal-contact-phone-error" className="form-error" role="alert">請輸入完整手機號碼或含區碼的市話</p>}
          <div className="meal-checkout-settings">
            {business && <p className="meal-business-invoice">發票於取餐當日開立</p>}
            <section className="meal-invoice-settings" aria-label="電子發票設定">
              <div className="meal-invoice-heading"><h3>電子發票</h3>
                {invoicePreferenceIsValid(invoicePreference) && <button type="button" className="text-link" aria-expanded={editingInvoice} aria-controls="meal-invoice-fields" onClick={() => setEditingInvoice(!editingInvoice)}>{editingInvoice ? "完成發票設定" : "修改發票設定"}</button>}
              </div>
              <div id="meal-invoice-fields" hidden={!editingInvoice && invoicePreferenceIsValid(invoicePreference)}><InvoicePreferenceFields key={mealBookingType} lockBuyerType={business} deferErrors={business} submitted={submitted} onChange={(value) => { setEditingInvoice(true); setInvoicePreference(value); }} sectionNumber="02" showHeading={false} value={invoicePreference} /></div>
              {!editingInvoice && invoicePreferenceIsValid(invoicePreference) && <dl className="meal-invoice-summary"><InvoicePreferenceSummary value={invoicePreference} typeLabel="發票形式" /></dl>}
            </section>
          </div>
          <label className="field"><span>餐點備註（選填）</span><textarea maxLength={500} value={kitchenNote} onChange={event => setKitchenNote(event.target.value)} /></label>
          <div className="meal-payment-action">
          <dl className="meal-payment-total"><div className="offer-total"><dt>{totalLabel}</dt><dd>{totalDisplay}</dd></div></dl>
          {!user ? (
            <button className="button button-primary full-width" type="button" disabled={orderingPaused || ((businessOnly || mealEvent.same_day_preparation_minutes != null) && (!orderingOpen || !pickupOptions.length))} onClick={openLogin}>登入後預訂</button>
          ) : submissionReview ? (
            <>
              <p className="meal-selection-notice" role="status">
                {submissionReview.confirmAgain ? "請再確認是否另下一筆。" : "先前送出的便當訂單可能已成立。"}{" "}
                <Link className="text-link" to="/meal-orders" target="_blank" rel="noopener">查看便當訂單</Link>
              </p>
              <button className="button button-system full-width" type="button" disabled={checkoutDisabled} onClick={() => attemptCheckout(submissionReview.review)}>另下一筆</button>
            </>
          ) : (
            <button className="button button-system full-width" type="button" disabled={checkoutDisabled} onClick={() => attemptCheckout()}>
              {checkout.isPending ? "建立餐點訂單中…" : business ? "送出會餐訂單（取餐當日付款）" : "預訂並前往線上付款"}
            </button>
          )}
          {business && !quantityReady && (submitted || touched.quantity) && <p className="form-error" role="alert">會餐至少 2 份</p>}
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
