import {
  ArrowLeft,
  BowlFood,
  CalendarBlank,
  MapPin,
  Minus,
  Plus,
} from "@phosphor-icons/react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";

import { DataState, LoadingLines } from "../components/Shared";
import {
  defaultInvoicePreference,
  InvoicePreferenceFields,
  InvoicePreferenceSummary,
  invoicePreferenceIsValid,
} from "../components/InvoicePreferenceFields";
import { useAuth } from "../context/AuthContext";
import { openOrderHandoff } from "../lib/checkout-navigation";
import { apiFetch, formatDateTime, formatMoney, resolveAsset } from "../lib/api";
import { beginMealCheckout, invoicePreferencePayload } from "../lib/commerce";
import { mealEventStatusLabel } from "../lib/labels";
import type {
  MealEvent,
  MealOptionGroup,
  MealOptionSelection,
} from "../lib/types";

interface MealQuote {
  amount_total: number;
  items: Array<{
    offering_id: string;
    meal_name: string;
    quantity: number;
    base_price: number;
    option_price: number;
    unit_price: number;
    subtotal: number;
    selections: MealOptionSelection[];
  }>;
  pickup: { location: string; starts_at: string; ends_at: string };
}

type SelectionState = Record<string, Record<string, string[]>>;

export function MealEventPage() {
  const { eventId } = useParams({ from: "/meals/$eventId" });
  const { user, openLogin } = useAuth();
  const [quantities, setQuantities] = useState<Record<string, number>>({});
  const [selections, setSelections] = useState<SelectionState>({});
  const [invoicePreference, setInvoicePreference] = useState(
    () => defaultInvoicePreference(user?.email || ""),
  );
  const event = useQuery({
    queryKey: ["meal-event", eventId],
    queryFn: () => apiFetch<MealEvent>(`/v1/meal-events/${eventId}`),
  });
  const activeOfferings = event.data?.offerings.filter((item) => item.is_active) || [];
  const items = useMemo(
    () => activeOfferings
      .filter((offering) => (quantities[offering.id] || 0) > 0)
      .map((offering) => ({
        offering_id: offering.id,
        quantity: quantities[offering.id],
        option_ids: offering.option_groups.flatMap(
          (group) => selections[offering.id]?.[group.id] || [],
        ),
      })),
    [activeOfferings, quantities, selections],
  );
  const selectionsReady = items.every((item) => {
    const offering = activeOfferings.find((candidate) => candidate.id === item.offering_id);
    return offering?.option_groups.every((group) => {
      const count = selections[offering.id]?.[group.id]?.length || 0;
      return count >= group.min_selections && count <= group.max_selections;
    });
  });
  const itemKey = items
    .map((item) => `${item.offering_id}:${item.quantity}:${[...item.option_ids].sort().join(",")}`)
    .join("|");
  const quote = useQuery({
    queryKey: ["meal-quote", eventId, itemKey, invoicePreference],
    queryFn: () => apiFetch<MealQuote>(`/v1/meal-events/${eventId}/quote`, {
      method: "POST",
      body: JSON.stringify({
        items,
        contact_email: user?.email || "preview@example.com",
        ...invoicePreferencePayload(invoicePreference),
      }),
    }),
    enabled:
      items.length > 0 &&
      selectionsReady &&
      invoicePreferenceIsValid(invoicePreference),
  });
  const checkout = useMutation({
    mutationFn: () => {
      if (!user) throw new Error("請先登入");
      return beginMealCheckout({
        eventId,
        items,
        contactEmail: user.email,
        invoicePreference,
      });
    },
    onSuccess: (outcome) => {
      if (outcome.kind === "redirect") return openOrderHandoff(outcome.url, outcome.orderId);
      const search = new URLSearchParams({ order_id: outcome.orderId, result: "resume", message: outcome.message });
      window.location.assign(`/orders?${search.toString()}`);
    },
  });

  useEffect(() => {
    if (!user?.email) return;
    setInvoicePreference((current) =>
      current.buyerEmail ? current : { ...current, buyerEmail: user.email },
    );
  }, [user?.email]);

  if (event.isPending) return <section className="offer-page"><LoadingLines count={4} /></section>;
  if (event.isError || !event.data) {
    return <section className="offer-page"><DataState kind="error" title="餐期資料無法讀取" detail={event.error?.message || "找不到這個餐期"} /></section>;
  }

  const mealEvent = event.data;
  const firstImage = activeOfferings.find((offering) => offering.image_url)?.image_url;
  const canOrder =
    mealEvent.status === "published" &&
    items.length > 0 &&
    selectionsReady &&
    invoicePreferenceIsValid(invoicePreference);

  const changeQuantity = (offeringId: string, next: number, maximum: number) => {
    setQuantities((current) => ({ ...current, [offeringId]: Math.max(0, Math.min(next, maximum, 99)) }));
  };

  const toggleOption = (offeringId: string, group: MealOptionGroup, optionId: string) => {
    setSelections((current) => {
      const selected = current[offeringId]?.[group.id] || [];
      const next = selected.includes(optionId)
        ? selected.filter((id) => id !== optionId)
        : group.max_selections === 1
          ? [optionId]
          : selected.length < group.max_selections
            ? [...selected, optionId]
            : selected;
      return {
        ...current,
        [offeringId]: { ...current[offeringId], [group.id]: next },
      };
    });
  };

  return (
    <section className="offer-page meal-offer-page">
      <Link className="text-link" to="/shop"><ArrowLeft size={17} />回到便當預購</Link>
      <div className="offer-hero meal-offer-hero">
        <div className="offer-image"><img src={resolveAsset(firstImage || "/assets/meals/taiwanese-lunchbox.webp")} alt={mealEvent.title} /><span>{mealEventStatusLabel(mealEvent.status)}</span></div>
        <div className="offer-copy">
          <p className="eyebrow">MEALS ON SCHEDULE</p>
          <h1>{mealEvent.title}</h1>
          <p>選好餐點與客製內容，付款完成後即可取得現場取餐碼。</p>
          <dl>
            <div><dt>取餐地點</dt><dd>{mealEvent.location}</dd></div>
            <div><dt>預訂截止</dt><dd>{formatDateTime(mealEvent.ordering_ends_at)}</dd></div>
            <div><dt>開始取餐</dt><dd>{formatDateTime(mealEvent.pickup_starts_at)}</dd></div>
            <div><dt>取餐結束</dt><dd>{formatDateTime(mealEvent.pickup_ends_at)}</dd></div>
          </dl>
        </div>
      </div>

      <div className="offer-checkout meal-checkout">
        <div>
          <div className="checkout-step-title"><span>01</span><div><small>MEAL SELECTION</small><h2>選擇餐點</h2></div></div>
          <div className="meal-offering-list">
            {activeOfferings.map((offering) => {
              const quantity = quantities[offering.id] || 0;
              return (
                <article className={quantity > 0 ? "meal-offering selected" : "meal-offering"} key={offering.id}>
                  <div className="meal-offering-summary">
                    <img src={resolveAsset(offering.image_url || "/assets/meals/taiwanese-lunchbox.webp")} alt={offering.meal_name} />
                    <div className="meal-offering-copy"><small>剩餘 {offering.available_quantity} 份</small><h3>{offering.meal_name}</h3><p>{offering.description || "本餐點內容由合作社廚房維護。"}</p><strong>{formatMoney(offering.price)} 起</strong></div>
                    <div className="quantity-control">
                      <button type="button" onClick={() => changeQuantity(offering.id, quantity - 1, offering.available_quantity)} aria-label={`減少${offering.meal_name}`}><Minus size={15} /></button>
                      <span>{quantity}</span>
                      <button type="button" onClick={() => changeQuantity(offering.id, quantity + 1, offering.available_quantity)} aria-label={`增加${offering.meal_name}`}><Plus size={15} /></button>
                    </div>
                  </div>
                  {quantity > 0 && offering.option_groups.length > 0 && (
                    <div className="meal-option-groups">
                      {offering.option_groups.map((group) => {
                        const selected = selections[offering.id]?.[group.id] || [];
                        const valid = selected.length >= group.min_selections && selected.length <= group.max_selections;
                        return (
                          <fieldset className="meal-option-group" key={group.id}>
                            <legend><span>{group.name}</span><small>{selectionRule(group)}</small></legend>
                            <div className="meal-option-list">
                              {group.options.map((option) => (
                                <button
                                  aria-pressed={selected.includes(option.id)}
                                  className={selected.includes(option.id) ? "selected" : ""}
                                  key={option.id}
                                  onClick={() => toggleOption(offering.id, group, option.id)}
                                  type="button"
                                >
                                  <span>{option.name}</span>
                                  {option.price_delta > 0 && <small>+{formatMoney(option.price_delta)}</small>}
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
          <InvoicePreferenceFields
            onChange={setInvoicePreference}
            sectionNumber="02"
            value={invoicePreference}
          />
        </div>

        <aside className="offer-summary meal-summary">
          <BowlFood size={29} weight="light" />
          <p className="eyebrow">MEAL PRE-ORDER</p>
          <h2>餐點摘要</h2>
          <div className="pickup-window"><MapPin size={19} /><div><strong>{mealEvent.location}</strong><small>{formatDateTime(mealEvent.pickup_starts_at)} 至 {formatDateTime(mealEvent.pickup_ends_at)}</small></div></div>
          {quote.isPending && <LoadingLines count={2} />}
          {quote.isError && <p className="form-error">{quote.error.message}</p>}
          {items.length > 0 && !selectionsReady && <p className="meal-selection-notice">請完成餐點的必選項目。</p>}
          <dl>
            {quote.data?.items.map((item) => (
              <div className="meal-quote-line" key={item.offering_id}>
                <dt><strong>{item.meal_name} × {item.quantity}</strong>{item.selections.length > 0 && <small>{item.selections.map((selection) => `${selection.group_name}：${selection.option_name}`).join(" · ")}</small>}</dt>
                <dd>{formatMoney(item.subtotal)}</dd>
              </div>
            ))}
            <InvoicePreferenceSummary value={invoicePreference} />
            <div className="offer-total"><dt>合計</dt><dd>{formatMoney(quote.data?.amount_total || 0)}</dd></div>
          </dl>
          {!user ? (
            <button className="button button-primary full-width" type="button" onClick={openLogin}>登入後預訂</button>
          ) : (
            <button className="button button-system full-width" type="button" disabled={!canOrder || !quote.data || checkout.isPending} onClick={() => checkout.mutate()}>
              {checkout.isPending ? "建立餐點訂單中…" : "預訂並前往線上付款"}
            </button>
          )}
          {checkout.isError && <p className="form-error">{checkout.error.message}</p>}
          <p className="pickup-note"><CalendarBlank size={16} />付款完成後，取餐碼會保存在訂單中心。</p>
        </aside>
      </div>
    </section>
  );
}

function selectionRule(group: MealOptionGroup): string {
  if (group.min_selections === group.max_selections) return `必選 ${group.min_selections} 項`;
  if (group.min_selections === 0) return `最多選 ${group.max_selections} 項`;
  return `選 ${group.min_selections}–${group.max_selections} 項`;
}
