import { useQuery } from "@tanstack/react-query";
import { router } from "expo-router";
import { useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import {
  CatalogCard,
  EmptyState,
  LoadingState,
  PageHeader,
  Screen,
  SegmentControl,
  StatusPill,
} from "../../src/components/ui";
import { dateTime, money } from "../../src/lib/format";
import { api } from "../../src/services/api";
import { useAuth } from "../../src/store/AuthContext";
import { colors, radii, spacing } from "../../src/theme";

type MealTab = "events" | "orders";

const mealTabs: { value: MealTab; label: string }[] = [
  { value: "events", label: "預購場次" },
  { value: "orders", label: "我的便當" },
];

export default function MealsScreen() {
  const [tab, setTab] = useState<MealTab>("events");
  const { isAuthenticated } = useAuth();
  const events = useQuery({
    queryKey: ["meal-events"],
    queryFn: api.mealEvents,
  });
  const orders = useQuery({
    queryKey: ["meal-orders"],
    queryFn: api.mealOrders,
    enabled: isAuthenticated,
  });

  return (
    <Screen>
      <PageHeader
        subtitle="先選場次與餐點，付款完成後憑六位取餐碼領取。"
        title="便當預購"
      />
      <SegmentControl onChange={setTab} options={mealTabs} value={tab} />

      {tab === "events" ? (
        events.isLoading ? (
          <LoadingState label="載入便當場次" />
        ) : events.isError ? (
          <EmptyState
            action="重新載入"
            description="目前無法取得預購場次。"
            icon="cloud-offline-outline"
            onAction={() => events.refetch()}
            title="場次載入失敗"
          />
        ) : events.data?.length ? (
          <View style={styles.grid}>
            {events.data.map((event) => {
              const lowestPrice = Math.min(
                ...event.items.map((item) => item.price),
              );
              const available = event.items.reduce(
                (total, item) => total + item.available_quantity,
                0,
              );
              return (
                <CatalogCard
                  badge={event.status === "published" ? "開放預購" : "即將取餐"}
                  imageKey="meal-lunchbox"
                  key={event.id}
                  meta={`${event.venue_name}\n${dateTime(event.order_deadline)} 截止`}
                  onPress={() =>
                    router.push({
                      pathname: "/meal/[id]",
                      params: { id: event.id },
                    })
                  }
                  price={`${money(lowestPrice)} 起`}
                  priceLabel="單一售價"
                  progressLabel={`尚可預訂 ${available} 份`}
                  title={event.title}
                />
              );
            })}
          </View>
        ) : (
          <EmptyState
            description="新場次公布後會顯示在這裡。"
            icon="restaurant-outline"
            title="目前沒有便當場次"
          />
        )
      ) : !isAuthenticated ? (
        <EmptyState
          action="登入帳號"
          description="登入後可以查看取餐碼與便當訂單。"
          icon="person-outline"
          onAction={() => router.push("/login")}
          title="登入查看便當"
        />
      ) : orders.isLoading ? (
        <LoadingState label="載入便當訂單" />
      ) : orders.data?.length ? (
        <View style={styles.orderList}>
          {orders.data.map((order) => (
            <Pressable
              key={order.id}
              onPress={() =>
                router.push({
                  pathname: "/meal-order/[id]",
                  params: { id: order.id },
                })
              }
              style={({ pressed }) => [
                styles.orderCard,
                pressed && styles.pressed,
              ]}
            >
              <View style={styles.orderTop}>
                <View style={styles.orderCopy}>
                  <Text style={styles.orderTitle}>{order.meal_event_title}</Text>
                  <Text style={styles.orderMeta}>{order.venue_name}</Text>
                </View>
                <StatusPill
                  label={
                    order.fulfillment_status === "ready"
                      ? "可取餐"
                      : order.fulfillment_status === "picked_up"
                        ? "已取餐"
                        : "處理中"
                  }
                  tone={
                    order.fulfillment_status === "ready" ? "positive" : "neutral"
                  }
                />
              </View>
              <View style={styles.codeRow}>
                <View>
                  <Text style={styles.codeLabel}>取餐碼</Text>
                  <Text style={styles.code}>{order.pickup_code}</Text>
                </View>
                <Text style={styles.total}>{money(order.amount_total)}</Text>
              </View>
            </Pressable>
          ))}
        </View>
      ) : (
        <EmptyState
          action="選擇場次"
          description="完成便當預購後，取餐資訊會集中顯示在這裡。"
          icon="restaurant-outline"
          onAction={() => setTab("events")}
          title="還沒有便當訂單"
        />
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  grid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 12,
    padding: spacing.md,
  },
  orderList: { gap: 12, padding: spacing.md },
  orderCard: {
    backgroundColor: colors.paper,
    borderRadius: radii.lg,
    padding: spacing.md,
  },
  orderTop: {
    alignItems: "flex-start",
    flexDirection: "row",
    justifyContent: "space-between",
  },
  orderCopy: { flex: 1, paddingRight: 12 },
  orderTitle: { color: colors.forest, fontSize: 17, fontWeight: "900" },
  orderMeta: { color: colors.muted, fontSize: 12, marginTop: 5 },
  codeRow: {
    alignItems: "flex-end",
    borderTopColor: colors.line,
    borderTopWidth: 1,
    flexDirection: "row",
    justifyContent: "space-between",
    marginTop: 14,
    paddingTop: 12,
  },
  codeLabel: { color: colors.muted, fontSize: 12 },
  code: {
    color: colors.forest,
    fontSize: 26,
    fontWeight: "900",
    letterSpacing: 4,
    marginTop: 2,
  },
  total: { color: colors.orange, fontSize: 20, fontWeight: "900" },
  pressed: { opacity: 0.78, transform: [{ scale: 0.99 }] },
});
