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
import { useMemo, useState } from "react";

import { DataState, LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import { apiFetch, formatDate, formatMoney, mealEventStatusLabel, resolveAsset } from "../lib/api";
import { beginMealCheckout } from "../lib/commerce";
import type { MealEvent } from "../lib/types";

interface MealQuote {
  amount_total: number;
  items: Array<{
    offering_id: string;
    meal_name: string;
    quantity: number;
    unit_price: number;
    subtotal: number;
  }>;
  pickup: { location: string; starts_at: string; ends_at: string };
}

export function MealEventPage() {
  const { eventId } = useParams({ from: "/meals/$eventId" });
  const { user, openLogin } = useAuth();
  const [quantities, setQuantities] = useState<Record<string, number>>({});
  const event = useQuery({
    queryKey: ["meal-event", eventId],
    queryFn: () => apiFetch<MealEvent>(`/v1/meal-events/${eventId}`),
  });
  const items = useMemo(
    () => Object.entries(quantities).filter(([, quantity]) => quantity > 0).map(([offering_id, quantity]) => ({ offering_id, quantity })),
    [quantities],
  );
  const itemKey = items.map((item) => `${item.offering_id}:${item.quantity}`).join("|");
  const quote = useQuery({
    queryKey: ["meal-quote", eventId, itemKey],
    queryFn: () => apiFetch<MealQuote>(`/v1/meal-events/${eventId}/quote`, {
      method: "POST",
      body: JSON.stringify({ items, contact_email: user?.email || "preview@example.com", invoice_carrier_type: "ecpay" }),
    }),
    enabled: items.length > 0,
  });
  const checkout = useMutation({
    mutationFn: () => {
      if (!user) throw new Error("請先登入");
      return beginMealCheckout({ eventId, items, contactEmail: user.email });
    },
    onSuccess: (outcome) => {
      if (outcome.kind === "redirect") return window.location.assign(outcome.url);
      const search = new URLSearchParams({ order_id: outcome.orderId, result: "resume", message: outcome.message });
      window.location.assign(`/orders?${search.toString()}`);
    },
  });

  if (event.isPending) return <section className="offer-page"><LoadingLines count={4} /></section>;
  if (event.isError || !event.data) {
    return <section className="offer-page"><DataState kind="error" title="餐期資料無法讀取" detail={event.error?.message || "找不到這個餐期"} /></section>;
  }

  const mealEvent = event.data;
  const firstImage = mealEvent.offerings.find((offering) => offering.image_url)?.image_url;
  const canOrder = mealEvent.status === "published" && items.length > 0;

  const changeQuantity = (offeringId: string, next: number, maximum: number) => {
    setQuantities((current) => ({ ...current, [offeringId]: Math.max(0, Math.min(next, maximum, 99)) }));
  };

  return (
    <section className="offer-page meal-offer-page">
      <Link className="text-link" to="/shop"><ArrowLeft size={17} />回到便當預購</Link>
      <div className="offer-hero meal-offer-hero">
        <div className="offer-image"><img src={resolveAsset(firstImage || "/assets/meals/taiwanese-lunchbox.png")} alt={mealEvent.title} /><span>{mealEventStatusLabel(mealEvent.status)}</span></div>
        <div className="offer-copy">
          <p className="eyebrow">MEALS ON SCHEDULE</p>
          <h1>{mealEvent.title}</h1>
          <p>依場次預訂、綠界付款，完成後使用取餐碼於指定時間領取。</p>
          <dl>
            <div><dt>取餐地點</dt><dd>{mealEvent.location}</dd></div>
            <div><dt>預訂截止</dt><dd>{formatDate(mealEvent.ordering_ends_at)}</dd></div>
            <div><dt>開始取餐</dt><dd>{formatDate(mealEvent.pickup_starts_at)}</dd></div>
          </dl>
        </div>
      </div>

      <div className="offer-checkout meal-checkout">
        <div>
          <div className="checkout-step-title"><span>01</span><div><small>MEAL SELECTION</small><h2>選擇餐點</h2></div></div>
          <div className="meal-offering-list">
            {mealEvent.offerings.filter((offering) => offering.is_active).map((offering) => {
              const quantity = quantities[offering.id] || 0;
              return (
                <article key={offering.id}>
                  <img src={resolveAsset(offering.image_url || "/assets/meals/taiwanese-lunchbox.png")} alt={offering.meal_name} />
                  <div><small>剩餘 {offering.available_quantity} 份</small><h3>{offering.meal_name}</h3><p>{offering.description || "本餐點內容由合作社廚房維護。"}</p><strong>{formatMoney(offering.price)}</strong></div>
                  <div className="quantity-control">
                    <button type="button" onClick={() => changeQuantity(offering.id, quantity - 1, offering.available_quantity)} aria-label={`減少${offering.meal_name}`}><Minus size={15} /></button>
                    <span>{quantity}</span>
                    <button type="button" onClick={() => changeQuantity(offering.id, quantity + 1, offering.available_quantity)} aria-label={`增加${offering.meal_name}`}><Plus size={15} /></button>
                  </div>
                </article>
              );
            })}
          </div>
        </div>

        <aside className="offer-summary meal-summary">
          <BowlFood size={29} weight="light" />
          <p className="eyebrow">MEAL PRE-ORDER</p>
          <h2>餐點摘要</h2>
          <div className="pickup-window"><MapPin size={19} /><div><strong>{mealEvent.location}</strong><small>{formatDate(mealEvent.pickup_starts_at)} 至 {formatDate(mealEvent.pickup_ends_at)}</small></div></div>
          {quote.isPending && <LoadingLines count={2} />}
          {quote.isError && <p className="form-error">{quote.error.message}</p>}
          <dl>
            {quote.data?.items.map((item) => <div key={item.offering_id}><dt>{item.meal_name} × {item.quantity}</dt><dd>{formatMoney(item.subtotal)}</dd></div>)}
            <div className="offer-total"><dt>合計</dt><dd>{formatMoney(quote.data?.amount_total || 0)}</dd></div>
          </dl>
          {!user ? (
            <button className="button button-primary full-width" type="button" onClick={openLogin}>登入後預訂</button>
          ) : (
            <button className="button button-system full-width" type="button" disabled={!canOrder || !quote.data || checkout.isPending} onClick={() => checkout.mutate()}>
              {checkout.isPending ? "建立餐點訂單中…" : "預訂並前往綠界付款"}
            </button>
          )}
          {checkout.isError && <p className="form-error">{checkout.error.message}</p>}
          <p className="pickup-note"><CalendarBlank size={16} />付款完成後，取餐碼會保存在訂單中心。</p>
        </aside>
      </div>
    </section>
  );
}
