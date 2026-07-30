import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { router, useLocalSearchParams } from "expo-router";
import { useMemo, useState } from "react";
import { StyleSheet, Text, View } from "react-native";

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
import { api, getErrorMessage } from "../../src/services/api";
import { useAuth } from "../../src/store/AuthContext";
import { colors, radii, spacing } from "../../src/theme";

export default function MealEventDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { isAuthenticated } = useAuth();
  const queryClient = useQueryClient();
  const [quantities, setQuantities] = useState<Record<string, number>>({});
  const query = useQuery({
    queryKey: ["meal-event", id],
    queryFn: () => api.mealEvent(id),
  });
  const total = useMemo(
    () =>
      (query.data?.items ?? []).reduce(
        (sum, item) => sum + item.price * (quantities[item.meal_id] ?? 0),
        0,
      ),
    [quantities, query.data?.items],
  );

  const createOrder = useMutation({
    mutationFn: () =>
      api.createMealOrder(id, {
        items: Object.entries(quantities)
          .filter(([, quantity]) => quantity > 0)
          .map(([meal_id, quantity]) => ({ meal_id, quantity })),
      }),
    onSuccess: async (order) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["meal-events"] }),
        queryClient.invalidateQueries({ queryKey: ["meal-event", id] }),
        queryClient.invalidateQueries({ queryKey: ["meal-orders"] }),
      ]);
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

  return (
    <Screen
      bottom={
        <View style={styles.bottom}>
          <View>
            <Text style={styles.bottomLabel}>合計</Text>
            <Text style={styles.bottomTotal}>{money(total)}</Text>
          </View>
          <Button
            disabled={total === 0}
            label={isAuthenticated ? "確認預購" : "登入後預購"}
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
        subtitle={`${event.school_name}，${dateTime(event.order_deadline)} 截止`}
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
      {createOrder.error ? (
        <View style={styles.message}>
          <InlineMessage text={getErrorMessage(createOrder.error)} tone="danger" />
        </View>
      ) : null}
      <View style={styles.grid}>
        {event.items.map((item) => {
          const quantity = quantities[item.meal_id] ?? 0;
          return (
            <CatalogCard
              actionIcon={quantity ? "add" : "add"}
              badge={quantity ? `已選 ${quantity} 份` : undefined}
              imageKey={item.image_key ?? "meal-lunchbox"}
              imageUrl={item.image_url}
              key={item.meal_id}
              meta={`${item.description}\n尚有 ${item.available_quantity} 份`}
              onAction={() =>
                setQuantities((current) => ({
                  ...current,
                  [item.meal_id]: Math.min(
                    item.available_quantity,
                    quantity + 1,
                  ),
                }))
              }
              onPress={() =>
                setQuantities((current) => ({
                  ...current,
                  [item.meal_id]:
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
