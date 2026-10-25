import {
  ArrowLeft,
  Minus,
  Plus,
  UsersThree,
} from "@phosphor-icons/react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import { DataState, LoadingLines } from "../components/Shared";
import { storefrontCapabilities, storefrontOrderBlockReason, useStorefrontStatus } from "../components/StorefrontStatus";
import { InvoiceTaxSummary } from "../components/InvoiceTaxSummary";
import {
  useInvoicePreference,
  InvoicePreferenceFields,
  InvoicePreferenceSummary,
  invoicePreferenceIsValid,
} from "../components/InvoicePreferenceFields";
import { useAuth } from "../context/AuthContext";
import { openOrderHandoff } from "../lib/checkout-navigation";
import { apiFetch, formatDate, formatMoney, replaceBrokenAsset, resolveAsset } from "../lib/api";
import {
  beginGroupCheckout,
  shippingChannelLabels,
  temperatureLabels,
} from "../lib/commerce";
import type { GroupCampaign, PickupLocation, ShippingChannel } from "../lib/types";

interface GroupQuote {
  membership_type: string;
  member_tax_exempt?: boolean;
  tax_amount?: number;
  tax_type?: "taxable" | "tax_exempt";
  quantity: number;
  unit_price: number;
  product_subtotal: number;
  shipping_fee: number;
  amount_total: number;
}

const logisticsEnabled = import.meta.env.VITE_LOGISTICS_ENABLED === "true";

