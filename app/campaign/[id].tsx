import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { router, useLocalSearchParams } from "expo-router";
import { useEffect, useState } from "react";
import {
  Image,
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
  InfoRow,
  InlineMessage,
  LoadingState,
  PageHeader,
  ProgressBar,
  QuantityControl,
  Screen,
  StatusPill,
} from "../../src/components/ui";
import {
  campaignLabels,
  dateTime,
  money,
} from "../../src/lib/format";
import { imageFor } from "../../src/lib/images";
import { getLogisticsBlockers } from "../../src/lib/checkoutValidation";
import { hasMemberPricing } from "../../src/lib/membership";
import {
  openLogisticsPage,
  openPaymentPage,
} from "../../src/lib/payment";
import { api, getErrorMessage } from "../../src/services/api";
import { useAuth } from "../../src/store/AuthContext";
import { colors, radii, spacing } from "../../src/theme";
import type {
  FulfillmentMethod,
  InvoiceCarrierType,
  LogisticsProvider,
} from "../../src/types";

export default function CampaignDetailScreen() {
  const { id, order_id: draftOrderId } = useLocalSearchParams<{
    id: string;
    order_id?: string;
  }>();
  const queryClient = useQueryClient();
  const { user, isAuthenticated } = useAuth();
  const [quantity, setQuantity] = useState(1);
  const [email, setEmail] = useState(user?.email ?? "");
  const [carrier, setCarrier] = useState<InvoiceCarrierType>("ecpay");
  const [barcode, setBarcode] = useState("/");
  const [fulfillmentMethod, setFulfillmentMethod] =
    useState<FulfillmentMethod>("cooperative_pickup");
  const [logisticsProvider, setLogisticsProvider] =
    useState<LogisticsProvider>("home_delivery");
  const [deliveryAddress, setDeliveryAddress] = useState("");
  const [recipientName, setRecipientName] = useState(user?.display_name ?? "");
  const [recipientPhone, setRecipientPhone] = useState("");
  const [submittedOrderId, setSubmittedOrderId] = useState<string | null>(
    draftOrderId ?? null,
  );
  const query = useQuery({
    queryKey: ["campaign", id],
    queryFn: () => api.campaign(id),
    refetchInterval: 30000,
  });
  const rates = useQuery({
    queryKey: ["shipping-rates"],
    queryFn: api.shippingRates,
  });
  const draftOrder = useQuery({
    queryKey: ["order", submittedOrderId],
    queryFn: () => api.order(submittedOrderId!),
    enabled: isAuthenticated && Boolean(submittedOrderId),
  });

  useEffect(() => {
    const order = draftOrder.data;
    const method = order?.fulfillment?.method;
    if (order?.order_kind !== "group" || order.group_campaign_id !== id) return;
    setQuantity(order.items[0]?.quantity ?? 1);
    if (method === "cooperative_pickup" || method === "ecpay_logistics") {
      setFulfillmentMethod(method);
    }
  }, [draftOrder.data, id]);
  const campaignTemperature = query.data?.temperature_zone ?? "ambient";
  const quote = useQuery({
    queryKey: [
      "campaign-quote",
      id,
      quantity,
      fulfillmentMethod,
      logisticsProvider,
    ],
    queryFn: () =>
      api.quoteCampaign(id, {
        quantity,
        fulfillment_method: fulfillmentMethod,
        ...(fulfillmentMethod === "ecpay_logistics"
          ? { shipping_channel: logisticsProvider }
          : {}),
      }),
    enabled: isAuthenticated && Boolean(query.data) && !submittedOrderId,
    retry: false,
  });

  useEffect(() => {
    const campaign = query.data;
    if (
      campaign?.can_ship &&
      campaign.allowed_logistics.length > 0 &&
      !campaign.allowed_logistics.includes(logisticsProvider)
    ) {
      setLogisticsProvider(campaign.allowed_logistics[0]!);
    }
  }, [logisticsProvider, query.data]);

  const join = useMutation({
    mutationFn: async () => {
      if (!submittedOrderId && carrier === "mobile_barcode") {
        const result = await api.validateMobileBarcode(barcode);
        if (!result.valid) throw new Error(result.message ?? "手機條碼格式不正確");
      }
      const order = submittedOrderId
        ? draftOrder.data ?? (await api.order(submittedOrderId))
        : await api.joinCampaign(id, {
            quantity,
            contact_email: email,
            invoice_carrier_type: carrier,
            fulfillment_method: fulfillmentMethod,
            ...(fulfillmentMethod === "ecpay_logistics"
              ? { shipping_channel: logisticsProvider }
              : {}),
            ...(carrier === "mobile_barcode"
              ? { invoice_carrier_value: barcode }
              : {}),
          });
      if (order.order_kind !== "group" || order.group_campaign_id !== id) {
        throw new Error("這個待續訂單不屬於目前團購，請回訂單列表處理");
      }
      if (!submittedOrderId) {
        setSubmittedOrderId(order.id);
        router.setParams({ order_id: order.id });
      }
      const orderFulfillmentMethod =
        order.fulfillment?.method ?? fulfillmentMethod;
      if (orderFulfillmentMethod === "ecpay_logistics") {
        const selection = await api.createLogisticsSelection(order.id, {
          channel: logisticsProvider,
          temperature: campaignTemperature,
          recipient_name: recipientName.trim(),
          recipient_phone: recipientPhone.trim(),
          shipping_address: deliveryAddress.trim(),
        });
        return { order, payment: null, selection };
      }
      const payment = await api.createPaymentAttempt(order.id);
      return { order, payment, selection: null };
    },
    onSuccess: async ({ order, payment, selection }) => {
      setSubmittedOrderId(order.id);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["campaigns"] }),
        queryClient.invalidateQueries({ queryKey: ["campaign", id] }),
        queryClient.invalidateQueries({ queryKey: ["orders"] }),
      ]);
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

  if (query.isLoading) return <LoadingState label="載入團購資料" />;
  if (query.isError || !query.data) {
    return (
      <Screen>
        <PageHeader onBack={() => router.back()} title="團購詳情" />
        <EmptyState
          action={query.isError ? "重新載入" : undefined}
          description={
            query.isError
              ? "目前無法取得團購資料，尚未建立任何訂單。"
              : "這個共同購買可能已經結束。"
          }
          onAction={query.isError ? () => query.refetch() : undefined}
          title={query.isError ? "團購載入失敗" : "找不到團購"}
        />
      </Screen>
    );
  }

  const campaign = query.data;
  const validDraftOrder =
    draftOrder.data?.order_kind === "group" &&
    draftOrder.data.group_campaign_id === id
      ? draftOrder.data
      : null;
  const draftProductSubtotal =
    validDraftOrder?.items.reduce((sum, item) => sum + item.subtotal, 0) ?? 0;
  const activeQuote =
    quote.data ??
    (validDraftOrder
      ? {
          amount_total: validDraftOrder.amount_total,
          membership_type: validDraftOrder.membership_type_snapshot,
          product_subtotal: draftProductSubtotal,
          quantity: validDraftOrder.items[0]?.quantity ?? 1,
          shipping_fee: Math.max(
            validDraftOrder.amount_total - draftProductSubtotal,
            0,
          ),
          unit_price: validDraftOrder.items[0]?.unit_price ?? 0,
        }
      : null);
  const availableProviders = campaign.can_ship
    ? campaign.allowed_logistics
    : [];
  const selectedRate = (rates.data ?? []).find(
    (rate) =>
      rate.channel === logisticsProvider &&
      rate.temperature === campaignTemperature,
  );
  const selectedProviderLabel =
    {
      home_delivery: "宅配",
      seven_eleven: "7-ELEVEN",
      family_mart: "全家",
      hilife: "萊爾富",
    }[logisticsProvider] ?? "所選物流";
  const logisticsBlockers =
    fulfillmentMethod === "ecpay_logistics"
      ? getLogisticsBlockers({
          deliveryAddress,
          incompatibleTemperature: false,
          logisticsProviderLabel: selectedProviderLabel,
          missingRate: !rates.isLoading && !rates.isError && !selectedRate,
          recipientName,
          recipientPhone,
          shippingDataError: rates.isError,
          shippingDataLoading: rates.isLoading,
          unsupportedProductNames: availableProviders.includes(logisticsProvider)
            ? []
            : [campaign.title],
        })
      : [];
  const membership =
    activeQuote?.membership_type ?? user?.membership_type ?? "nonmember";
  const memberPricing = hasMemberPricing(membership);
  const unitPrice =
    activeQuote?.unit_price ??
    (memberPricing ? campaign.member_price : campaign.nonmember_price);
  const maxQuantity = Math.max(
    quantity,
    Math.min(campaign.per_user_cap, campaign.available_quantity),
  );
  const isOpen =
    campaign.intake_status === "open" &&
    campaign.available_quantity > 0 &&
    Date.now() < Date.parse(campaign.deadline);
  const progress = Math.min(
    100,
    (campaign.paid_quantity / campaign.min_paid_quantity) * 100,
  );

  return (
    <Screen>
      <PageHeader onBack={() => router.back()} title="團購詳情" />
      <Image
        source={imageFor(campaign.image_key, campaign.image_url)}
        style={styles.image}
      />
      <View style={styles.content}>
        <View style={styles.statusRow}>
          <StatusPill
            label={campaignLabels[campaign.decision_status]}
            tone={
              campaign.decision_status === "confirmed"
                ? "positive"
                : "warning"
            }
          />
          <Text style={styles.deadline}>{dateTime(campaign.deadline)} 截止</Text>
        </View>
        <Text style={styles.title}>{campaign.title}</Text>
        <Text style={styles.description}>{campaign.description}</Text>

        <View style={styles.pricePanel}>
          <View>
            <Text style={styles.priceLabel}>
              {memberPricing ? "社員團購價" : "非社員團購價"}
            </Text>
            <Text style={styles.price}>{money(unitPrice)}</Text>
          </View>
          <Text style={styles.otherPrice}>
            {memberPricing
              ? `非社員 ${money(campaign.nonmember_price)}`
              : `社員 ${money(campaign.member_price)}`}
          </Text>
        </View>

        <View style={styles.progressCard}>
          <View style={styles.progressTop}>
            <View>
              <Text style={styles.progressValue}>
                {campaign.paid_quantity} 組
              </Text>
              <Text style={styles.progressLabel}>已完成付款</Text>
            </View>
            <View style={styles.progressRight}>
              <Text style={styles.progressValue}>
                {campaign.min_paid_quantity} 組
              </Text>
              <Text style={styles.progressLabel}>成團門檻</Text>
            </View>
          </View>
          <ProgressBar value={progress} />
          <Text style={styles.remaining}>
            尚可加入 {campaign.available_quantity} 組・每人最多{" "}
            {campaign.per_user_cap} 組
          </Text>
        </View>

        <View style={styles.infoPanel}>
          <InfoRow
            icon="storefront-outline"
            label="取貨方式"
            value={
              campaign.can_ship
                ? "合作社現場取貨／綠界物流配送"
                : "合作社現場取貨"
            }
          />
          <View style={styles.rule} />
          <InfoRow
            icon="calendar-outline"
            label={
              campaign.final_pickup_at ? "最終取貨時間" : "預估取貨區間"
            }
            value={
              campaign.final_pickup_at
                ? dateTime(campaign.final_pickup_at)
                : `${dateTime(campaign.estimated_pickup_start)} 至 ${dateTime(
                    campaign.estimated_pickup_end,
                  )}`
            }
          />
        </View>

        {isOpen || Boolean(submittedOrderId) ? (
          isAuthenticated ? (
            <View style={styles.joinPanel}>
              <Text style={styles.sectionTitle}>加入共同購買</Text>
              <View style={styles.quantityRow}>
                <View>
                  <Text style={styles.fieldLabel}>數量</Text>
                  <Text style={styles.fieldHint}>付款成功才計入門檻</Text>
                </View>
                <QuantityControl
                  disabled={Boolean(submittedOrderId)}
                  max={maxQuantity}
                  onChange={setQuantity}
                  value={quantity}
                />
              </View>

              <Text style={styles.fieldLabel}>履約方式</Text>
              <View style={styles.carriers}>
                {[
                  {
                    value: "cooperative_pickup" as const,
                    label: "合作社取貨",
                  },
                  ...(campaign.can_ship
                    ? [{ value: "ecpay_logistics" as const, label: "綠界物流" }]
                    : []),
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
                      styles.carrier,
                      fulfillmentMethod === option.value &&
                        styles.carrierSelected,
                      submittedOrderId && styles.choiceLocked,
                    ]}
                  >
                    <View
                      style={[
                        styles.radio,
                        fulfillmentMethod === option.value &&
                          styles.radioSelected,
                      ]}
                    />
                    <Text style={styles.carrierLabel}>{option.label}</Text>
                  </Pressable>
                ))}
              </View>
              {fulfillmentMethod === "ecpay_logistics" ? (
                <>
                  <Text style={styles.fieldLabel}>物流通路</Text>
                  <View style={styles.providerWrap}>
                    {[
                      { value: "home_delivery" as const, label: "宅配" },
                      { value: "seven_eleven" as const, label: "7-ELEVEN" },
                      { value: "family_mart" as const, label: "全家" },
                      { value: "hilife" as const, label: "萊爾富" },
                    ]
                      .filter((provider) =>
                        availableProviders.includes(provider.value),
                      )
                      .map((provider) => (
                      <Pressable
                        accessibilityRole="radio"
                        accessibilityState={{
                          selected: logisticsProvider === provider.value,
                        }}
                        key={provider.value}
                        onPress={() => setLogisticsProvider(provider.value)}
                        style={[
                          styles.provider,
                          logisticsProvider === provider.value &&
                            styles.providerSelected,
                        ]}
                      >
                        <Text
                          style={[
                            styles.providerLabel,
                            logisticsProvider === provider.value &&
                              styles.providerLabelSelected,
                          ]}
                        >
                          {provider.label}
                        </Text>
                      </Pressable>
                    ))}
                  </View>
                  <Text style={styles.fieldLabel}>收件人姓名</Text>
                  <TextInput
                    onChangeText={setRecipientName}
                    placeholder="收件人姓名"
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
                  <TextInput
                    onChangeText={setDeliveryAddress}
                    placeholder="配送地址或門市"
                    placeholderTextColor={colors.sage}
                    style={styles.input}
                    value={deliveryAddress}
                  />
                  <Text style={styles.fieldHint}>
                    {selectedRate
                      ? `滿 ${money(selectedRate.free_shipping_threshold)} 免運；團購失敗時商品與運費一併退款。`
                      : "目前找不到適用運費，請改選合作社取貨。"}
                  </Text>
                  {rates.isError ? (
                    <>
                      <InlineMessage
                        text="運費資料載入失敗，重新載入前無法建立物流訂單。"
                        tone="danger"
                      />
                      <Button
                        compact
                        label="重新載入運費"
                        onPress={() => rates.refetch()}
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
                </>
              ) : null}

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

              <Text style={styles.fieldLabel}>電子發票載具</Text>
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
                    <Text style={styles.carrierLabel}>{option.label}</Text>
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

              {join.error ? (
                <InlineMessage
                  text={getErrorMessage(join.error)}
                  tone="danger"
                />
              ) : null}
              {!submittedOrderId && quote.isError ? (
                <InlineMessage
                  text="後端目前無法完成報價，重新計價前不會建立訂單。"
                  tone="danger"
                />
              ) : null}
              {submittedOrderId && (draftOrder.isError || !validDraftOrder) ? (
                <InlineMessage
                  text="無法讀取這筆待續團購訂單，請回訂單列表確認或取消後重新加入。"
                  tone="danger"
                />
              ) : null}
              {submittedOrderId ? (
                <InlineMessage text="團購訂單已建立；數量、履約與發票資料已鎖定，本次只會重試同一筆付款或物流流程。" />
              ) : null}
              <View style={styles.totalRow}>
                <Text style={styles.totalLabel}>
                  {submittedOrderId ? "既有訂單" : "後端報價"}
                </Text>
                <Text style={styles.total}>
                  {activeQuote ? money(activeQuote.amount_total) : "計價中"}
                </Text>
              </View>
              {activeQuote ? (
                <Text style={styles.fieldHint}>
                  商品 {money(activeQuote.product_subtotal)}
                  {activeQuote.shipping_fee > 0
                    ? `＋運費 ${money(activeQuote.shipping_fee)}`
                    : fulfillmentMethod === "ecpay_logistics"
                      ? submittedOrderId && !validDraftOrder?.shipment
                        ? "；物流完成後確認運費"
                        : "＋免運"
                      : ""}
                  {submittedOrderId
                    ? "；重試不會建立第二筆訂單。"
                    : "；建單時會再次驗證社員資格、名額與費率。"}
                </Text>
              ) : null}
              <Button
                disabled={
                  (submittedOrderId
                    ? draftOrder.isLoading || !validDraftOrder
                    : quote.isLoading || quote.isError || !quote.data) ||
                  !email.includes("@") ||
                  (fulfillmentMethod === "ecpay_logistics" &&
                    logisticsBlockers.length > 0)
                }
                icon="card-outline"
                label={
                  fulfillmentMethod === "ecpay_logistics"
                    ? "下一步：選擇物流"
                    : "確認並前往付款"
                }
                loading={join.isPending}
                onPress={() => join.mutate()}
              />
            </View>
          ) : (
            <Button
              label="登入後加入"
              onPress={() => router.push("/login")}
            />
          )
        ) : (
          <InlineMessage
            text={
              Date.now() >= Date.parse(campaign.deadline)
                ? "本團已截止，請留意後續成團與履約通知。"
                : campaign.intake_status === "full"
                ? "本團已額滿，暫不接受新的加入。"
                : "目前暫停加入，請留意後續成團與取貨通知。"
            }
          />
        )}
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  image: {
    aspectRatio: 3 / 2,
    borderRadius: radii.lg,
    marginHorizontal: spacing.md,
    width: "auto",
  },
  content: { gap: 15, padding: spacing.md },
  statusRow: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
  },
  deadline: { color: colors.muted, fontSize: 12 },
  title: { color: colors.forest, fontSize: 30, fontWeight: "900" },
  description: {
    color: colors.charcoal,
    fontSize: 13,
    lineHeight: 21,
  },
  pricePanel: {
    alignItems: "flex-end",
    backgroundColor: colors.orangeSoft,
    borderRadius: radii.md,
    flexDirection: "row",
    justifyContent: "space-between",
    padding: spacing.md,
  },
  priceLabel: { color: colors.danger, fontSize: 12, fontWeight: "800" },
  price: {
    color: colors.orange,
    fontSize: 31,
    fontWeight: "900",
    marginTop: 2,
  },
  otherPrice: { color: colors.muted, fontSize: 12 },
  progressCard: {
    backgroundColor: colors.paper,
    borderRadius: radii.md,
    padding: spacing.md,
  },
  progressTop: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginBottom: 12,
  },
  progressRight: { alignItems: "flex-end" },
  progressValue: { color: colors.forest, fontSize: 18, fontWeight: "900" },
  progressLabel: { color: colors.muted, fontSize: 12, marginTop: 2 },
  remaining: { color: colors.muted, fontSize: 12, marginTop: 9 },
  infoPanel: {
    backgroundColor: colors.paper,
    borderRadius: radii.md,
    gap: 12,
    padding: spacing.md,
  },
  rule: { backgroundColor: colors.line, height: 1 },
  joinPanel: {
    backgroundColor: colors.paper,
    borderRadius: radii.lg,
    gap: 11,
    padding: spacing.md,
  },
  sectionTitle: { color: colors.forest, fontSize: 19, fontWeight: "900" },
  quantityRow: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
  },
  fieldLabel: { color: colors.forest, fontSize: 12, fontWeight: "800" },
  fieldHint: { color: colors.muted, fontSize: 12, marginTop: 3 },
  input: {
    borderColor: colors.line,
    borderRadius: radii.sm,
    borderWidth: 1,
    color: colors.charcoal,
    fontSize: 13,
    minHeight: 46,
    paddingHorizontal: 12,
  },
  carriers: { flexDirection: "row", gap: 8 },
  choiceLocked: { opacity: 0.55 },
  providerWrap: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  provider: {
    alignItems: "center",
    borderColor: colors.line,
    borderRadius: radii.sm,
    borderWidth: 1,
    justifyContent: "center",
    minHeight: 44,
    width: "48.8%",
  },
  providerSelected: {
    backgroundColor: colors.forest,
    borderColor: colors.forest,
  },
  providerLabel: { color: colors.forest, fontSize: 12, fontWeight: "800" },
  providerLabelSelected: { color: colors.white },
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
  carrierLabel: { color: colors.forest, fontSize: 12, fontWeight: "800" },
  totalRow: {
    alignItems: "baseline",
    flexDirection: "row",
    justifyContent: "space-between",
  },
  totalLabel: { color: colors.muted, fontSize: 12 },
  total: { color: colors.forest, fontSize: 24, fontWeight: "900" },
});
