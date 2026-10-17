import {
  ArrowLeft,
  Check,
  Minus,
  Package,
  Plus,
  Receipt,
  ShieldCheck,
} from "@phosphor-icons/react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";

import { DataState, LoadingLines } from "../components/Shared";
import { InvoiceTaxSummary } from "../components/InvoiceTaxSummary";
import {
  defaultInvoicePreference,
  InvoicePreferenceFields,
  InvoicePreferenceSummary,
  invoicePreferenceIsValid,
} from "../components/InvoicePreferenceFields";
import { useAuth } from "../context/AuthContext";
import { useCommerce } from "../context/CommerceContext";
import { openOrderHandoff } from "../lib/checkout-navigation";
import { apiFetch, formatMoney, replaceBrokenAsset, resolveAsset } from "../lib/api";
import { taxTypeLabel } from "../lib/invoice-tax";
import {
  beginCheckout,
  shippingChannelLabels,
  shippingEligibility,
  shippingFee,
  temperatureLabels,
} from "../lib/commerce";
import type {
  FulfillmentMethod,
  OrderQuote,
  PickupLocation,
  ShippingChannel,
  ShippingRate,
} from "../lib/types";

type CheckoutMethod = Extract<FulfillmentMethod, "cooperative_pickup"> | "shipping";
const logisticsEnabled = import.meta.env.VITE_LOGISTICS_ENABLED === "true";

