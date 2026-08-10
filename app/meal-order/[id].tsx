import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { router, useLocalSearchParams } from "expo-router";
import { Platform, StyleSheet, Text, View } from "react-native";
import QRCode from "react-native-qrcode-svg";

import {
  Button,
  EmptyState,
  InfoRow,
  InlineMessage,
  LoadingState,
  PageHeader,
  Screen,
  StatusPill,
} from "../../src/components/ui";
import { dateTime, money } from "../../src/lib/format";
import { confirmAction } from "../../src/lib/confirm";
import { openPaymentPage } from "../../src/lib/payment";
import { api, getErrorMessage } from "../../src/services/api";
import { colors, radii, spacing } from "../../src/theme";

export default function MealOrderDetailScreen() {
  const { id, payment, setup } = useLocalSearchParams<{
    id: string;
    payment?: string;
    setup?: string;
  }>();
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["meal-order", id],
    queryFn: () => api.mealOrder(id),
    refetchInterval: (state) =>
      state.state.data?.payment_status === "pending" ? 4000 : false,
  });
  const pay = useMutation({
    mutationFn: () => api.createPaymentAttempt(id),
    onSuccess: async (payment) => {
      if (payment.payment_url) {
        await openPaymentPage(payment.payment_url);
        if (Platform.OS === "web") return;
      }
      await Promise.all([
        query.refetch(),
        queryClient.invalidateQueries({ queryKey: ["meal-orders"] }),
        queryClient.invalidateQueries({ queryKey: ["orders"] }),
      ]);
    },
  });
  const cancel = useMutation({
    mutationFn: async () => {
      const confirmed = await confirmAction({
        title: "取消便當預購",
        message:
          "取消後將釋放便當容量；已付款訂單會建立 Sandbox 全額退款紀錄。確定取消嗎？",
        confirmLabel: "確認取消",
        cancelLabel: "保留訂單",
        destructive: true,
      });
      if (!confirmed) return null;
      return api.cancelMealOrder(id);
    },
    onSuccess: async (result) => {
      if (!result) return;
      await Promise.all([
        query.refetch(),
        queryClient.invalidateQueries({ queryKey: ["meal-orders"] }),
        queryClient.invalidateQueries({ queryKey: ["orders"] }),
      ]);
    },
  });

  if (query.isLoading) return <LoadingState label="載入取餐憑證" />;
  if (query.isError || !query.data) {
    return (
      <Screen>
        <PageHeader onBack={() => router.back()} title="便當訂單" />
        <EmptyState
          action={query.isError ? "重新載入" : undefined}
          description={
            query.isError ? "目前無法取得訂單資料。" : "找不到這筆便當訂單。"
          }
          onAction={query.isError ? () => query.refetch() : undefined}
          title={query.isError ? "訂單載入失敗" : "訂單不存在"}
        />
      </Screen>
    );
  }
  const order = query.data;
  return (
    <Screen>
      <PageHeader
        onBack={() =>
          payment ? router.replace("/(tabs)/orders") : router.back()
        }
        title="取餐憑證"
      />
      {payment ? (
        <View style={styles.message}>
          <InlineMessage
            text={
              order.payment_status === "paid"
                ? "付款已完成，正在準備取餐憑證。"
                : payment === "failed"
                  ? "本次付款未完成，請確認資料後重新付款。"
                  : "系統正在確認最終付款結果，請勿重複付款。"
            }
            tone={
              order.payment_status === "paid"
                ? "positive"
                : payment === "failed"
                  ? "danger"
                  : "warning"
            }
          />
        </View>
      ) : null}
      {setup === "retry" ? (
        <View style={styles.message}>
          <InlineMessage
            text="便當訂單已安全建立，但付款頁暫時未開啟。請在本頁重新付款，不要重複下單。"
            tone="warning"
          />
        </View>
      ) : null}
      {order.payment_status === "paid" &&
      order.pickup_code &&
      order.pickup_qr_payload ? (
        <View style={styles.ticket}>
          <StatusPill
            label={
              order.fulfillment_status === "ready" ? "可取餐" : "處理中"
            }
            tone="positive"
          />
          <View
            accessibilityLabel={`便當訂單 ${order.order_number} 的取餐 QR Code`}
            accessibilityRole="image"
            style={styles.qr}
          >
            <QRCode
              backgroundColor={colors.white}
              color={colors.charcoal}
              ecl="H"
              quietZone={8}
              size={144}
              value={order.pickup_qr_payload}
            />
          </View>
          <Text style={styles.codeLabel}>六位取餐碼</Text>
          <Text selectable style={styles.code}>
            {order.pickup_code}
          </Text>
          <Text style={styles.ticketHint}>
            取餐時出示本頁 QR 或六位取餐碼
          </Text>
        </View>
      ) : ["picked_up", "no_show", "cancelled"].includes(
          order.fulfillment_status,
        ) ? (
        <View style={styles.pendingPanel}>
          <StatusPill
            label={
              order.fulfillment_status === "picked_up"
                ? "已取餐"
                : order.fulfillment_status === "no_show"
                  ? "逾時未取"
                  : "已取消"
            }
            tone={
              order.fulfillment_status === "picked_up" ? "positive" : "neutral"
            }
          />
          <Text style={styles.pendingTitle}>
            {order.fulfillment_status === "picked_up"
              ? "這筆訂單已完成核銷"
              : order.fulfillment_status === "no_show"
                ? "本場次取餐時間已結束"
                : "這筆訂單已取消"}
          </Text>
          <Text style={styles.ticketHint}>
            為避免重複核銷，取餐憑證已停止顯示。
          </Text>
        </View>
      ) : order.payment_status === "paid" ? (
        <View style={styles.pendingPanel}>
          <StatusPill label="憑證建立中" tone="warning" />
          <Text style={styles.pendingTitle}>正在產生取餐憑證</Text>
          <Text style={styles.ticketHint}>
            系統已收到付款結果，完成後會自動顯示 QR 與六位取餐碼。
          </Text>
          <Button
            label="重新查詢"
            loading={query.isFetching}
            onPress={() => query.refetch()}
            variant="secondary"
          />
        </View>
      ) : (
        <View style={styles.pendingPanel}>
          <StatusPill label="待付款" tone="warning" />
          <Text style={styles.pendingTitle}>付款後顯示取餐憑證</Text>
          <Text style={styles.ticketHint}>
            完成綠界測試付款後，系統才會顯示六位取餐碼與 QR。
          </Text>
          <Button
            icon="card-outline"
            label={`前往付款 ${money(order.amount_total)}`}
            loading={pay.isPending}
            onPress={() => pay.mutate()}
          />
          {pay.error ? (
            <InlineMessage text={getErrorMessage(pay.error)} tone="danger" />
          ) : null}
        </View>
      )}
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
            <Text style={styles.totalLabel}>
              {order.payment_status === "paid" ? "已付款" : "待付款"}
            </Text>
            <Text style={styles.total}>{money(order.amount_total)}</Text>
          </View>
        </View>
        {order.available_actions?.includes("cancel") ? (
          <Button
            label="取消這筆便當預購"
            loading={cancel.isPending}
            onPress={() => cancel.mutate()}
            variant="danger"
          />
        ) : null}
        {cancel.error ? (
          <InlineMessage text={getErrorMessage(cancel.error)} tone="danger" />
        ) : null}
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  message: { paddingHorizontal: spacing.md, paddingBottom: spacing.sm },
  ticket: {
    alignItems: "center",
    backgroundColor: colors.paper,
    borderRadius: radii.lg,
    marginHorizontal: spacing.md,
    padding: spacing.lg,
  },
  pendingPanel: {
    alignItems: "center",
    backgroundColor: colors.paper,
    borderRadius: radii.lg,
    gap: 13,
    marginHorizontal: spacing.md,
    padding: spacing.lg,
  },
  pendingTitle: { color: colors.forest, fontSize: 21, fontWeight: "900" },
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