export function GroupCampaignPage() {
  const { campaignId } = useParams({ from: "/groups/$campaignId" });
  const { user, openLogin, saveMobileBarcode } = useAuth();
  const platform = useStorefrontStatus();
  const shippingEnabled = logisticsEnabled && storefrontCapabilities(platform.data).shipping;
  const notificationEmail = user?.notification_email || user?.email || "";
  const [quantity, setQuantity] = useState(1);
  const [method, setMethod] = useState<"pickup" | "shipping">("pickup");
  const [pickupLocationId, setPickupLocationId] = useState("");
  const [shippingChannel, setShippingChannel] = useState<ShippingChannel | null>(null);
  const [recipientName, setRecipientName] = useState("");
  const [recipientPhone, setRecipientPhone] = useState("");
  const [shippingAddress, setShippingAddress] = useState("");
  const [invoicePreference, setInvoicePreference] = useInvoicePreference(notificationEmail);
  const orderBlockReason = storefrontOrderBlockReason(platform.data, "group")
    || (method === "shipping" && !shippingEnabled ? "目前未開放配送，請選擇合作社取貨" : "");
  const campaign = useQuery({
    queryKey: ["group-campaign", campaignId],
    queryFn: () => apiFetch<GroupCampaign>(`/v1/group-campaigns/${campaignId}`),
  });
  const locations = useQuery({
    queryKey: ["pickup-locations"],
    queryFn: () => apiFetch<PickupLocation[]>("/v1/pickup-locations"),
  });
  const selectedPickupLocation = locations.data?.find((location) => location.id === pickupLocationId);
  const quote = useQuery({
    queryKey: ["group-quote", campaignId, quantity, method, shippingChannel, user?.id, invoicePreference.buyerType],
    queryFn: () => apiFetch<GroupQuote>(`/v1/group-campaigns/${campaignId}/quote`, {
      method: "POST",
      body: JSON.stringify({
        quantity,
        invoice_buyer_type: invoicePreference.buyerType,
        fulfillment_method: method === "shipping" ? "ecpay_logistics" : "cooperative_pickup",
        shipping_channel: method === "shipping" ? shippingChannel : null,
      }),
    }),
    enabled: Boolean(user && campaign.data && !orderBlockReason && (method === "pickup" || shippingChannel)),
  });
  const checkout = useMutation({
    mutationFn: async () => {
      if (!user || !campaign.data) throw new Error("請先登入");
      if (orderBlockReason) throw new Error(orderBlockReason);
      if (invoicePreference.carrierType === "mobile_barcode") {
        await saveMobileBarcode(invoicePreference.carrierValue || "");
      }
      if (method === "shipping") {
        if (!shippingChannel || !campaign.data.shipping_temperature) throw new Error("請選擇配送通路");
        return beginGroupCheckout({
          campaignId,
          quantity,
          contactEmail: invoicePreference.buyerEmail.trim(),
          invoicePreference,
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
      if (!selectedPickupLocation || locations.isError) throw new Error("請選擇可用的取貨地點");
      return beginGroupCheckout({
        campaignId,
        quantity,
        contactEmail: invoicePreference.buyerEmail.trim(),
        invoicePreference,
        fulfillment: { kind: "pickup", pickupLocationId },
      });
    },
    onSuccess: (outcome) => {
      if (outcome.kind === "redirect") return openOrderHandoff(outcome.url, outcome.order.id);
      const search = new URLSearchParams({ order_id: outcome.order.id, result: "resume", message: outcome.message });
      window.location.assign(`/orders?${search.toString()}`);
    },
  });

  useEffect(() => {
    if (locations.data && !locations.data.some((location) => location.id === pickupLocationId)) {
      setPickupLocationId(locations.data[0]?.id || "");
    }
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
    return <section className="offer-page"><DataState kind="error" title="共同購買資料無法讀取" detail={campaign.error?.message || "找不到這筆共同購買"} /></section>;
  }

  const item = campaign.data;
  const shippingReady = Boolean(shippingChannel && recipientName.trim() && recipientPhone.trim() && shippingAddress.trim());
  const submitReady = Boolean(
    !orderBlockReason && quote.data && !quote.isFetching && !quote.isError &&
      invoicePreferenceIsValid(invoicePreference) &&
      (method === "pickup" ? selectedPickupLocation && !locations.isError : shippingReady),
  );

  return (
    <section className="offer-page">
      <Link className="text-link" to="/shop"><ArrowLeft size={17} />回到共同購買</Link>
      <div className="offer-hero">
        <div className="offer-image">
          <img src={resolveAsset(item.image_url)} alt={item.title} onError={replaceBrokenAsset} />
          <span>{item.intake_status}</span>
        </div>
        <div className="offer-copy">
          <p className="eyebrow">BUYING TOGETHER / 共同購買</p>
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

      {orderBlockReason && <p className="form-error" role="status">{orderBlockReason}</p>}
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
          <div className="checkout-fulfillment">
            <label className="field">
              取貨方式
              <select value={method} disabled={checkout.isPending} onChange={(event) => setMethod(event.target.value as "pickup" | "shipping")}>
                <option value="pickup">合作社取貨</option>
                {(shippingEnabled || method === "shipping") && <option value="shipping" disabled={!shippingEnabled || !item.can_ship}>宅配／超商取貨{!shippingEnabled ? "（目前未開放配送）" : !item.can_ship ? "（此共同購買未開放配送）" : `（${temperatureLabels[item.shipping_temperature || "ambient"]}）`}</option>}
              </select>
            </label>
            {method === "pickup" && <label className="field">
              取貨地點
              <select value={pickupLocationId} required disabled={checkout.isPending || locations.isPending || locations.isError || !locations.data?.length} onChange={(event) => setPickupLocationId(event.target.value)}>
                <option value="" disabled>{locations.isPending ? "載入取貨地點中…" : locations.isError ? "取貨點暫時無法使用" : !locations.data?.length ? "目前沒有可用的取貨地點" : "請選擇取貨地點"}</option>
                {!locations.isError && locations.data?.map((location) => <option key={location.id} value={location.id}>{location.name}</option>)}
              </select>
            </label>}
          </div>
          {method === "pickup" && locations.isError && <p className="form-error">取貨點暫時無法使用，請稍後再試。</p>}
          {method === "shipping" && (
            <div className="shipping-form">
              <label className="field">配送通路
                <select value={shippingChannel || ""} required disabled={checkout.isPending || !item.allowed_shipping_channels.length} onChange={(event) => setShippingChannel(event.target.value as ShippingChannel)}>
                  <option value="" disabled>請選擇配送通路</option>
                  {item.allowed_shipping_channels.map((channel) => <option key={channel} value={channel}>{shippingChannelLabels[channel]}</option>)}
                </select>
              </label>
              <div className="recipient-grid">
                <label>收件人姓名<input value={recipientName} onChange={(event) => setRecipientName(event.target.value)} /></label>
                <label>聯絡電話<input value={recipientPhone} onChange={(event) => setRecipientPhone(event.target.value)} inputMode="tel" /></label>
                <label className="recipient-address">配送地址<input value={shippingAddress} onChange={(event) => setShippingAddress(event.target.value)} /></label>
              </div>
            </div>
          )}

          <InvoicePreferenceFields
            onChange={setInvoicePreference}
            value={invoicePreference}
          />
        </div>

        <aside className="offer-summary">
          <UsersThree size={28} weight="light" />
          <p className="eyebrow">GROUP ORDER / 共同購買訂單</p>
          <h2>共同購買摘要</h2>
          {!user ? (
            <button className="button button-primary full-width" type="button" onClick={openLogin}>登入後取得正式價格</button>
          ) : (
            <>
              {quote.isPending && !orderBlockReason && <LoadingLines count={2} />}
              {quote.isError && <p className="form-error">{quote.error.message}</p>}
              <dl>
                {method === "pickup" && <div><dt>取貨地點</dt><dd>{locations.isError ? "暫時無法使用" : selectedPickupLocation?.name || "尚未選擇"}</dd></div>}
                {method === "pickup" && !locations.isError && selectedPickupLocation?.address && <div><dt>取貨地址</dt><dd>{selectedPickupLocation.address}</dd></div>}
                <div><dt>社員單價</dt><dd>{formatMoney(quote.data?.unit_price || item.member_price)}</dd></div>
                <div><dt>商品小計</dt><dd>{formatMoney(quote.data?.product_subtotal || 0)}</dd></div>
                {shippingEnabled && <div><dt>運費</dt><dd>{formatMoney(quote.data?.shipping_fee || 0)}</dd></div>}
                <InvoicePreferenceSummary value={invoicePreference} />
                {quote.data && <InvoiceTaxSummary items={[{ tax_type: quote.data.tax_type }]} memberTaxExempt={quote.data.member_tax_exempt} shippingFee={quote.data.shipping_fee} taxAmount={quote.data.tax_amount} />}
                <div className="offer-total"><dt>合計</dt><dd>{formatMoney(quote.data?.amount_total || 0)}</dd></div>
              </dl>
              {checkout.isError && <p className="form-error">{checkout.error.message}</p>}
              <button className="button button-system full-width" type="button" disabled={!submitReady || checkout.isPending || item.intake_status !== "open"} onClick={() => checkout.mutate()}>
                {checkout.isPending ? "建立共同購買訂單中…" : method === "shipping" ? "參加並選擇物流" : "參加並前往付款"}
              </button>
            </>
          )}
        </aside>
      </div>
    </section>
  );
}