export function CheckoutPage() {
  const { user, openLogin } = useAuth();
  const notificationEmail = user?.notification_email || user?.email || "";
  const { cart, changeQuantity, clearCart } = useCommerce();
  const [checkoutMethod, setCheckoutMethod] =
    useState<CheckoutMethod>("cooperative_pickup");
  const [pickupLocationId, setPickupLocationId] = useState("");
  const [shippingChannel, setShippingChannel] =
    useState<ShippingChannel | null>(null);
  const [recipientName, setRecipientName] = useState("");
  const [recipientPhone, setRecipientPhone] = useState("");
  const [shippingAddress, setShippingAddress] = useState("");
  const [invoicePreference, setInvoicePreference] = useState(
    () => defaultInvoicePreference(notificationEmail),
  );
  const items = useMemo(
    () =>
      cart.map((item) => ({
        product_id: item.product.id,
        quantity: item.quantity,
      })),
    [cart],
  );
  const itemKey = items.map((item) => `${item.product_id}:${item.quantity}`).join("|");
  const eligibility = useMemo(() => shippingEligibility(cart), [cart]);

  const locations = useQuery({
    queryKey: ["pickup-locations"],
    queryFn: () => apiFetch<PickupLocation[]>("/v1/pickup-locations"),
  });
  const selectedPickupLocation = locations.data?.find((location) => location.id === pickupLocationId);
  const rates = useQuery({
    queryKey: ["shipping-rates"],
    queryFn: () => apiFetch<ShippingRate[]>("/v1/shipping-rates"),
    enabled: logisticsEnabled,
  });
  const quote = useQuery({
    queryKey: ["order-quote", itemKey, user?.id, invoicePreference.buyerType],
    queryFn: () =>
      apiFetch<OrderQuote>("/v1/orders/quote", {
        method: "POST",
        body: JSON.stringify({ items, invoice_buyer_type: invoicePreference.buyerType }),
      }),
    enabled: Boolean(user && items.length),
  });
  const estimatedShippingFee =
    checkoutMethod === "shipping" &&
    shippingChannel &&
    eligibility.temperature &&
    quote.data &&
    rates.data
      ? shippingFee(
          rates.data,
          shippingChannel,
          eligibility.temperature,
          quote.data.amount_total,
        )
      : 0;
  const createOrder = useMutation({
    mutationFn: () => {
      if (!user) throw new Error("請先登入");
      if (checkoutMethod === "shipping") {
        if (!shippingChannel || !eligibility.temperature) {
          throw new Error("這筆商品目前無法使用配送");
        }
        return beginCheckout({
          items,
          contactEmail: invoicePreference.buyerEmail.trim(),
          invoicePreference,
          fulfillment: {
            kind: "shipping",
            channel: shippingChannel,
            temperature: eligibility.temperature,
            recipientName,
            recipientPhone,
            shippingAddress,
          },
        });
      }
      if (!selectedPickupLocation || locations.isError) throw new Error("請選擇可用的取貨地點");
      return beginCheckout({
        items,
        contactEmail: invoicePreference.buyerEmail.trim(),
        invoicePreference,
        fulfillment: { kind: "pickup", pickupLocationId },
      });
    },
    onSuccess: async (outcome) => {
      clearCart();
      if (outcome.kind === "redirect") {
        await openOrderHandoff(outcome.url, outcome.order.id);
        return;
      }
      const params = new URLSearchParams({
        order_id: outcome.order.id,
        result: "resume",
        message: outcome.message,
      });
      window.location.assign(`/orders?${params.toString()}`);
    },
  });

  useEffect(() => {
    if (locations.data && !locations.data.some((location) => location.id === pickupLocationId)) {
      setPickupLocationId(locations.data[0]?.id || "");
    }
  }, [locations.data, pickupLocationId]);

  useEffect(() => {
    if (
      !shippingChannel ||
      !eligibility.availableChannels.includes(shippingChannel)
    ) {
      setShippingChannel(eligibility.availableChannels[0] || null);
    }
  }, [eligibility.availableChannels, shippingChannel]);

  useEffect(() => {
    if (!notificationEmail) return;
    setInvoicePreference((current) =>
      current.buyerEmail ? current : { ...current, buyerEmail: notificationEmail },
    );
  }, [notificationEmail]);

  const shippingReady = Boolean(
    shippingChannel &&
      eligibility.temperature &&
      recipientName.trim() &&
      recipientPhone.trim() &&
      shippingAddress.trim() &&
      estimatedShippingFee !== null,
  );
  const canSubmit = Boolean(
    quote.data && !quote.isFetching && !quote.isError &&
      invoicePreferenceIsValid(invoicePreference) &&
      (checkoutMethod === "shipping" ? shippingReady : selectedPickupLocation && !locations.isError),
  );

  return (
    <section className="checkout-page">
      <div className="checkout-heading">
        <Link className="text-link" to="/shop">
          <ArrowLeft size={17} weight="light" />
          回到生活消費
        </Link>
        <p className="eyebrow">CHECKOUT / 結帳</p>
        <h1>把今天的選擇，<br />安排進生活裡。</h1>
      </div>

      {cart.length === 0 ? (
        <DataState title="結帳清單還是空的" detail="回到生活消費，加入合作社正式上架的商品。" />
      ) : (
        <div className="checkout-grid">
          <div className="checkout-items">
            <div className="checkout-step-title">
              <span>01</span>
              <div><small>YOUR SELECTION</small><h2>選購內容</h2></div>
            </div>
            {cart.map(({ product, quantity }) => (
              <article className="checkout-item" key={product.id}>
                <img src={resolveAsset(product.image_url)} alt={product.name} onError={replaceBrokenAsset} />
                <div>
                  <small>{product.supplier_name || product.category}</small>
                  <h3>{product.name}</h3>
                  <p>{formatMoney(product.member_price)}／{product.unit}</p>
                </div>
                <div className="quantity-control">
                  <button type="button" onClick={() => changeQuantity(product.id, quantity - 1)} aria-label="減少數量">
                    <Minus size={15} />
                  </button>
                  <span>{quantity}</span>
                  <button type="button" onClick={() => changeQuantity(product.id, quantity + 1)} aria-label="增加數量">
                    <Plus size={15} />
                  </button>
                </div>
              </article>
            ))}

            <div className="checkout-step-title pickup-title">
              <span>02</span>
              <div><small>FULFILLMENT</small><h2>選擇取貨方式</h2></div>
            </div>
            <div className="checkout-fulfillment">
              <label className="field">
                取貨方式
                <select
                  value={checkoutMethod}
                  onChange={(event) => setCheckoutMethod(event.target.value as CheckoutMethod)}
                >
                  <option value="cooperative_pickup">合作社取貨</option>
                  {logisticsEnabled && <option value="shipping" disabled={Boolean(eligibility.blockers.length)}>宅配／超商取貨</option>}
                </select>
              </label>
              {checkoutMethod === "cooperative_pickup" && (
                <label className="field">
                  取貨地點
                  <select
                    value={pickupLocationId}
                    onChange={(event) => setPickupLocationId(event.target.value)}
                    disabled={locations.isPending || locations.isError || !locations.data?.length}
                    required
                  >
                    <option value="" disabled>{locations.isPending ? "載入取貨地點中…" : locations.isError ? "取貨點暫時無法使用" : !locations.data?.length ? "目前沒有可用的取貨地點" : "請選擇取貨地點"}</option>
                    {!locations.isError && locations.data?.map((location) => <option key={location.id} value={location.id}>{location.name}</option>)}
                  </select>
                </label>
              )}
            </div>
            {logisticsEnabled && eligibility.blockers.length > 0 && (
              <div className="shipping-blockers">
                <Package size={20} weight="light" />
                <p>{eligibility.blockers.join("；")}。這筆仍可選擇合作社取貨。</p>
              </div>
            )}
            {checkoutMethod === "cooperative_pickup" && locations.isError && (
              <DataState kind="error" title="取貨點暫時無法使用" detail="請稍後再試。" />
            )}
            {checkoutMethod === "shipping" && (
              <div className="shipping-form">
                <div className="shipping-temperature">
                  <span>本筆溫層</span>
                  <strong>{eligibility.temperature ? temperatureLabels[eligibility.temperature] : "無法判定"}</strong>
                </div>
                <fieldset>
                  <legend>配送通路</legend>
                  <div className="shipping-channels">
                    {eligibility.availableChannels.map((channel) => (
                      <label key={channel} className={shippingChannel === channel ? "selected" : ""}>
                        <input
                          type="radio"
                          name="shipping-channel"
                          checked={shippingChannel === channel}
                          onChange={() => setShippingChannel(channel)}
                        />
                        <span>{shippingChannelLabels[channel]}</span>
                        {shippingChannel === channel && <Check size={14} />}
                      </label>
                    ))}
                  </div>
                </fieldset>
                <div className="recipient-grid">
                  <label>
                    收件人姓名
                    <input
                      value={recipientName}
                      onChange={(event) => setRecipientName(event.target.value)}
                      placeholder="2–5 個中文字"
                      autoComplete="name"
                    />
                  </label>
                  <label>
                    聯絡電話
                    <input
                      value={recipientPhone}
                      onChange={(event) => setRecipientPhone(event.target.value)}
                      placeholder="0912345678"
                      inputMode="tel"
                      autoComplete="tel"
                    />
                  </label>
                  <label className="recipient-address">
                    配送地址
                    <input
                      value={shippingAddress}
                      onChange={(event) => setShippingAddress(event.target.value)}
                      placeholder="超商取貨仍需填寫綠界要求的聯絡地址"
                      autoComplete="street-address"
                    />
                  </label>
                </div>
              </div>
            )}

            <InvoicePreferenceFields
              onChange={setInvoicePreference}
              value={invoicePreference}
            />
          </div>

          <aside className="order-summary">
            <div className="summary-bezel">
              <p className="eyebrow">ORDER SUMMARY / 訂單摘要</p>
              <h2>訂單摘要</h2>
              {!user ? (
                <div className="login-required">
                  <ShieldCheck size={27} weight="light" />
                  <p>登入後依社員身分重新計算價格。</p>
                  <button className="button button-primary" type="button" onClick={openLogin}>登入並試算</button>
                </div>
              ) : (
                <>
                  {quote.isPending && <LoadingLines count={2} />}
                  {quote.isError && <p className="form-error">{quote.error.message}</p>}
                  <dl className="summary-lines">
                    {quote.data?.items.map((item) => (
                      <div key={item.product_id}>
                        <dt>{item.product_name} × {item.quantity} · {taxTypeLabel(item.tax_type)}</dt>
                        <dd>{formatMoney(item.subtotal)}</dd>
                      </div>
                    ))}
                    <div>
                      <dt>取貨方式</dt>
                      <dd>{checkoutMethod === "shipping" ? "綠界物流" : "合作社取貨"}</dd>
                    </div>
                    {checkoutMethod === "cooperative_pickup" && (
                      <div className="summary-pickup">
                        <dt>取貨地點</dt>
                        <dd>{locations.isError ? "暫時無法使用" : selectedPickupLocation?.name || (locations.isPending ? "載入中…" : "尚未選擇")}</dd>
                      </div>
                    )}
                    <InvoicePreferenceSummary value={invoicePreference} />
                    {quote.data && <InvoiceTaxSummary
                      items={quote.data.items}
                      memberTaxExempt={quote.data.member_tax_exempt}
                      shippingFee={estimatedShippingFee || 0}
                      taxAmount={quote.data.tax_amount}
                      taxAmountLabel={estimatedShippingFee ? "商品稅額" : "稅額"}
                    />}
                    {logisticsEnabled && checkoutMethod === "shipping" && (
                      <div className="summary-shipping">
                        <dt>預估運費</dt>
                        <dd>
                          {estimatedShippingFee === null
                            ? "無適用費率"
                            : formatMoney(estimatedShippingFee || 0)}
                        </dd>
                      </div>
                    )}
                    <div className="summary-membership">
                      <dt>計價身分</dt>
                      <dd>{quote.data ? ({ nonmember: "一般買家", trainee: "實習社員", member: "正式社員" }[quote.data.membership_type] || quote.data.membership_type) : "讀取中"}</dd>
                    </div>
                    <div className="summary-total">
                      <dt>合計</dt>
                      <dd>{formatMoney((quote.data?.amount_total || 0) + (estimatedShippingFee || 0))}</dd>
                    </div>
                  </dl>
                  {createOrder.isError && <p className="form-error">{createOrder.error.message}</p>}
                  <button
                    className="button button-primary full-width"
                    type="button"
                    onClick={() => createOrder.mutate()}
                    disabled={!canSubmit || createOrder.isPending}
                  >
                    <Receipt size={19} weight="light" />
                    {createOrder.isPending
                      ? "準備結帳中…"
                      : checkoutMethod === "shipping"
                        ? "前往選擇物流"
                        : "前往線上付款"}
                  </button>
                </>
              )}
            </div>
          </aside>
        </div>
      )}
    </section>
  );
}
