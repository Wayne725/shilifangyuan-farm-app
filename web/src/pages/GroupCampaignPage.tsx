import {
  ArrowLeft,
  Check,
  MapPin,
  Minus,
  Plus,
  Storefront,
  Truck,
  UsersThree,
} from "@phosphor-icons/react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import { DataState, LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import { apiFetch, formatDate, formatMoney, replaceBrokenAsset, resolveAsset } from "../lib/api";
import {
  beginGroupCheckout,
  shippingChannelLabels,
  temperatureLabels,
} from "../lib/commerce";
import type { GroupCampaign, PickupLocation, ShippingChannel } from "../lib/types";

interface GroupQuote {
  membership_type: string;
  quantity: number;
  unit_price: number;
  product_subtotal: number;
  shipping_fee: number;
  amount_total: number;
}

export function GroupCampaignPage() {
  const { campaignId } = useParams({ from: "/groups/$campaignId" });
  const { user, openLogin } = useAuth();
  const [quantity, setQuantity] = useState(1);
  const [method, setMethod] = useState<"pickup" | "shipping">("pickup");
  const [pickupLocationId, setPickupLocationId] = useState("");
  const [shippingChannel, setShippingChannel] = useState<ShippingChannel | null>(null);
  const [recipientName, setRecipientName] = useState("");
  const [recipientPhone, setRecipientPhone] = useState("");
  const [shippingAddress, setShippingAddress] = useState("");
  const campaign = useQuery({
    queryKey: ["group-campaign", campaignId],
    queryFn: () => apiFetch<GroupCampaign>(`/v1/group-campaigns/${campaignId}`),
  });
  const locations = useQuery({
    queryKey: ["pickup-locations"],
    queryFn: () => apiFetch<PickupLocation[]>("/v1/pickup-locations"),
  });
  const quote = useQuery({
    queryKey: ["group-quote", campaignId, quantity, method, shippingChannel, user?.id],
    queryFn: () => apiFetch<GroupQuote>(`/v1/group-campaigns/${campaignId}/quote`, {
      method: "POST",
      body: JSON.stringify({
        quantity,
        fulfillment_method: method === "shipping" ? "ecpay_logistics" : "cooperative_pickup",
        shipping_channel: method === "shipping" ? shippingChannel : null,
      }),
    }),
    enabled: Boolean(user && campaign.data && (method === "pickup" || shippingChannel)),
  });
  const checkout = useMutation({
    mutationFn: () => {
      if (!user || !campaign.data) throw new Error("請先登入");
      if (method === "shipping") {
        if (!shippingChannel || !campaign.data.shipping_temperature) throw new Error("請選擇配送通路");
        return beginGroupCheckout({
          campaignId,
          quantity,
          contactEmail: user.email,
          invoiceCarrierType: "ecpay",
          fulfillment: {
            kind: "shipping",
            channel: shippingChannel,
            temperature: campaign.data.shipping_temperature,
            recipientName,
            recipientPhone,
            shippingAddress,
          },
        });
      }
      return beginGroupCheckout({
        campaignId,
        quantity,
        contactEmail: user.email,
        invoiceCarrierType: "ecpay",
        fulfillment: { kind: "pickup", pickupLocationId },
      });
    },
    onSuccess: (outcome) => {
      if (outcome.kind === "redirect") return window.location.assign(outcome.url);
      const search = new URLSearchParams({ order_id: outcome.order.id, result: "resume", message: outcome.message });
      window.location.assign(`/orders?${search.toString()}`);
    },
  });

  useEffect(() => {
    if (!pickupLocationId && locations.data?.[0]) setPickupLocationId(locations.data[0].id);
  }, [locations.data, pickupLocationId]);

  useEffect(() => {
    if (!campaign.data) return;
    if (!shippingChannel || !campaign.data.allowed_shipping_channels.includes(shippingChannel)) {
      setShippingChannel(campaign.data.allowed_shipping_channels[0] || null);
    }
    setQuantity((value) => Math.max(1, Math.min(value, campaign.data.per_user_cap, campaign.data.available_quantity)));
  }, [campaign.data, shippingChannel]);

  if (campaign.isPending) return <section className="offer-page"><LoadingLines count={4} /></section>;
  if (campaign.isError || !campaign.data) {
    return <section className="offer-page"><DataState kind="error" title="團購資料無法讀取" detail={campaign.error?.message || "找不到這筆團購"} /></section>;
  }

  const item = campaign.data;
  const shippingReady = Boolean(shippingChannel && recipientName.trim() && recipientPhone.trim() && shippingAddress.trim());
  const submitReady = Boolean(quote.data && (method === "pickup" ? pickupLocationId : shippingReady));

  return (
    <section className="offer-page">
      <Link className="text-link" to="/shop"><ArrowLeft size={17} />回到共同團購</Link>
      <div className="offer-hero">
        <div className="offer-image">
          <img src={resolveAsset(item.image_url)} alt={item.title} onError={replaceBrokenAsset} />
          <span>{item.intake_status}</span>
        </div>
        <div className="offer-copy">
          <p className="eyebrow">BUYING TOGETHER</p>
          <h1>{item.title}</h1>
          <p>{item.description}</p>
          <dl>
            <div><dt>已付款</dt><dd>{item.paid_quantity}／{item.min_paid_quantity} 份成團</dd></div>
            <div><dt>剩餘供應</dt><dd>{item.available_quantity} 份</dd></div>
            <div><dt>截止時間</dt><dd>{formatDate(item.deadline)}</dd></div>
            <div><dt>預計取貨</dt><dd>{formatDate(item.estimated_pickup_start)}</dd></div>
          </dl>
        </div>
      </div>

      <div className="offer-checkout">
        <div>
          <div className="checkout-step-title"><span>01</span><div><small>QUANTITY</small><h2>選擇數量</h2></div></div>
          <div className="offer-quantity">
            <button type="button" onClick={() => setQuantity((value) => Math.max(1, value - 1))}><Minus size={16} /></button>
            <strong>{quantity}</strong>
            <button type="button" onClick={() => setQuantity((value) => Math.min(item.per_user_cap, item.available_quantity, value + 1))}><Plus size={16} /></button>
            <span>每人上限 {item.per_user_cap} 份</span>
          </div>

          <div className="checkout-step-title pickup-title"><span>02</span><div><small>FULFILLMENT</small><h2>取貨方式</h2></div></div>
          <div className="fulfillment-switch">
            <button type="button" className={method === "pickup" ? "selected" : ""} onClick={() => setMethod("pickup")}><Storefront size={22} /><span><strong>合作社取貨</strong><small>依預計取貨窗口領取</small></span><i><Check size={14} /></i></button>
            <button type="button" className={method === "shipping" ? "selected shipping" : "shipping"} onClick={() => setMethod("shipping")} disabled={!item.can_ship}><Truck size={22} /><span><strong>宅配／超商取貨</strong><small>{item.can_ship ? `${temperatureLabels[item.shipping_temperature || "ambient"]}配送` : "此團購未開放配送"}</small></span><i><Check size={14} /></i></button>
          </div>

          {method === "pickup" ? (
            <div className="pickup-options offer-pickup-options">
              {locations.data?.map((location) => (
                <label key={location.id} className={pickupLocationId === location.id ? "selected" : ""}>
                  <input type="radio" checked={pickupLocationId === location.id} onChange={() => setPickupLocationId(location.id)} />
                  <MapPin size={20} /><span><strong>{location.name}</strong><small>{location.address || location.instructions}</small></span><i><Check size={14} /></i>
                </label>
              ))}
            </div>
          ) : (
            <div className="shipping-form">
              <div className="shipping-channels">
                {item.allowed_shipping_channels.map((channel) => (
                  <label key={channel} className={shippingChannel === channel ? "selected" : ""}>
                    <input type="radio" checked={shippingChannel === channel} onChange={() => setShippingChannel(channel)} />
                    <span>{shippingChannelLabels[channel]}</span>{shippingChannel === channel && <Check size={14} />}
                  </label>
                ))}
              </div>
              <div className="recipient-grid">
                <label>收件人姓名<input value={recipientName} onChange={(event) => setRecipientName(event.target.value)} /></label>
                <label>聯絡電話<input value={recipientPhone} onChange={(event) => setRecipientPhone(event.target.value)} inputMode="tel" /></label>
                <label className="recipient-address">配送地址<input value={shippingAddress} onChange={(event) => setShippingAddress(event.target.value)} /></label>
              </div>
            </div>
          )}
        </div>

        <aside className="offer-summary">
          <UsersThree size={28} weight="light" />
          <p className="eyebrow">GROUP ORDER</p>
          <h2>團購摘要</h2>
          {!user ? (
            <button className="button button-primary full-width" type="button" onClick={openLogin}>登入後取得正式價格</button>
          ) : (
            <>
              {quote.isPending && <LoadingLines count={2} />}
              {quote.isError && <p className="form-error">{quote.error.message}</p>}
              <dl>
                <div><dt>社員單價</dt><dd>{formatMoney(quote.data?.unit_price || item.member_price)}</dd></div>
                <div><dt>商品小計</dt><dd>{formatMoney(quote.data?.product_subtotal || 0)}</dd></div>
                <div><dt>運費</dt><dd>{formatMoney(quote.data?.shipping_fee || 0)}</dd></div>
                <div className="offer-total"><dt>合計</dt><dd>{formatMoney(quote.data?.amount_total || 0)}</dd></div>
              </dl>
              {checkout.isError && <p className="form-error">{checkout.error.message}</p>}
              <button className="button button-system full-width" type="button" disabled={!submitReady || checkout.isPending || item.intake_status !== "open"} onClick={() => checkout.mutate()}>
                {checkout.isPending ? "建立團購訂單中…" : method === "shipping" ? "參加並選擇物流" : "參加並前往付款"}
              </button>
            </>
          )}
        </aside>
      </div>
    </section>
  );
}
