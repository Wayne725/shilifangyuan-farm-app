import { Ionicons } from "@expo/vector-icons";
import { useQuery } from "@tanstack/react-query";
import { router, useLocalSearchParams } from "expo-router";
import { StyleSheet, Text, View } from "react-native";

import {
  EmptyState,
  InfoRow,
  LoadingState,
  PageHeader,
  Screen,
  StatusPill,
} from "../../src/components/ui";
import { dateTime, money } from "../../src/lib/format";
import { api } from "../../src/services/api";
import { colors, radii, spacing } from "../../src/theme";

export default function MealOrderDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const query = useQuery({
    queryKey: ["meal-order", id],
    queryFn: () => api.mealOrder(id),
  });

  if (query.isLoading) return <LoadingState label="載入取餐憑證" />;
  if (!query.data) {
    return (
      <Screen>
        <PageHeader onBack={() => router.back()} title="便當訂單" />
        <EmptyState description="找不到這筆便當訂單。" title="訂單不存在" />
      </Screen>
    );
  }
  const order = query.data;
  return (
    <Screen>
      <PageHeader onBack={() => router.back()} title="取餐憑證" />
      <View style={styles.ticket}>
        <StatusPill
          label={order.fulfillment_status === "ready" ? "可取餐" : "處理中"}
          tone="positive"
        />
        <View style={styles.qr}>
          <Ionicons color={colors.forest} name="qr-code" size={126} />
        </View>
        <Text style={styles.codeLabel}>六位取餐碼</Text>
        <Text selectable style={styles.code}>
          {order.pickup_code}
        </Text>
        <Text style={styles.ticketHint}>取餐時出示本頁 QR 或六位取餐碼</Text>
      </View>
      <View style={styles.content}>
        <Text style={styles.title}>{order.meal_event_title}</Text>
        <Text style={styles.number}>{order.order_number}</Text>
        <View style={styles.info}>
          <InfoRow
            icon="location-outline"
            label="取餐地點"
            value={order.venue_name}
          />
          <View style={styles.rule} />
          <InfoRow
            icon="time-outline"
            label="取餐時間"
            value={`${dateTime(order.pickup_start)} 至 ${dateTime(order.pickup_end)}`}
          />
        </View>
        <View style={styles.items}>
          {order.items.map((item) => (
            <View key={item.meal_id} style={styles.itemRow}>
              <Text style={styles.itemName}>{item.meal_name}</Text>
              <Text style={styles.itemMeta}>
                {item.quantity} 份　{money(item.subtotal)}
              </Text>
            </View>
          ))}
          <View style={styles.totalRow}>
            <Text style={styles.totalLabel}>已付款</Text>
            <Text style={styles.total}>{money(order.amount_total)}</Text>
          </View>
        </View>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  ticket: {
    alignItems: "center",
    backgroundColor: colors.paper,
    borderRadius: radii.lg,
    marginHorizontal: spacing.md,
    padding: spacing.lg,
  },
  qr: {
    alignItems: "center",
    backgroundColor: colors.white,
    borderColor: colors.line,
    borderRadius: radii.md,
    borderWidth: 1,
    height: 168,
    justifyContent: "center",
    marginTop: spacing.lg,
    width: 168,
  },
  codeLabel: { color: colors.muted, fontSize: 12, marginTop: spacing.md },
  code: {
    color: colors.forest,
    fontSize: 34,
    fontWeight: "900",
    letterSpacing: 7,
    marginTop: 3,
  },
  ticketHint: {
    color: colors.muted,
    fontSize: 12,
    marginTop: 10,
    textAlign: "center",
  },
  content: { padding: spacing.md },
  title: { color: colors.forest, fontSize: 22, fontWeight: "900" },
  number: { color: colors.muted, fontSize: 12, marginTop: 5 },
  info: {
    backgroundColor: colors.paper,
    borderRadius: radii.lg,
    gap: 12,
    marginTop: spacing.md,
    padding: spacing.md,
  },
  rule: { backgroundColor: colors.line, height: 1 },
  items: {
    backgroundColor: colors.paper,
    borderRadius: radii.lg,
    marginTop: spacing.md,
    padding: spacing.md,
  },
  itemRow: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    minHeight: 44,
  },
  itemName: { color: colors.charcoal, flex: 1, fontSize: 13 },
  itemMeta: { color: colors.muted, fontSize: 12 },
  totalRow: {
    alignItems: "flex-end",
    borderTopColor: colors.line,
    borderTopWidth: 1,
    flexDirection: "row",
    justifyContent: "space-between",
    marginTop: 8,
    paddingTop: 12,
  },
  totalLabel: { color: colors.forest, fontSize: 13, fontWeight: "900" },
  total: { color: colors.orange, fontSize: 23, fontWeight: "900" },
});
