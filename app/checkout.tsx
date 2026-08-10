import { useMutation, useQuery } from "@tanstack/react-query";
import { router, useLocalSearchParams } from "expo-router";
import { useEffect, useState } from "react";
import {
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import {
  Button,
  EmptyState,
  InlineMessage,
  LoadingState,
  PageHeader,
  Screen,
} from "../src/components/ui";
import { getLogisticsBlockers } from "../src/lib/checkoutValidation";
import { membershipLabel, money } from "../src/lib/format";
import { openLogisticsPage, openPaymentPage } from "../src/lib/payment";
import { api, getErrorMessage } from "../src/services/api";
import { useAuth } from "../src/store/AuthContext";
import { useCart } from "../src/store/CartContext";
import { colors, radii, spacing } from "../src/theme";
import type {
  FulfillmentMethod,
  InvoiceCarrierType,
  LogisticsProvider,
  TemperatureZone,
} from "../src/types";

const logisticsOptions: { value: LogisticsProvider; label: string }[] = [
  { value: "home_delivery", label: "宅配" },
  { value: "seven_eleven", label: "7-ELEVEN" },
  { value: "family_mart", label: "全家" },
  { value: "hilife", label: "萊爾富" },
];

export default function CheckoutScreen() {
  const { order_id: draftOrderId } = useLocalSearchParams<{
    order_id?: string;
  }>();
  const { user } = useAuth();
  const { items, clear } = useCart();
  const [email, setEmail] = useState(user?.email ?? "");
  const [carrier, setCarrier] = useState<InvoiceCarrierType>("ecpay");
  const [barcode, setBarcode] = useState("/");
  const [fulfillmentMethod, setFulfillmentMethod] =
    useState<FulfillmentMethod>("cooperative_pickup");
  const [logisticsProvider, setLogisticsProvider] =
    useState<LogisticsProvider>("home_delivery");
  const [recipientName, setRecipientName] = useState(user?.display_name ?? "");
  const [recipientPhone, setRecipientPhone] = useState("");
  const [deliveryAddress, setDeliveryAddress] = useState("");
  const [submittedOrderId, setSubmittedOrderId] = useState<string | null>(
    draftOrderId ?? null,
  );
  const quote = useQuery({
    queryKey: ["quote", items],
    queryFn: () => api.quote(items),
    enabled: items.length > 0 && !submittedOrderId,
  });
  const products = useQuery({
    queryKey: ["products"],
    queryFn: api.products,
  });
  const rates = useQuery({
    queryKey: ["shipping-rates"],
    queryFn: api.shippingRates,
  });
  const draftOrder = useQuery({
    queryKey: ["order", submittedOrderId],
    queryFn: () => api.order(submittedOrderId!),
    enabled: Boolean(user && submittedOrderId),
  });
  const validDraftOrder =
    draftOrder.data?.order_kind === "regular" ? draftOrder.data : null;
  const activeItems = validDraftOrder?.items ?? quote.data?.items ?? [];

  useEffect(() => {
    const method = validDraftOrder?.fulfillment?.method;
    if (
      validDraftOrder &&
      (method === "cooperative_pickup" || method === "ecpay_logistics")
    ) {
      setFulfillmentMethod(method);
    }
    if (validDraftOrder?.contact_email) {
      setEmail(validDraftOrder.contact_email);
    }
    if (validDraftOrder?.invoice_carrier_type) {
      setCarrier(validDraftOrder.invoice_carrier_type);
    }
    if (validDraftOrder?.shipment?.logistics_provider) {
      setLogisticsProvider(validDraftOrder.shipment.logistics_provider);
    }
  }, [draftOrder.data]);

  const submit = useMutation({
    mutationFn: async () => {
      if (!user) throw new Error("請先登入");
      if (!submittedOrderId && carrier === "mobile_barcode") {
        const result = await api.validateMobileBarcode(barcode);
        if (!result.valid) throw new Error(result.message ?? "手機條碼格式不正確");
      }
      const order = submittedOrderId
        ? draftOrder.data ?? (await api.order(submittedOrderId))
        : await api.createOrder({
            items,
            contact_email: email,
            fulfillment_method: fulfillmentMethod,
            invoice_carrier_type: carrier,
            ...(carrier === "mobile_barcode"
              ? { invoice_carrier_value: barcode }
              : {}),
          });
      if (order.order_kind !== "regular") {
        throw new Error("這個待續訂單不屬於一般購物車，請回訂單列表處理");
      }
      if (!submittedOrderId) {
        setSubmittedOrderId(order.id);
        router.setParams({ order_id: order.id });
      }

      // Pickup can go straight to payment. Shipping has to visit ECPay's
      // picker first, because that is what fixes the address and the fee.
      const orderFulfillmentMethod =
        order.fulfillment?.method ?? fulfillmentMethod;
      if (orderFulfillmentMethod !== "ecpay_logistics") {
        const payment = await api.createPaymentAttempt(order.id);
        return { order, payment, selection: null };
      }
      const selection = await api.createLogisticsSelection(order.id, {
        channel: logisticsProvider,
        temperature: cartTemperature,
        recipient_name: recipientName.trim(),
        recipient_phone: recipientPhone.trim(),
        shipping_address: deliveryAddress.trim(),
      });
      return { order, payment: null, selection };
    },
    onSuccess: async ({ order, payment, selection }) => {
      setSubmittedOrderId(order.id);
      clear();
      if (selection?.selection_url) {
        await openLogisticsPage(selection.selection_url);
        if (Platform.OS === "web") return;
        router.replace({ pathname: "/order/[id]", params: { id: order.id } });
        return;
      }
      if (payment?.payment_url) {
        await openPaymentPage(payment.payment_url);
        if (Platform.OS === "web") return;
      }
      router.replace({
        pathname: "/order/[id]",
        params: { id: order.id },
      });
    },
  });

  useEffect(() => {
    if (items.length || submittedOrderId || submit.isPending) return;
    router.replace("/(tabs)/cart");
  }, [items.length, submittedOrderId, submit.isPending]);

  if (submittedOrderId && draftOrder.isLoading) {
    return <LoadingState label="正在開啟訂單" />;
  }
  if (submittedOrderId && (draftOrder.isError || !validDraftOrder)) {
    return (
      <Screen>
        <PageHeader onBack={() => router.back()} title="確認結帳" />
        <EmptyState
          action="回訂單列表"
          description="無法讀取這筆待續的一般訂單，請從訂單列表重新確認。"
          onAction={() => router.replace("/(tabs)/orders")}
          title="訂單恢復失敗"
        />
      </Screen>
    );
  }
  if (!items.length && !submittedOrderId) {
    return <LoadingState label="正在開啟購物車" />;
  }
  if (!submittedOrderId && quote.isError) {
    return (
      <Screen>
        <PageHeader onBack={() => router.back()} title="確認結帳" />
        <EmptyState
          action="重新計價"
          description="目前無法確認庫存與實際售價，尚未建立訂單。"
          onAction={() => quote.refetch()}
          title="訂單金額載入失敗"
        />
      </Screen>
    );
  }
  const productAmount = activeItems.reduce(
    (total, item) => total + item.subtotal,
    0,
  );
  const activeProductIds = new Set(
    activeItems
      .map((item) => item.product_id)
      .filter((productId): productId is string => Boolean(productId)),
  );
  const cartProducts = (products.data ?? []).filter((product) =>
    activeProductIds.has(product.id),
  );
  const temperatureZones = new Set(
    cartProducts.map((product) => product.temperature_zone ?? "ambient"),
  );
  const incompatibleTemperature = temperatureZones.size > 1;
  const cartTemperature: TemperatureZone =
    (Array.from(temperatureZones)[0] as TemperatureZone) ?? "ambient";
  const providers = logisticsOptions.map((option) => option.value);
  const availableProviders = providers.filter((provider) =>
    cartProducts.every(
      (product) =>
        product.is_shippable !== false &&
        (product.allowed_logistics ?? providers).includes(provider),
    ),
  );
  // Fees come from the API's rate table, not from constants in the App.
  const rateFor = (provider: LogisticsProvider) =>
    (rates.data ?? []).find(
      (rate) =>
        rate.channel === provider && rate.temperature === cartTemperature,
    );
  const feeFor = (provider: LogisticsProvider) => {
    const rate = rateFor(provider);
    if (!rate) return null;
    return productAmount >= rate.free_shipping_threshold ? 0 : rate.fee;
  };
  const selectedFee = feeFor(logisticsProvider);
  const shippingFee =
    fulfillmentMethod === "ecpay_logistics"
      ? (validDraftOrder?.shipment?.shipping_fee ?? selectedFee ?? 0)
      : 0;
  const missingRate =
    fulfillmentMethod === "ecpay_logistics" && selectedFee === null;
  const payableAmount = validDraftOrder?.shipment
    ? validDraftOrder.amount_total
    : productAmount + shippingFee;
  const selectedProviderLabel =
    logisticsOptions.find((option) => option.value === logisticsProvider)
      ?.label ?? "所選物流";
  const unsupportedProductNames = cartProducts
    .filter(
      (product) =>
        product.is_shippable === false ||
        !(product.allowed_logistics ?? providers).includes(logisticsProvider),
    )
    .map((product) => product.name);
  const logisticsBlockers =
    fulfillmentMethod === "ecpay_logistics"
      ? getLogisticsBlockers({
          deliveryAddress,
          incompatibleTemperature,
          logisticsProviderLabel: selectedProviderLabel,
          missingRate,
          recipientName,
          recipientPhone,
          shippingDataError: products.isError || rates.isError,
          shippingDataLoading: products.isLoading || rates.isLoading,
          unsupportedProductNames,
        })
      : [];

  return (
    <Screen>
      <PageHeader
        onBack={() => router.back()}
        subtitle="選擇現場取貨或配送，再確認電子發票與付款資料。"
        title="確認結帳"
      />
      {!submittedOrderId && quote.isLoading ? (
        <LoadingState label="計算訂單金額" />
      ) : (
        <View style={styles.content}>
          <View style={styles.orderPanel}>
            <View style={styles.panelHeading}>
              <Text style={[styles.sectionTitle, styles.orderTitle]}>
                訂購內容
              </Text>
              <Text style={styles.identity}>
                {membershipLabel(
                  validDraftOrder?.membership_type_snapshot ??
                    user?.membership_type ??
                    "nonmember",
                )}價格
              </Text>
            </View>
            {activeItems.map((item, index) => (
              <View key={`${item.product_name}-${index}`} style={styles.item}>
                <View style={styles.itemCopy}>
                  <Text style={styles.itemName}>{item.product_name}</Text>
                  <Text style={styles.itemMeta}>
                    {money(item.unit_price)} × {item.quantity}
                  </Text>
                </View>
                <Text style={styles.subtotal}>{money(item.subtotal)}</Text>
              </View>
            ))}
            <View style={styles.rule} />
            {fulfillmentMethod === "ecpay_logistics" ? (
              <View style={styles.feeRow}>
                <Text style={styles.totalLabel}>運費</Text>
                <Text style={styles.subtotal}>
                  {shippingFee ? money(shippingFee) : "免運"}
                </Text>
              </View>
            ) : null}
            <View style={styles.totalRow}>
              <Text style={styles.totalLabel}>付款金額</Text>
              <Text style={styles.total}>{money(payableAmount)}</Text>
            </View>
          </View>

          <View style={styles.panel}>
            <Text style={styles.sectionTitle}>取貨方式</Text>
            {[
              {
                value: "cooperative_pickup" as const,
                title: "合作社現場取貨",
                hint: "可取貨時透過 App 與 Email 通知",
              },
              {
                value: "ecpay_logistics" as const,
                title: "綠界物流配送",
                hint: "預先付款，不使用取貨付款",
              },
            ].map((option) => (
              <Pressable
                accessibilityRole="radio"
                accessibilityState={{
                  disabled: Boolean(submittedOrderId),
                  selected: fulfillmentMethod === option.value,
                }}
                disabled={Boolean(submittedOrderId)}
                key={option.value}
                onPress={() => setFulfillmentMethod(option.value)}
                style={[
                  styles.fulfillmentChoice,
                  fulfillmentMethod === option.value &&
                    styles.fulfillmentChoiceSelected,
                  submittedOrderId && styles.choiceLocked,
                ]}
              >
                <View
                  style={[
                    styles.radio,
                    fulfillmentMethod === option.value && styles.radioSelected,
                  ]}
                />
                <View style={styles.choiceCopy}>
                  <Text style={styles.lineTitle}>{option.title}</Text>
                  <Text style={styles.lineHint}>{option.hint}</Text>
                </View>
              </Pressable>
            ))}
            {fulfillmentMethod === "ecpay_logistics" ? (
              <>
                <Text style={styles.fieldLabel}>配送通路</Text>
                <View style={styles.logisticsGrid}>
                  {logisticsOptions.map((provider) => {
                    const fee = feeFor(provider.value);
                    const disabled =
                      !availableProviders.includes(provider.value) ||
                      fee === null;
                    const selected = logisticsProvider === provider.value;
                    return (
                      <Pressable
                        accessibilityLabel={`選擇${provider.label}配送`}
                        accessibilityRole="radio"
                        accessibilityState={{
                          disabled,
                          selected,
                        }}
                        disabled={disabled}
                        key={provider.value}
                        onPress={() => setLogisticsProvider(provider.value)}
                        style={[
                          styles.logisticsChoice,
                          selected && !disabled && styles.logisticsChoiceSelected,
                          disabled && styles.logisticsChoiceDisabled,
                        ]}
                      >
                        <Text
                          style={[
                            styles.logisticsLabel,
                            selected && styles.logisticsLabelSelected,
                          ]}
                        >
                          {provider.label}
                          {fee === null
                            ? "（不適用）"
                            : fee === 0
                              ? " 免運"
                              : ` ${money(fee)}`}
                        </Text>
                      </Pressable>
                    );
                  })}
                </View>
                {incompatibleTemperature ? (
                  <InlineMessage
                    text="購物車含不同溫層商品，物流訂單需分開結帳；仍可改選現場取貨。"
                    tone="danger"
                  />
                ) : null}
                {missingRate ? (
                  <InlineMessage
                    text="此通路目前沒有適用的運費費率，請改選其他通路或現場取貨。"
                    tone="danger"
                  />
                ) : null}
                {products.isError || rates.isError ? (
                  <>
                    <InlineMessage
                      text="配送商品或運費資料載入失敗，重新載入前無法建立物流訂單。"
                      tone="danger"
                    />
                    <Button
                      compact
                      label="重新載入配送資料"
                      onPress={() => {
                        products.refetch();
                        rates.refetch();
                      }}
                      variant="secondary"
                    />
                  </>
                ) : null}
                {logisticsBlockers.length ? (
                  <InlineMessage
                    text={`目前無法進入下一步：${logisticsBlockers.join("；")}`}
                    tone="danger"
                  />
                ) : null}
                <Text style={styles.shippingHint}>
                  {rateFor(logisticsProvider)
                    ? `商品滿 ${money(
                        rateFor(logisticsProvider)!.free_shipping_threshold,
                      )} 免運；同筆訂單只使用單一地址與溫層。`
                    : "同筆訂單只使用單一地址與溫層。"}
                </Text>
                <Text style={styles.fieldLabel}>收件人姓名</Text>
                <TextInput
                  onChangeText={setRecipientName}
                  placeholder="與證件相同的姓名"
                  placeholderTextColor={colors.sage}
                  style={styles.input}
                  value={recipientName}
                />
                <Text style={styles.fieldLabel}>收件人電話</Text>
                <TextInput
                  keyboardType="phone-pad"
                  onChangeText={setRecipientPhone}
                  placeholder="09xxxxxxxx 或市話"
                  placeholderTextColor={colors.sage}
                  style={styles.input}
                  value={recipientPhone}
                />
                <Text style={styles.fieldLabel}>
                  {logisticsProvider === "home_delivery"
                    ? "配送地址"
                    : "取貨地區（下一步在綠界選門市）"}
                </Text>
                <TextInput
                  onChangeText={setDeliveryAddress}
                  placeholder={
                    logisticsProvider === "home_delivery"
                      ? "輸入完整宅配地址"
                      : "輸入希望取貨的地區，例如：高雄市三民區"
                  }
                  placeholderTextColor={colors.sage}
                  style={styles.input}
                  value={deliveryAddress}
                />
                <Text style={styles.shippingHint}>
                  下一步會前往綠界物流頁面
                  {logisticsProvider === "home_delivery"
                    ? "確認配送資料"
                    : "選擇取貨門市"}
                  ，完成後回到訂單再付款。
                </Text>
              </>
            ) : null}
          </View>

          <View style={styles.panel}>
            <Text style={styles.sectionTitle}>電子發票</Text>
            <Text style={styles.fieldLabel}>發票通知 Email</Text>
            <TextInput
              autoCapitalize="none"
              editable={!submittedOrderId}
              keyboardType="email-address"
              onChangeText={setEmail}
              placeholder="name@example.com"
              placeholderTextColor={colors.sage}
              style={styles.input}
              value={email}
            />
            <Text style={styles.fieldLabel}>選擇載具</Text>
            <View style={styles.carriers}>
              {[
                { value: "ecpay" as const, label: "綠界載具＋Email" },
                { value: "mobile_barcode" as const, label: "手機條碼" },
              ].map((option) => (
                <Pressable
                  accessibilityRole="radio"
                  accessibilityState={{
                    disabled: Boolean(submittedOrderId),
                    selected: carrier === option.value,
                  }}
                  disabled={Boolean(submittedOrderId)}
                  key={option.value}
                  onPress={() => setCarrier(option.value)}
                  style={[
                    styles.carrier,
                    carrier === option.value && styles.carrierSelected,
                    submittedOrderId && styles.choiceLocked,
                  ]}
                >
                  <View
                    style={[
                      styles.radio,
                      carrier === option.value && styles.radioSelected,
                    ]}
                  />
                  <Text style={styles.carrierText}>{option.label}</Text>
                </Pressable>
              ))}
            </View>
            {carrier === "mobile_barcode" ? (
              <TextInput
                autoCapitalize="characters"
                editable={!submittedOrderId}
                onChangeText={setBarcode}
                placeholder="/ABC+123"
                placeholderTextColor={colors.sage}
                style={styles.input}
                value={barcode}
              />
            ) : null}
            <Text style={styles.invoiceHint}>
              完成取貨後開立 B2C 電子發票，不支援公司統編。
            </Text>
          </View>

          {submit.error ? (
            <InlineMessage
              text={getErrorMessage(submit.error)}
              tone="danger"
            />
          ) : null}
          {submittedOrderId ? (
            <InlineMessage text="訂單已建立；商品、取貨方式與發票資料已鎖定，本次只會重試同一筆付款或物流流程。" />
          ) : null}
          <Button
            disabled={
              draftOrder.isLoading ||
              activeItems.length === 0 ||
              (submittedOrderId ? !validDraftOrder : !quote.data) ||
              !email.includes("@") ||
              (fulfillmentMethod === "ecpay_logistics" &&
                logisticsBlockers.length > 0)
            }
            icon={
              fulfillmentMethod === "ecpay_logistics"
                ? "cube-outline"
                : "card-outline"
            }
            label={
              fulfillmentMethod === "ecpay_logistics"
                ? "下一步：選擇物流"
                : `前往付款 ${money(payableAmount)}`
            }
            loading={submit.isPending}
            onPress={() => submit.mutate()}
          />
          {fulfillmentMethod === "ecpay_logistics" ? (
            <Text style={styles.shippingHint}>
              預估應付 {money(payableAmount)}（含運費 {money(shippingFee)}）；
              實際金額以綠界物流選擇完成後的訂單為準。
            </Text>
          ) : null}
        </View>
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  content: { gap: 13, padding: spacing.md },
  orderPanel: {
    backgroundColor: colors.forest,
    borderRadius: radii.lg,
    padding: spacing.md,
  },
  panelHeading: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
  },
  sectionTitle: { color: colors.forest, fontSize: 17, fontWeight: "900" },
  orderTitle: { color: colors.white },
  identity: { color: "#D7E2DA", fontSize: 13 },
  item: {
    alignItems: "center",
    flexDirection: "row",
    paddingTop: 13,
  },
  itemCopy: { flex: 1 },
  itemName: { color: colors.white, fontSize: 14, fontWeight: "800" },
  itemMeta: { color: "#C8D5CC", fontSize: 13, marginTop: 3 },
  subtotal: { color: colors.white, fontSize: 14, fontWeight: "900" },
  rule: { backgroundColor: "#49695E", height: 1, marginVertical: 14 },
  totalRow: {
    alignItems: "baseline",
    flexDirection: "row",
    justifyContent: "space-between",
  },
  feeRow: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    marginBottom: 8,
  },
  totalLabel: { color: "#C8D5CC", fontSize: 13 },
  total: { color: "#EEC8A4", fontSize: 27, fontWeight: "900" },
  panel: {
    backgroundColor: colors.paper,
    borderRadius: radii.md,
    gap: 10,
    padding: spacing.md,
  },
  fulfillmentChoice: {
    alignItems: "center",
    borderColor: colors.line,
    borderRadius: radii.md,
    borderWidth: 1,
    flexDirection: "row",
    gap: 10,
    minHeight: 56,
    padding: 11,
  },
  fulfillmentChoiceSelected: {
    backgroundColor: colors.sageLight,
    borderColor: colors.sage,
  },
  choiceLocked: { opacity: 0.55 },
  choiceCopy: { flex: 1 },
  lineTitle: { color: colors.forest, fontSize: 14, fontWeight: "900" },
  lineHint: { color: colors.muted, fontSize: 13, marginTop: 3 },
  logisticsGrid: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  logisticsChoice: {
    alignItems: "center",
    borderColor: colors.line,
    borderRadius: radii.md,
    borderWidth: 1,
    justifyContent: "center",
    minHeight: 44,
    width: "48.8%",
  },
  logisticsChoiceSelected: {
    backgroundColor: colors.forest,
    borderColor: colors.forest,
  },
  logisticsChoiceDisabled: { opacity: 0.38 },
  logisticsLabel: { color: colors.forest, fontSize: 14, fontWeight: "800" },
  logisticsLabelSelected: { color: colors.white },
  shippingHint: { color: colors.muted, fontSize: 13, lineHeight: 18 },
  fieldLabel: {
    color: colors.forest,
    fontSize: 13,
    fontWeight: "800",
    marginTop: 3,
  },
  input: {
    borderColor: colors.line,
    borderRadius: radii.sm,
    borderWidth: 1,
    color: colors.charcoal,
    fontSize: 13,
    minHeight: 45,
    paddingHorizontal: 12,
  },
  carriers: { flexDirection: "row", gap: 8 },
  carrier: {
    alignItems: "center",
    borderColor: colors.line,
    borderRadius: radii.sm,
    borderWidth: 1,
    flex: 1,
    flexDirection: "row",
    gap: 7,
    minHeight: 48,
    padding: 10,
  },
  carrierSelected: {
    backgroundColor: colors.sageLight,
    borderColor: colors.sage,
  },
  radio: {
    borderColor: colors.sage,
    borderRadius: 7,
    borderWidth: 1.5,
    height: 14,
    width: 14,
  },
  radioSelected: {
    backgroundColor: colors.forest,
    borderColor: colors.forest,
  },
  carrierText: { color: colors.forest, fontSize: 14, fontWeight: "800" },
  invoiceHint: { color: colors.muted, fontSize: 13, lineHeight: 14 },
});
