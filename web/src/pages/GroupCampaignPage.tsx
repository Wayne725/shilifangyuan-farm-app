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
import { saveInvoiceCarrierIfRequested } from "../lib/invoice-carrier";
import { openOrderHandoff } from "../lib/checkout-navigation";
import { apiFetch, captureSessionIdentityGuard, formatMoney, replaceBrokenAsset, resolveAsset } from "../lib/api";
import {
  beginGroupCheckout,
  shippingChannelLabels,
  temperatureLabels,
} from "../lib/commerce";
import { campaignAccessDenied, campaignDateTime, campaignOrderBlockReason, campaignStatusLabel, retryCampaignRead } from "../lib/group-campaigns";
import { useDeadlineClock } from "../lib/use-deadline-clock";
import { clearGroupSubmission, groupQuoteIsConfirmed, groupSubmissionWasRejected, prepareGroupSubmission, readGroupSubmission, type GroupQuote, type GroupSubmission } from "../lib/group-submission";
import type { GroupCampaign, PickupLocation, ShippingChannel } from "../lib/types";
import "../styles/fulfillment-methods.css";

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
  const submissionScope = `${user?.id || ""}:${campaignId}`;
  const [savedSubmission, setSavedSubmission] = useState(() => ({ scope: submissionScope,
    submission: user ? readGroupSubmission(user.id, campaignId) : null }));
  const pendingSubmission = savedSubmission.scope === submissionScope ? savedSubmission.submission : null;
  const setPendingSubmission = (submission: GroupSubmission | null) => setSavedSubmission({ scope: submissionScope, submission });
  useEffect(() => {
    setSavedSubmission({ scope: `${user?.id || ""}:${campaignId}`, submission: user ? readGroupSubmission(user.id, campaignId) : null });
  }, [user?.id, campaignId]);
  const campaign = useQuery({
    queryKey: ["group-campaign", campaignId],
    queryFn: () => apiFetch<GroupCampaign>(`/v1/group-campaigns/${campaignId}`),
    retry: retryCampaignRead,
    refetchInterval: query => campaignAccessDenied(query.state.error) ? false : 30000,
  });
  const now = useDeadlineClock(campaign.data?.deadline);
  const orderBlockReason = storefrontOrderBlockReason(platform.data, "group")
    || (campaign.data ? campaignOrderBlockReason(campaign.data, now) : "")
    || (method === "shipping" && !shippingEnabled ? "目前未開放配送，請選擇合作社取貨" : "");
  const locations = useQuery({
    queryKey: ["pickup-locations"],
    queryFn: () => apiFetch<PickupLocation[]>("/v1/pickup-locations"),
  });
  const selectedPickupLocation = locations.data?.find((location) => location.id === pickupLocationId);
  const quote = useQuery({
    queryKey: ["group-quote", campaignId, quantity, method, shippingChannel, user?.id, user?.membership_type, invoicePreference.buyerType],
    queryFn: () => apiFetch<GroupQuote>(`/v1/group-campaigns/${campaignId}/quote`, {
      method: "POST",
      body: JSON.stringify({
        quantity,
        invoice_buyer_type: invoicePreference.buyerType,
        fulfillment_method: method === "shipping" ? "ecpay_logistics" : "cooperative_pickup",
        shipping_channel: method === "shipping" ? shippingChannel : null,
      }),
    }),
    enabled: Boolean(user && !pendingSubmission && campaign.data && !campaign.isError && !orderBlockReason && (method === "pickup" || shippingChannel)),
  });
  const checkout = useMutation({
    mutationFn: async () => {
      if (!user) throw new Error("請先登入");
      const sameIdentity = captureSessionIdentityGuard();
      let submission = readGroupSubmission(user.id, campaignId);
      const firstAttempt = !submission;
      try {
        if (!submission) {
          if (!campaign.data) throw new Error("共同購買資料尚未確認");
          if (orderBlockReason || campaignOrderBlockReason(campaign.data)) throw new Error(orderBlockReason || campaignOrderBlockReason(campaign.data));
          if (!groupQuoteIsConfirmed(quote.data) || quote.isFetching || quote.isError) throw new Error("請先確認最新報價");
          if (method === "shipping" && (!shippingChannel || !campaign.data.shipping_temperature)) throw new Error("請選擇配送通路");
          if (method === "pickup" && (!selectedPickupLocation || locations.isError)) throw new Error("請選擇可用的取貨地點");
          const originalQuote = { ...quote.data };
          const input = { campaignId, quantity, contactEmail: invoicePreference.buyerEmail.trim(),
            invoicePreference: { ...invoicePreference }, fulfillment: method === "shipping"
              ? { kind: "shipping" as const, channel: shippingChannel!, temperature: campaign.data.shipping_temperature!, recipientName, recipientPhone, shippingAddress }
              : { kind: "pickup" as const, pickupLocationId } };
          await saveInvoiceCarrierIfRequested(invoicePreference, user.saved_mobile_barcode, saveMobileBarcode);
          if (!sameIdentity()) throw new Error("登入身分已變更，請確認目前帳號後重新操作。");
          submission = prepareGroupSubmission(user.id, input, originalQuote, { title: campaign.data.title,
            pickupLocationName: selectedPickupLocation?.name });
        } else {
          submission = prepareGroupSubmission(user.id, submission.input, submission.quote,
            { title: submission.title, pickupLocationName: submission.pickupLocationName });
        }
        setPendingSubmission(submission);
        const outcome = await beginGroupCheckout({ ...submission.input, clientRequestId: submission.requestId }, undefined, sameIdentity);
        if (!sameIdentity()) throw new Error("登入身分已變更，請確認目前帳號後重新操作。");
        return { outcome, submission, userId: user.id, sameIdentity };
      } catch (reason) {
        if (sameIdentity()) {
          const saved = readGroupSubmission(user.id, campaignId);
          setPendingSubmission(saved);
          if (saved && groupSubmissionWasRejected(reason, firstAttempt)) {
            clearGroupSubmission(user.id, campaignId, saved.requestId);
            setPendingSubmission(null);
            if (reason instanceof Error && "code" in reason && reason.code === "quote_changed") await quote.refetch();
          }
        }
        throw reason;
      }
    },
    onSuccess: async ({ outcome, submission, userId, sameIdentity }) => {
      if (!sameIdentity()) return;
      if (outcome.kind === "redirect") {
        await openOrderHandoff(outcome.url, outcome.order.id, sameIdentity);
        clearGroupSubmission(userId, campaignId, submission.requestId);
        return;
      }
      const search = new URLSearchParams({ order_id: outcome.order.id, result: "resume", message: outcome.message });
      if (!sameIdentity()) return;
      window.location.assign(`/orders?${search.toString()}`);
      clearGroupSubmission(userId, campaignId, submission.requestId);
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

  if (pendingSubmission) return <section className="offer-page">
    <Link className="text-link" to="/shop" search={{ tab: "groups" }}><ArrowLeft size={17} />回到共同購買</Link>
    <h1>{pendingSubmission.title}</h1>
    <div className="offer-summary group-recovery">
      <h2>確認原共同購買訂單結果</h2>
      <p role="status">上次送單尚未確認結果，請先恢復原共同購買訂單。</p>
      <dl>
        <div><dt>數量</dt><dd>{pendingSubmission.input.quantity} 份</dd></div>
        <div><dt>本次單價</dt><dd>{formatMoney(pendingSubmission.quote.unit_price)}</dd></div>
        <div><dt>商品小計</dt><dd>{formatMoney(pendingSubmission.quote.product_subtotal)}</dd></div>
        {pendingSubmission.input.fulfillment.kind === "pickup"
          ? <div><dt>取貨地點</dt><dd>{pendingSubmission.pickupLocationName || "原取貨地點"}</dd></div>
          : <><div><dt>配送通路</dt><dd>{shippingChannelLabels[pendingSubmission.input.fulfillment.channel]}</dd></div>
            <div><dt>收件人</dt><dd>{pendingSubmission.input.fulfillment.recipientName}</dd></div>
            <div><dt>配送地址</dt><dd>{pendingSubmission.input.fulfillment.shippingAddress}</dd></div>
            <div><dt>運費</dt><dd>{formatMoney(pendingSubmission.quote.shipping_fee)}</dd></div></>}
        <InvoicePreferenceSummary value={pendingSubmission.input.invoicePreference} />
        <div className="offer-total"><dt>確認總額</dt><dd>{formatMoney(pendingSubmission.quote.amount_total)}</dd></div>
      </dl>
      {checkout.isError && <p className="form-error" role="alert">{checkout.error.message}</p>}
      <button className="button button-system full-width" type="button" disabled={checkout.isPending} onClick={() => checkout.mutate()}>
        {checkout.isPending ? "確認原訂單中…" : "重試原共同購買訂單"}
      </button>
      <Link className="text-link" to="/orders" search={{}}>查看我的訂單</Link>
    </div>
  </section>;

  if (campaign.isPending) return <section className="offer-page"><LoadingLines count={4} /></section>;
  if (campaign.isError || !campaign.data) {
    return <section className="offer-page"><DataState kind="error" title="共同購買資料無法讀取" detail={campaign.error?.message || "找不到這筆共同購買"} /></section>;
  }

  const item = campaign.data;
  const shippingAvailable = shippingEnabled && item.can_ship;
  const currentQuote = !orderBlockReason && !quote.isError && !quote.isFetching ? quote.data : undefined;
  const unitPriceLabel = !currentQuote ? "單價"
    : currentQuote.membership_type === "member" || currentQuote.membership_type === "trainee" ? "社員單價" : "一般單價";
  const shippingReady = Boolean(shippingChannel && recipientName.trim() && recipientPhone.trim() && shippingAddress.trim());
  const submitReady = Boolean(
    !orderBlockReason && groupQuoteIsConfirmed(quote.data) && !quote.isFetching && !quote.isError &&
      invoicePreferenceIsValid(invoicePreference) &&
      (method === "pickup" ? selectedPickupLocation && !locations.isError : shippingReady),
  );

  return (
    <section className="offer-page">
      <Link className="text-link" to="/shop" search={{ tab: "groups" }}><ArrowLeft size={17} />回到共同購買</Link>
      <div className="offer-hero">
        <div className="offer-image">
          <img src={resolveAsset(item.image_url)} alt={item.title} onError={replaceBrokenAsset} />
          <span>{campaignStatusLabel(item, now)}</span>
        </div>
        <div className="offer-copy">
          <p className="eyebrow">BUYING TOGETHER / 共同購買</p>
          <h1>{item.title}</h1>
          <p>{item.description}</p>
          <dl>
            <div><dt>已付款</dt><dd>{item.paid_quantity}／{item.min_paid_quantity} 份門檻</dd></div>
            <div><dt>剩餘供應</dt><dd>{item.available_quantity} 份</dd></div>
            <div><dt>付款截止（台灣）</dt><dd>{campaignDateTime(item.deadline)}</dd></div>
            {item.confirmation_deadline && <div><dt>審核截止（台灣）</dt><dd>{campaignDateTime(item.confirmation_deadline)}</dd></div>}
            <div><dt>{item.final_pickup_at ? "最終取貨" : "預計取貨"}</dt><dd>{campaignDateTime(item.final_pickup_at || item.estimated_pickup_start)}</dd></div>
          </dl>
        </div>
      </div>

      {orderBlockReason && <p className="form-error" role="status">{orderBlockReason}</p>}
      <div className="offer-checkout">
        <div>
          <div className="checkout-step-title"><span>01</span><div><small>QUANTITY</small><h2>選擇數量</h2></div></div>
          <div className="offer-quantity">
            <button aria-label="減少數量" disabled={Boolean(orderBlockReason) || checkout.isPending || quantity <= 1} type="button" onClick={() => setQuantity((value) => Math.max(1, value - 1))}><Minus size={16} /></button>
            <strong>{quantity}</strong>
            <button aria-label="增加數量" disabled={Boolean(orderBlockReason) || checkout.isPending || quantity >= Math.min(item.per_user_cap, item.available_quantity)} type="button" onClick={() => setQuantity((value) => Math.min(item.per_user_cap, item.available_quantity, value + 1))}><Plus size={16} /></button>
            <span>每人上限 {item.per_user_cap} 份</span>
          </div>

          <div className="checkout-step-title pickup-title"><span>02</span><div><small>FULFILLMENT</small><h2>取貨方式</h2></div></div>
          <div className="checkout-fulfillment">
            {shippingAvailable || method === "shipping" ? (
              <fieldset className="fulfillment-method-choice" disabled={checkout.isPending}>
                <legend>取貨方式</legend>
                <div className="fulfillment-method-options">
                  <label className={`fulfillment-method-option${method === "pickup" ? " selected" : ""}`}>
                    <input type="radio" name="group_fulfillment_method" value="pickup" checked={method === "pickup"} onChange={() => setMethod("pickup")} />
                    <span>合作社取貨</span>
                  </label>
                  <label className={`fulfillment-method-option${method === "shipping" ? " selected" : ""}${!shippingAvailable ? " unavailable" : ""}`}>
                    <input type="radio" name="group_fulfillment_method" value="shipping" checked={method === "shipping"} disabled={!shippingAvailable} onChange={() => setMethod("shipping")} />
                    <span>宅配／超商取貨{!shippingEnabled ? "（目前未開放配送）" : !item.can_ship ? "（此共同購買未開放配送）" : `（${temperatureLabels[item.shipping_temperature || "ambient"]}）`}</span>
                  </label>
                </div>
              </fieldset>
            ) : (
              <dl className="fulfillment-method-summary"><dt>取貨方式</dt><dd>合作社取貨</dd></dl>
            )}
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
                <div><dt>{unitPriceLabel}</dt><dd>{currentQuote ? formatMoney(currentQuote.unit_price) : "—"}</dd></div>
                <div><dt>商品小計</dt><dd>{currentQuote ? formatMoney(currentQuote.product_subtotal) : "—"}</dd></div>
                {shippingEnabled && <div><dt>運費</dt><dd>{currentQuote ? formatMoney(currentQuote.shipping_fee) : "—"}</dd></div>}
                <InvoicePreferenceSummary value={invoicePreference} />
                {currentQuote && <InvoiceTaxSummary items={[{ tax_type: currentQuote.tax_type }]} memberTaxExempt={currentQuote.member_tax_exempt} shippingFee={currentQuote.shipping_fee} taxAmount={currentQuote.tax_amount} />}
                <div className="offer-total"><dt>合計</dt><dd>{currentQuote ? formatMoney(currentQuote.amount_total) : "—"}</dd></div>
              </dl>
              {quote.data && !groupQuoteIsConfirmed(quote.data) && <><p className="form-error" role="status">報價識別尚未確認，請重新取得報價</p><button className="button button-quiet" type="button" disabled={quote.isFetching} onClick={() => void quote.refetch()}>重新取得報價</button></>}
              {checkout.isError && <p className="form-error" role="alert">{checkout.error.message}</p>}
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
