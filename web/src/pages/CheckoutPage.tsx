import {
  ArrowLeft,
  Minus,
  Package,
  Plus,
  Receipt,
  ShieldCheck,
} from "@phosphor-icons/react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";

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
import { useCommerce } from "../context/CommerceContext";
import { maxOrderItemQuantity, productQuantityLimit } from "../lib/cart-quantity";
import { openOrderHandoff } from "../lib/checkout-navigation";
import { ApiError, apiFetch, formatMoney, isCurrentSessionUser, replaceBrokenAsset, resolveAsset } from "../lib/api";
import { checkoutWasRejected, clearCheckoutSubmission, prepareCheckoutSubmission, readCheckoutSubmission, type CheckoutSubmission } from "../lib/checkout-submission";
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
  Product,
  ShippingChannel,
  ShippingRate,
} from "../lib/types";
import "../styles/fulfillment-methods.css";

type CheckoutMethod = Extract<FulfillmentMethod, "cooperative_pickup"> | "shipping";
const logisticsEnabled = import.meta.env.VITE_LOGISTICS_ENABLED === "true";
const stockQueryKey = "checkout-stock";
const stockMaxAge = 30_000;

export function CheckoutPage() {
  const { user, openLogin, saveMobileBarcode } = useAuth();
  const queryClient = useQueryClient();
  const platform = useStorefrontStatus();
  const shippingEnabled = logisticsEnabled && storefrontCapabilities(platform.data).shipping;
  const notificationEmail = user?.notification_email || user?.email || "";
  const { cart, adjustQuantity, removeFromCart, clearCart } = useCommerce();
  const [quantityError, setQuantityError] = useState("");
  const [checkoutMethod, setCheckoutMethod] =
    useState<CheckoutMethod>("cooperative_pickup");
  const [pickupLocationId, setPickupLocationId] = useState("");
  const [shippingChannel, setShippingChannel] =
    useState<ShippingChannel | null>(null);
  const [recipientName, setRecipientName] = useState("");
  const [recipientPhone, setRecipientPhone] = useState("");
  const [shippingAddress, setShippingAddress] = useState("");
  const [invoicePreference, setInvoicePreference] = useInvoicePreference(notificationEmail);
  const [pendingSubmission, setPendingSubmission] = useState<CheckoutSubmission | null>(null);
  useEffect(() => {
    setPendingSubmission(user ? readCheckoutSubmission(user.id) : null);
    setQuantityError("");
  }, [user?.id]);
  const items = useMemo(
    () =>
      cart.map((item) => ({
        product_id: item.product.id,
        quantity: item.quantity,
      })),
    [cart],
  );
  const orderBlockReason = storefrontOrderBlockReason(platform.data, "regular", items)
    || (checkoutMethod === "shipping" && !shippingEnabled ? "目前未開放配送，請選擇合作社取貨" : "");
  const itemKey = items.map((item) => `${item.product_id}:${item.quantity}`).join("|");
  const eligibility = useMemo(() => shippingEligibility(cart), [cart]);
  const shippingAvailable = shippingEnabled && eligibility.blockers.length === 0;

  const locations = useQuery({
    queryKey: ["pickup-locations"],
    queryFn: () => apiFetch<PickupLocation[]>("/v1/pickup-locations"),
  });
  const selectedPickupLocation = locations.data?.find((location) => location.id === pickupLocationId);
  const rates = useQuery({
    queryKey: ["shipping-rates"],
    queryFn: () => apiFetch<ShippingRate[]>("/v1/shipping-rates"),
    enabled: shippingEnabled,
  });
  const stock = useQueries({ queries: items.map(({ product_id }) => ({
    queryKey: [stockQueryKey, product_id],
    queryFn: ({ signal }: { signal: AbortSignal }) =>
      apiFetch<Product>(`/v1/products/${encodeURIComponent(product_id)}`, { cache: "no-store", signal }),
    enabled: !pendingSubmission,
    refetchOnMount: "always" as const,
    refetchOnWindowFocus: true,
    staleTime: stockMaxAge,
    retry: false,
  })) });
  const quote = useQuery({
    queryKey: ["order-quote", itemKey, user?.id, user?.membership_type, invoicePreference.buyerType],
    queryFn: () =>
      apiFetch<OrderQuote>("/v1/orders/quote", {
        method: "POST",
        body: JSON.stringify({ items, invoice_buyer_type: invoicePreference.buyerType }),
      }),
    enabled: Boolean(user && items.length && !orderBlockReason),
  });
  useEffect(() => {
    if (quote.error instanceof ApiError && [409, 422].includes(quote.error.status)) {
      void queryClient.invalidateQueries({ queryKey: [stockQueryKey] });
    }
  }, [queryClient, quote.error, quote.errorUpdatedAt]);
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
    mutationFn: async () => {
      if (!user) throw new Error("請先登入");
      let submission = readCheckoutSubmission(user.id);
      if (!submission) {
        if (orderBlockReason) throw new Error(orderBlockReason);
        if (!quote.data || quote.isFetching || quote.isError) throw new Error("請重新取得訂單價格後再試");
        if (checkoutMethod === "shipping" && (!shippingChannel || !eligibility.temperature)) {
          throw new Error("這筆商品目前無法使用配送");
        }
        if (checkoutMethod !== "shipping" && (!selectedPickupLocation || locations.isError)) throw new Error("請選擇可用的取貨地點");
        await saveInvoiceCarrierIfRequested(invoicePreference, user.saved_mobile_barcode, saveMobileBarcode);
      }
      if (!isCurrentSessionUser(user.id)) throw new ApiError(409, "登入身分已變更，請確認目前帳號後重新操作。");
      try {
        submission = prepareCheckoutSubmission(user.id, submission?.input ?? {
          items,
          expectedTotal: quote.data!.amount_total,
          expectedQuoteFingerprint: quote.data!.confirmation_fingerprint,
          contactEmail: invoicePreference.buyerEmail.trim(),
          invoicePreference,
          fulfillment: checkoutMethod === "shipping" ? {
            kind: "shipping",
            channel: shippingChannel!,
            temperature: eligibility.temperature!,
            recipientName,
            recipientPhone,
            shippingAddress,
          } : { kind: "pickup", pickupLocationId },
        }, submission?.quote ?? quote.data!);
      } catch (reason) {
        setPendingSubmission(readCheckoutSubmission(user.id));
        throw reason;
      }
      setPendingSubmission(submission);
      try {
        const outcome = await beginCheckout({ ...submission.input, clientRequestId: submission.requestId });
        if (!isCurrentSessionUser(user.id)) throw new ApiError(409, "登入身分已變更，請確認目前帳號後重新操作。");
        return { outcome, submission, userId: user.id };
      } catch (reason) {
        if (checkoutWasRejected(reason)) {
          clearCheckoutSubmission(user.id, submission.requestId);
          setPendingSubmission(null);
          if (reason instanceof Error && reason.message === "價格已變動，請重新確認訂單明細") await quote.refetch();
        }
        throw reason;
      }
    },
    onSuccess: async ({ outcome, submission, userId }) => {
      if (!isCurrentSessionUser(userId)) return;
      clearCart(submission.input.items);
      if (outcome.kind === "redirect") {
        await openOrderHandoff(outcome.url, outcome.order.id, () => isCurrentSessionUser(userId));
        clearCheckoutSubmission(userId, submission.requestId);
        return;
      }
      const params = new URLSearchParams({
        order_id: outcome.order.id,
        result: "resume",
        message: outcome.message,
      });
      window.location.assign(`/orders?${params.toString()}`);
      clearCheckoutSubmission(userId, submission.requestId);
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

  const shippingReady = Boolean(
    shippingChannel &&
      eligibility.temperature &&
      recipientName.trim() &&
      recipientPhone.trim() &&
      shippingAddress.trim() &&
      estimatedShippingFee !== null,
  );
  const canSubmit = Boolean(
    !orderBlockReason && quote.data && !quote.isFetching && !quote.isError &&
      invoicePreferenceIsValid(invoicePreference) &&
      (checkoutMethod === "shipping" ? shippingReady : selectedPickupLocation && !locations.isError),
  );
  const changeQuantity = (change: () => void) => {
    try {
      change();
      setQuantityError("");
    } catch (reason) {
      setQuantityError(reason instanceof RangeError ? reason.message : "無法儲存結帳清單，請再試一次。");
    }
  };

  return (
    <section className="checkout-page">
      <div className="checkout-heading">
        <Link className="text-link" to="/shop" search={{ tab: "products" }}>
          <ArrowLeft size={17} weight="light" />
          回到生活消費
        </Link>
        <p className="eyebrow">CHECKOUT / 結帳</p>
        <h1>把今天的選擇，<br />安排進生活裡。</h1>
      </div>
      {orderBlockReason && !pendingSubmission && <p className="form-error" role="status">{orderBlockReason}</p>}

      {pendingSubmission ? (
        <div className="order-summary checkout-recovery">
          <h2>確認原訂單結果</h2>
          <p role="status">上次送單尚未確認結果，請先恢復原訂單。</p>
          <dl className="summary-lines">
            {pendingSubmission.quote.items.map(item => <div key={item.product_id}><dt>{item.product_name} × {item.quantity}</dt><dd>{formatMoney(item.subtotal)}</dd></div>)}
            <InvoicePreferenceSummary value={pendingSubmission.input.invoicePreference} />
            <div className="summary-total"><dt>商品合計</dt><dd>{formatMoney(pendingSubmission.input.expectedTotal)}</dd></div>
          </dl>
          {createOrder.isError && <p className="form-error" role="alert">{createOrder.error.message}</p>}
          <button className="button button-primary" type="button" disabled={createOrder.isPending} onClick={() => createOrder.mutate()}>{createOrder.isPending ? "確認訂單中…" : "重試原訂單"}</button>
          <Link className="text-link" to="/orders" search={{}}>查看我的訂單</Link>
        </div>
      ) : cart.length === 0 ? (
        <DataState title="結帳清單還是空的" detail="回到生活消費，加入合作社正式上架的商品。" />
      ) : (
        <div className="checkout-grid">
          <div className="checkout-items">
            <div className="checkout-step-title">
              <span>01</span>
              <div><small>YOUR SELECTION</small><h2>選購內容</h2></div>
            </div>
            {cart.map(({ product, quantity }, index) => {
              const availability = stock[index];
              const checked = availability.isFetchedAfterMount && availability.fetchStatus === "idle";
              const latest = checked && availability.isSuccess && !availability.isStale &&
                availability.data?.id === product.id ? availability.data : null;
              const unavailable = (checked && availability.error instanceof ApiError && availability.error.status === 404)
                || latest?.is_active === false || latest?.daily_sales_enabled === false;
              const limit = latest?.is_active === true && latest.daily_sales_enabled === true &&
                Number.isInteger(latest.stock_quantity) && latest.stock_quantity >= 0 ? productQuantityLimit(latest) : null;
              const checking = availability.fetchStatus === "fetching";
              let stockMessage = "";
              if (unavailable) stockMessage = "目前停止販售";
              else if (limit === 0) stockMessage = "目前缺貨";
              else if (checking) stockMessage = "正在確認庫存";
              else if (limit === null) stockMessage = availability.isSuccess && availability.isStale ? "庫存待確認" : "暫時無法確認庫存";
              else if (quantity >= limit) stockMessage = `目前最多可買 ${limit} ${latest?.unit || product.unit}`;
              const statusId = `checkout-stock-${product.id}`;
              return (
                <article className="checkout-item" key={product.id}>
                  <img src={resolveAsset(product.image_url)} alt={product.name} onError={replaceBrokenAsset} />
                  <div>
                    <small>{product.supplier_name || product.category}</small>
                    <h3>{product.name}</h3>
                    <p>{formatMoney(quote.data?.items.find((item) => item.product_id === product.id)?.unit_price
                      ?? (user?.membership_type === "member" || user?.membership_type === "trainee" ? product.member_price : product.nonmember_price))}／{product.unit}</p>
                    {stockMessage && <p id={statusId} role="status">{stockMessage}</p>}
                    {(unavailable || limit === 0) && <button className="text-link" type="button" disabled={createOrder.isPending} onClick={() => changeQuantity(() => removeFromCart(product.id))}>移除商品</button>}
                    {!unavailable && limit === null && !checking && <button className="text-link" type="button" disabled={availability.fetchStatus === "paused"} onClick={() => void availability.refetch()}>重查庫存</button>}
                  </div>
                  <div className="quantity-control">
                    <button type="button" disabled={createOrder.isPending} onClick={() => changeQuantity(() => adjustQuantity(product.id, -1))} aria-label="減少數量">
                      <Minus size={15} />
                    </button>
                    <span>{quantity}</span>
                    <button type="button" disabled={createOrder.isPending || limit === null || quantity >= limit} aria-label="增加數量" aria-describedby={stockMessage ? statusId : undefined} onClick={() => {
                      if (Date.now() - availability.dataUpdatedAt >= stockMaxAge) {
                        void availability.refetch();
                        return;
                      }
                      changeQuantity(() => adjustQuantity(product.id, 1, limit));
                    }}>
                      <Plus size={15} />
                    </button>
                  </div>
                </article>
              );
            })}
            {quantityError && <p className="form-error" role="alert">{quantityError}</p>}

            <div className="checkout-step-title pickup-title">
              <span>02</span>
              <div><small>FULFILLMENT</small><h2>選擇取貨方式</h2></div>
            </div>
            <div className="checkout-fulfillment">
              {shippingAvailable || checkoutMethod === "shipping" ? (
                <fieldset className="fulfillment-method-choice" disabled={createOrder.isPending}>
                  <legend>取貨方式</legend>
                  <div className="fulfillment-method-options">
                    <label className={`fulfillment-method-option${checkoutMethod === "cooperative_pickup" ? " selected" : ""}`}>
                      <input type="radio" name="checkout_fulfillment_method" value="cooperative_pickup" checked={checkoutMethod === "cooperative_pickup"} onChange={() => setCheckoutMethod("cooperative_pickup")} />
                      <span>合作社取貨</span>
                    </label>
                    <label className={`fulfillment-method-option${checkoutMethod === "shipping" ? " selected" : ""}${!shippingAvailable ? " unavailable" : ""}`}>
                      <input type="radio" name="checkout_fulfillment_method" value="shipping" checked={checkoutMethod === "shipping"} disabled={!shippingAvailable} onChange={() => setCheckoutMethod("shipping")} />
                      <span>宅配／超商取貨</span>
                    </label>
                  </div>
                </fieldset>
              ) : (
                <dl className="fulfillment-method-summary"><dt>取貨方式</dt><dd>合作社取貨</dd></dl>
              )}
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
            {shippingEnabled && eligibility.blockers.length > 0 && (
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
                <label className="field">
                  配送通路
                  <select value={shippingChannel || ""} required disabled={createOrder.isPending || !eligibility.availableChannels.length} onChange={(event) => setShippingChannel(event.target.value as ShippingChannel)}>
                    <option value="" disabled>請選擇配送通路</option>
                    {eligibility.availableChannels.map((channel) => (
                      <option key={channel} value={channel}>{shippingChannelLabels[channel]}</option>
                    ))}
                  </select>
                </label>
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
                  {quote.isPending && !orderBlockReason && <LoadingLines count={2} />}
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
                      taxAmountLabel={estimatedShippingFee ? "預估商品稅額" : "預估稅額"}
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
