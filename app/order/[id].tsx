import { Ionicons } from "@expo/vector-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { router, useLocalSearchParams } from "expo-router";
import { Platform, StyleSheet, Text, View } from "react-native";

import {
  Button,
  EmptyState,
  InlineMessage,
  LoadingState,
  PageHeader,
  Screen,
  StatusPill,
} from "../../src/components/ui";
import { confirmAction } from "../../src/lib/confirm";
import {
  dateTime,
  fulfillmentStatusLabel,
  invoiceLabels,
  membershipLabel,
  money,
  paymentLabels,
  shipmentStatusLabel,
} from "../../src/lib/format";
import { openPaymentPage } from "../../src/lib/payment";
import { api, getErrorMessage } from "../../src/services/api";
import { colors, radii, spacing } from "../../src/theme";

export default function OrderDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["order", id],
    queryFn: () => api.order(id),
    refetchInterval: (state) =>
      state.state.data?.payment_status === "pending" ? 4000 : false,
  });
  const pay = useMutation({
    mutationFn: () => api.createPaymentAttempt(id),
    onSuccess: async (payment) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["order", id] }),
        queryClient.invalidateQueries({ queryKey: ["orders"] }),
      ]);
      if (payment.payment_url) {
        await openPaymentPage(payment.payment_url);
        if (Platform.OS !== "web") await query.refetch();
      }
    },
  });
  const reselect = useMutation({
    mutationFn: () => api.reissueLogisticsSelectionLink(id),
    onSuccess: async (selection) => {
      if (selection.selection_url) {
        await openPaymentPage(selection.selection_url);
        return;
      }
      await query.refetch();
    },
  });
  const cancel = useMutation({
    mutationFn: async () => {
      const paid = query.data?.payment_status === "paid";
      const confirmed = await confirmAction({
        title: paid ? "取消訂單並申請退款" : "取消訂單",
        message: paid
          ? "取消後將建立全額退款紀錄，且無法復原。確定要繼續嗎？"
          : "取消後這筆訂單無法復原，需要重新下單。確定要繼續嗎？",
        confirmLabel: paid ? "取消並退款" : "取消訂單",
        cancelLabel: "先不要",
      });
      if (!confirmed) return null;
      return api.cancelOrder(id);
    },
    onSuccess: async (result) => {
      if (!result) return;
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["order", id] }),
        queryClient.invalidateQueries({ queryKey: ["orders"] }),
        queryClient.invalidateQueries({ queryKey: ["campaigns"] }),
      ]);
    },
  });

  if (query.isLoading) return <LoadingState label="載入訂單明細" />;
  if (!query.data) {
    return (
      <Screen>
        <PageHeader onBack={() => router.back()} title="訂單詳情" />
        <EmptyState description="這筆訂單不存在或你沒有查看權限。" title="找不到訂單" />
      </Screen>
    );
  }

  const order = query.data;
  // Shipping orders cannot be paid until ECPay has fixed the store/address,
  // so surface the missing step instead of a payment button that 409s.
  const awaitingSelection =
    order.fulfillment?.method === "ecpay_logistics" &&
    !!order.shipment &&
    !["ready_to_create", "created", "in_transit", "delivered"].includes(
      order.shipment.status,
    );
  const canPay = order.available_actions.includes("pay") && !awaitingSelection;
  const canCancel = order.available_actions.includes("cancel");
  const fulfillmentStatus =
    order.fulfillment?.status ?? order.fulfillment_status;

  return (
    <Screen>
      <PageHeader
        onBack={() => router.back()}
        subtitle={`${
          order.order_kind === "group"
            ? "團購訂單"
            : order.order_kind === "meal_preorder"
              ? "便當預購"
              : "一般訂單"
        }・${dateTime(order.created_at)}`}
        title={order.order_number}
      />
      <View style={styles.content}>
        <View style={styles.summary}>
          <View>
            <Text style={styles.summaryLabel}>訂單金額</Text>
            <Text style={styles.summaryTotal}>{money(order.amount_total)}</Text>
          </View>
          <StatusPill
            label={fulfillmentStatusLabel(fulfillmentStatus)}
            tone={
              ["ready_for_pickup", "ready", "delivered"].includes(
                fulfillmentStatus,
              )
                ? "positive"
                : fulfillmentStatus === "cancelled"
                  ? "danger"
                  : "neutral"
            }
          />
        </View>

        <View style={styles.itemsPanel}>
          <Text style={styles.sectionTitle}>履約方式</Text>
          <View style={styles.fulfillmentRow}>
            <Ionicons
              color={colors.forest}
              name={
                order.fulfillment?.method === "ecpay_logistics"
                  ? "cube-outline"
                  : "storefront-outline"
              }
              size={21}
            />
            <View style={styles.fulfillmentCopy}>
              <Text style={styles.itemName}>
                {order.fulfillment?.method === "ecpay_logistics"
                  ? "綠界物流配送"
                  : order.fulfillment?.method === "event_pickup"
                    ? "活動場次取餐"
                    : "合作社現場取貨"}
              </Text>
              <Text style={styles.itemMeta}>
                {order.shipment
                  ? `貨態 ${shipmentStatusLabel(order.shipment.status)}・追蹤碼 ${
                      order.shipment.tracking_number ?? "尚未取得"
                    }`
                  : order.fulfillment?.venue_name ?? "依通知時間前往取貨"}
              </Text>
            </View>
          </View>
          {order.shipment ? (
            <View style={styles.shippingFeeRow}>
              <Text style={styles.totalLabel}>運費</Text>
              <Text style={styles.itemSubtotal}>
                {money(order.shipment.shipping_fee)}
              </Text>
            </View>
          ) : null}
        </View>

        <View style={styles.statusPanel}>
          {[
            {
              icon: "card-outline" as const,
              label: "付款狀態",
              value: paymentLabels[order.payment_status],
            },
            {
              icon: "cube-outline" as const,
              label: "履約狀態",
              value: fulfillmentStatusLabel(fulfillmentStatus),
            },
            {
              icon: "document-text-outline" as const,
              label: "發票狀態",
              value: invoiceLabels[order.invoice_status],
            },
          ].map((item, index) => (
            <View key={item.label}>
              <View style={styles.statusRow}>
                <View style={styles.statusIcon}>
                  <Ionicons color={colors.forest} name={item.icon} size={19} />
                </View>
                <Text style={styles.statusLabel}>{item.label}</Text>
                <Text style={styles.statusValue}>{item.value}</Text>
              </View>
              {index < 2 ? <View style={styles.rule} /> : null}
            </View>
          ))}
        </View>

        <View style={styles.itemsPanel}>
          <Text style={styles.sectionTitle}>訂購內容</Text>
          {order.items.map((item, index) => (
            <View key={`${item.product_name}-${index}`} style={styles.item}>
              <View style={styles.itemCopy}>
                <Text style={styles.itemName}>{item.product_name}</Text>
                <Text style={styles.itemMeta}>
                  {money(item.unit_price)} × {item.quantity}
                </Text>
              </View>
              <Text style={styles.itemSubtotal}>{money(item.subtotal)}</Text>
            </View>
          ))}
          <View style={styles.rule} />
          <View style={styles.totalRow}>
            <Text style={styles.totalLabel}>
              {membershipLabel(order.membership_type_snapshot)}價格
            </Text>
            <Text style={styles.total}>{money(order.amount_total)}</Text>
          </View>
        </View>

        <InlineMessage
          text={
            order.order_kind === "group"
              ? order.fulfillment?.method === "ecpay_logistics"
                ? "只有付款完成的數量會計入門檻。確認成團並備貨後才會建立正式物流單。"
                : "只有付款完成的數量會計入成團門檻。確認成團後，合作社會發布最終取貨時間。"
              : "付款完成後由合作社確認訂單；完成取貨後才開立電子發票。"
          }
        />
        {pay.error || cancel.error || reselect.error ? (
          <InlineMessage
            text={getErrorMessage(pay.error ?? cancel.error ?? reselect.error)}
            tone="danger"
          />
        ) : null}

        {awaitingSelection ? (
          <>
            <InlineMessage
              text="尚未完成綠界物流門市或地址選擇，完成後才能付款。"
              tone="danger"
            />
            <Button
              icon="cube-outline"
              label="繼續選擇物流"
              loading={reselect.isPending}
              onPress={() => reselect.mutate()}
              variant="secondary"
            />
          </>
        ) : null}
        {canPay ? (
          <Button
            icon="card-outline"
            label="前往付款"
            loading={pay.isPending}
            onPress={() => pay.mutate()}
          />
        ) : null}
        {canCancel ? (
          <Button
            label={
              order.payment_status === "paid"
                ? "取消訂單並申請全額退款"
                : "取消訂單"
            }
            loading={cancel.isPending}
            onPress={() => cancel.mutate()}
            variant="danger"
          />
        ) : null}
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  content: { gap: 13, padding: spacing.md },
  summary: {
    alignItems: "center",
    backgroundColor: colors.forest,
    borderRadius: radii.lg,
    flexDirection: "row",
    justifyContent: "space-between",
    padding: spacing.lg,
  },
  summaryLabel: { color: "#C9D6CE", fontSize: 13 },
  summaryTotal: {
    color: colors.white,
    fontSize: 31,
    fontWeight: "900",
    marginTop: 4,
  },
  statusPanel: {
    backgroundColor: colors.paper,
    borderRadius: radii.md,
    padding: spacing.md,
  },
  statusRow: { alignItems: "center", flexDirection: "row" },
  statusIcon: {
    alignItems: "center",
    backgroundColor: colors.sageLight,
    borderRadius: 11,
    height: 38,
    justifyContent: "center",
    width: 38,
  },
  statusLabel: { color: colors.muted, flex: 1, fontSize: 13, marginLeft: 10 },
  statusValue: { color: colors.forest, fontSize: 13, fontWeight: "900" },
  rule: { backgroundColor: colors.line, height: 1, marginVertical: 11 },
  itemsPanel: {
    backgroundColor: colors.paper,
    borderRadius: radii.md,
    padding: spacing.md,
  },
  sectionTitle: {
    color: colors.forest,
    fontSize: 16,
    fontWeight: "900",
    marginBottom: 7,
  },
  item: {
    alignItems: "center",
    flexDirection: "row",
    paddingVertical: 9,
  },
  itemCopy: { flex: 1 },
  fulfillmentRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 11,
    minHeight: 48,
  },
  fulfillmentCopy: { flex: 1 },
  shippingFeeRow: {
    alignItems: "center",
    borderTopColor: colors.line,
    borderTopWidth: 1,
    flexDirection: "row",
    justifyContent: "space-between",
    marginTop: 10,
    paddingTop: 10,
  },
  itemName: { color: colors.charcoal, fontSize: 13, fontWeight: "800" },
  itemMeta: { color: colors.muted, fontSize: 13, marginTop: 3 },
  itemSubtotal: { color: colors.forest, fontSize: 13, fontWeight: "900" },
  totalRow: {
    alignItems: "baseline",
    flexDirection: "row",
    justifyContent: "space-between",
  },
  totalLabel: { color: colors.muted, fontSize: 13 },
  total: { color: colors.forest, fontSize: 22, fontWeight: "900" },
});
