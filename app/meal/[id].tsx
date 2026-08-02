import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { router, useLocalSearchParams } from "expo-router";
import { useMemo, useState } from "react";
import { Platform, Pressable, StyleSheet, Text, TextInput, View } from "react-native";

import {
  Button,
  CatalogCard,
  EmptyState,
  InfoRow,
  InlineMessage,
  LoadingState,
  PageHeader,
  Screen,
} from "../../src/components/ui";
import { dateTime, money } from "../../src/lib/format";
import { openPaymentPage } from "../../src/lib/payment";
import { api, getErrorMessage } from "../../src/services/api";
import { useAuth } from "../../src/store/AuthContext";
import { colors, radii, spacing } from "../../src/theme";
import type { InvoiceCarrierType } from "../../src/types";

function isOrderingOpen(
  event: { status: string; sales_start: string; order_deadline: string },
  now = Date.now(),
) {
  return (
    event.status === "published" &&
    now >= Date.parse(event.sales_start) &&
    now < Date.parse(event.order_deadline)
  );
}

function orderingLabel(
  event: { status: string; sales_start: string; order_deadline: string },
  now = Date.now(),
) {
  if (event.status === "cancelled") return "場次已取消";
  if (event.status === "pickup_open") return "已進入取餐時間";
  if (event.status === "completed") return "場次已結束";
  if (event.status === "ordering_closed" || now >= Date.parse(event.order_deadline)) {
    return "預購已截止";
  }
  if (event.status !== "published" || now < Date.parse(event.sales_start)) {
    return "尚未開放預購";
  }
  return "確認預購";
}

export default function MealEventDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { isAuthenticated, user } = useAuth();
  const queryClient = useQueryClient();
  const [quantities, setQuantities] = useState<Record<string, number>>({});
  const [email, setEmail] = useState(user?.email ?? "");
  const [carrier, setCarrier] = useState<InvoiceCarrierType>("ecpay");
  const [barcode, setBarcode] = useState("/");
  const query = useQuery({
    queryKey: ["meal-event", id],
    queryFn: () => api.mealEvent(id),
    refetchInterval: 30000,
  });
  const total = useMemo(
    () =>
      (query.data?.items ?? []).reduce(
        (sum, item) => sum + item.price * (quantities[item.offering_id] ?? 0),
        0,
      ),
    [quantities, query.data?.items],
  );

  const createOrder = useMutation({
    mutationFn: async () => {
      if (!query.data || !isOrderingOpen(query.data)) {
        throw new Error("此場次目前未開放預購");
      }
      if (carrier === "mobile_barcode") {
        const result = await api.validateMobileBarcode(barcode);
        if (!result.valid) throw new Error(result.message ?? "手機條碼格式不正確");
      }
      const order = await api.createMealOrder(id, {
        items: Object.entries(quantities)
          .filter(([, quantity]) => quantity > 0)
          .map(([offering_id, quantity]) => ({ offering_id, quantity })),
        contact_email: email.trim(),
        invoice_carrier_type: carrier,
        ...(carrier === "mobile_barcode"
          ? { invoice_carrier_value: barcode.trim() }
          : {}),
      });
      try {
        const payment = await api.createPaymentAttempt(order.id);
        return { order, payment, setupFailed: false };
      } catch {
        return { order, payment: null, setupFailed: true };
      }
    },
    onSuccess: async ({ order, payment, setupFailed }) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["meal-events"] }),
        queryClient.invalidateQueries({ queryKey: ["meal-event", id] }),
        queryClient.invalidateQueries({ queryKey: ["meal-orders"] }),
      ]);
      if (setupFailed) {
        router.replace({
          pathname: "/meal-order/[id]",
          params: { id: order.id, setup: "retry" },
        });
        return;
      }
      if (payment?.payment_url) {
        await openPaymentPage(payment.payment_url);
        if (Platform.OS === "web") return;
      }
      router.replace({
        pathname: "/meal-order/[id]",
        params: { id: order.id },
      });
    },
  });

  if (query.isLoading) return <LoadingState label="載入便當菜單" />;
  if (!query.data) {
    return (
      <Screen>
        <PageHeader onBack={() => router.back()} title="便當場次" />
        <EmptyState description="這個場次可能已經結束。" title="找不到場次" />
      </Screen>
    );
  }

  const event = query.data;
  const orderingOpen = isOrderingOpen(event);

  return (
    <Screen
      bottom={
        <View style={styles.bottom}>
          <View>
            <Text style={styles.bottomLabel}>合計</Text>
            <Text style={styles.bottomTotal}>{money(total)}</Text>
          </View>
          <Button
            disabled={
              !orderingOpen ||
              total === 0 ||
              !email.includes("@") ||
              (carrier === "mobile_barcode" && barcode.length < 8)
            }
            label={
              isAuthenticated
                ? orderingLabel(event)
                : orderingOpen
                  ? "登入後預購"
                  : orderingLabel(event)
            }
            loading={createOrder.isPending}
            onPress={() =>
              isAuthenticated ? createOrder.mutate() : router.push("/login")
            }
          />
        </View>
      }
    >
      <PageHeader
        onBack={() => router.back()}
        subtitle={`${event.school_name ? `${event.school_name}，` : ""}${dateTime(event.order_deadline)} 截止`}
        title={event.title}
      />
      <View style={styles.info}>
        <InfoRow
          icon="location-outline"
          label="取餐地點"
          value={event.venue_name}
        />
        <View style={styles.rule} />
        <InfoRow
          icon="time-outline"
          label="取餐時間"
          value={`${dateTime(event.pickup_start)} 至 ${dateTime(event.pickup_end)}`}
        />
      </View>
      <View style={styles.heading}>
        <Text style={styles.headingTitle}>選擇餐點</Text>
        <Text style={styles.headingHint}>各餐點獨立計算預購容量</Text>
      </View>
      {!orderingOpen ? (
        <View style={styles.message}>
          <InlineMessage text={orderingLabel(event)} tone="warning" />
        </View>
      ) : null}
      {createOrder.error ? (
        <View style={styles.message}>
          <InlineMessage text={getErrorMessage(createOrder.error)} tone="danger" />
        </View>
      ) : null}
      <View style={styles.grid}>
        {event.items.map((item) => {
          const quantity = quantities[item.offering_id] ?? 0;
          return (
            <CatalogCard
              actionIcon={quantity ? "add" : "add"}
              badge={quantity ? `已選 ${quantity} 份` : undefined}
              disabled={!orderingOpen || item.available_quantity === 0}
              imageKey={item.image_key ?? "meal-lunchbox"}
              imageUrl={item.image_url}
              key={item.offering_id}
              meta={`${item.description}\n尚有 ${item.available_quantity} 份`}
              onAction={() =>
                setQuantities((current) => ({
                  ...current,
                  [item.offering_id]: Math.min(
                    item.available_quantity,
                    quantity + 1,
                  ),
                }))
              }
              onPress={() =>
                setQuantities((current) => ({
                  ...current,
                  [item.offering_id]:
                    quantity > 0
                      ? quantity - 1
                      : Math.min(1, item.available_quantity),
                }))
              }
              price={money(item.price)}
              priceLabel="每份"
              title={item.meal_name}
            />
          );
        })}
      </View>
      <View style={styles.checkoutPanel}>
        <Text style={styles.headingTitle}>付款與發票</Text>
        <Text style={styles.fieldLabel}>發票通知 Email</Text>
        <TextInput
          autoCapitalize="none"
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
              accessibilityState={{ selected: carrier === option.value }}
              key={option.value}
              onPress={() => setCarrier(option.value)}
              style={[
                styles.carrier,
                carrier === option.value && styles.carrierSelected,
              ]}
            >
              <Text style={styles.carrierLabel}>{option.label}</Text>
            </Pressable>
          ))}
        </View>
        {carrier === "mobile_barcode" ? (
          <TextInput
            autoCapitalize="characters"
            onChangeText={setBarcode}
            placeholder="/ABC+123"
            placeholderTextColor={colors.sage}
            style={styles.input}
            value={barcode}
          />
        ) : null}
      </View>
      <Text style={styles.tip}>點卡片可減少數量，右下角加號可增加。</Text>
    </Screen>
  );
}

