import { useQuery } from "@tanstack/react-query";
import { router } from "expo-router";
import { useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import {
  EmptyState,
  LoadingState,
  PageHeader,
  Screen,
  SegmentControl,
  StatusPill,
} from "../../src/components/ui";
import {
  dateTime,
  fulfillmentStatusLabel,
  money,
  paymentLabels,
} from "../../src/lib/format";
import { api } from "../../src/services/api";
import { useAuth } from "../../src/store/AuthContext";
import { colors, radii, spacing } from "../../src/theme";
import type { Order, OrderKind } from "../../src/types";

const orderTabs: { value: OrderKind; label: string }[] = [
  { value: "regular", label: "一般訂單" },
  { value: "group", label: "團購訂單" },
  { value: "meal_preorder", label: "便當預購" },
];

function OrderCard({ order }: { order: Order }) {
  const fulfillmentStatus =
    order.fulfillment?.status ?? order.fulfillment_status;
  return (
    <Pressable
      onPress={() =>
        order.order_kind === "meal_preorder"
          ? router.push({
              pathname: "/meal-order/[id]",
              params: { id: order.id },
            })
          : router.push({
              pathname: "/order/[id]",
              params: { id: order.id },
            })
      }
      style={({ pressed }) => [
        styles.card,
        pressed && styles.pressed,
      ]}
    >
      <View style={styles.cardTop}>
        <View>
          <Text style={styles.orderNumber}>{order.order_number}</Text>
          <Text style={styles.orderDate}>{dateTime(order.created_at)}</Text>
        </View>
        <StatusPill
          label={fulfillmentStatusLabel(fulfillmentStatus)}
          tone={
            ["ready_for_pickup", "ready", "delivered"].includes(
              fulfillmentStatus,
            )
              ? "positive"
              : order.fulfillment_status === "cancelled"
                ? "danger"
                : "neutral"
          }
        />
      </View>
      <View style={styles.rule} />
      {order.items.slice(0, 2).map((item, index) => (
        <View
          key={`${item.product_name}-${index}`}
          style={styles.itemRow}
        >
          <Text numberOfLines={1} style={styles.itemName}>
            {item.product_name}
          </Text>
          <Text style={styles.itemQuantity}>× {item.quantity}</Text>
        </View>
      ))}
      {order.items.length > 2 ? (
        <Text style={styles.more}>另有 {order.items.length - 2} 項</Text>
      ) : null}
      <View style={styles.cardBottom}>
        <StatusPill
          label={paymentLabels[order.payment_status]}
          tone={order.payment_status === "paid" ? "positive" : "warning"}
        />
        <Text style={styles.total}>{money(order.amount_total)}</Text>
      </View>
    </Pressable>
  );
}

export default function OrdersScreen() {
  const [kind, setKind] = useState<OrderKind>("regular");
  const { isAuthenticated } = useAuth();
  const query = useQuery({
    queryKey: ["orders"],
    queryFn: api.orders,
    enabled: isAuthenticated,
  });
  const orders = (query.data ?? []).filter(
    (order) => order.order_kind === kind,
  );

  return (
    <Screen>
      <PageHeader
        subtitle="付款、備貨、取貨與發票各自顯示進度。"
        title="我的訂單"
      />
      <SegmentControl onChange={setKind} options={orderTabs} value={kind} />

      {!isAuthenticated ? (
        <EmptyState
          action="登入查看訂單"
          description="登入後可查看付款、取貨與電子發票狀態。"
          icon="receipt-outline"
          onAction={() => router.push("/login")}
          title="尚未登入"
        />
      ) : query.isLoading ? (
        <LoadingState label="載入訂單" />
      ) : orders.length ? (
        <View style={styles.list}>
          {orders.map((order) => (
            <OrderCard key={order.id} order={order} />
          ))}
        </View>
      ) : (
        <EmptyState
          action={
            kind === "group"
              ? "看看共同購買"
              : kind === "meal_preorder"
                ? "看看便當"
                : "前往選購"
          }
          description={
            kind === "group"
              ? "加入正式團購後，訂單會集中顯示在這裡。"
              : kind === "meal_preorder"
                ? "完成便當預購後，取餐碼會顯示在這裡。"
              : "完成選購與付款後，訂單會顯示在這裡。"
          }
          icon={
            kind === "group"
              ? "people-outline"
              : kind === "meal_preorder"
                ? "restaurant-outline"
                : "basket-outline"
          }
          onAction={() =>
            router.push(
              kind === "group"
                ? "/(tabs)/group-buy"
                : kind === "meal_preorder"
                  ? "/(tabs)/meals"
                  : "/(tabs)/home",
            )
          }
          title="目前沒有訂單"
        />
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  list: { gap: 12, padding: spacing.md },
  card: {
    backgroundColor: colors.paper,
    borderRadius: radii.lg,
    padding: spacing.md,
  },
  cardTop: {
    alignItems: "flex-start",
    flexDirection: "row",
    justifyContent: "space-between",
  },
  orderNumber: { color: colors.forest, fontSize: 15, fontWeight: "900" },
  orderDate: { color: colors.muted, fontSize: 12, marginTop: 4 },
  rule: {
    backgroundColor: colors.line,
    height: 1,
    marginVertical: 12,
  },
  itemRow: { flexDirection: "row", marginBottom: 7 },
  itemName: { color: colors.charcoal, flex: 1, fontSize: 12 },
  itemQuantity: { color: colors.muted, fontSize: 12 },
  more: { color: colors.muted, fontSize: 12 },
  cardBottom: {
    alignItems: "flex-end",
    flexDirection: "row",
    justifyContent: "space-between",
    marginTop: 12,
  },
  total: { color: colors.forest, fontSize: 20, fontWeight: "900" },
  pressed: { opacity: 0.72, transform: [{ scale: 0.99 }] },
});