const styles = StyleSheet.create({
  info: {
    backgroundColor: colors.paper,
    borderRadius: radii.lg,
    gap: 12,
    margin: spacing.md,
    padding: spacing.md,
  },
  rule: { backgroundColor: colors.line, height: 1 },
  heading: {
    alignItems: "flex-end",
    flexDirection: "row",
    justifyContent: "space-between",
    paddingHorizontal: spacing.md,
    paddingTop: spacing.sm,
  },
  headingTitle: { color: colors.forest, fontSize: 20, fontWeight: "900" },
  headingHint: { color: colors.muted, fontSize: 12 },
  message: { paddingHorizontal: spacing.md, paddingTop: spacing.md },
  checkoutPanel: {
    backgroundColor: colors.paper,
    borderRadius: radii.lg,
    gap: 10,
    marginHorizontal: spacing.md,
    padding: spacing.md,
  },
  fieldLabel: { color: colors.forest, fontSize: 12, fontWeight: "800" },
  input: {
    borderColor: colors.line,
    borderRadius: radii.sm,
    borderWidth: 1,
    color: colors.charcoal,
    minHeight: 46,
    paddingHorizontal: 12,
  },
  carriers: { flexDirection: "row", gap: 8 },
  carrier: {
    alignItems: "center",
    borderColor: colors.line,
    borderRadius: radii.sm,
    borderWidth: 1,
    flex: 1,
    justifyContent: "center",
    minHeight: 44,
    padding: 8,
  },
  carrierSelected: { backgroundColor: colors.sageLight, borderColor: colors.forest },
  carrierLabel: { color: colors.forest, fontSize: 12, fontWeight: "800" },
  grid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 12,
    padding: spacing.md,
  },
  tip: {
    color: colors.muted,
    fontSize: 12,
    paddingBottom: spacing.lg,
    paddingHorizontal: spacing.md,
    textAlign: "center",
  },
  bottom: {
    alignItems: "center",
    backgroundColor: colors.paper,
    borderTopColor: colors.line,
    borderTopWidth: 1,
    flexDirection: "row",
    justifyContent: "space-between",
    padding: spacing.md,
  },
  bottomLabel: { color: colors.muted, fontSize: 12 },
  bottomTotal: {
    color: colors.forest,
    fontSize: 24,
    fontWeight: "900",
    marginTop: 2,
  },
});
