import { useMutation, useQuery } from "@tanstack/react-query";
import { router } from "expo-router";
import * as WebBrowser from "expo-web-browser";
import { useState } from "react";
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
  InlineMessage,
  LoadingState,
  PageHeader,
  Screen,
} from "../src/components/ui";
import { membershipLabel, money } from "../src/lib/format";
import { api, getErrorMessage } from "../src/services/api";
import { useAuth } from "../src/store/AuthContext";
import { useCart } from "../src/store/CartContext";
import { colors, radii, spacing } from "../src/theme";
import type { InvoiceCarrierType } from "../src/types";

export default function CheckoutScreen() {
  const { user } = useAuth();
  const { items, clear } = useCart();
  const [email, setEmail] = useState(user?.email ?? "");
  const [carrier, setCarrier] = useState<InvoiceCarrierType>("ecpay");
  const [barcode, setBarcode] = useState("/");
  const quote = useQuery({
    queryKey: ["quote", items],
    queryFn: () => api.quote(items),
    enabled: items.length > 0,
  });
  const submit = useMutation({
    mutationFn: async () => {
      if (!user) throw new Error("請先登入");
      if (carrier === "mobile_barcode") {
        const result = await api.validateMobileBarcode(barcode);
        if (!result.valid) throw new Error(result.message ?? "手機條碼格式不正確");
      }
      const order = await api.createOrder({
        items,
        contact_email: email,
        invoice_carrier_type: carrier,
        ...(carrier === "mobile_barcode"
          ? { invoice_carrier_value: barcode }
          : {}),
      });
      const payment = await api.createPaymentAttempt(order.id);
      return { order, payment };
    },
    onSuccess: async ({ order, payment }) => {
      clear();
      if (payment.payment_url) {
        if (Platform.OS === "web") {
          window.location.assign(payment.payment_url);
          return;
        }
        await WebBrowser.openBrowserAsync(payment.payment_url);
      }
      router.replace({
        pathname: "/order/[id]",
        params: { id: order.id },
      });
    },
  });

  if (!items.length) {
    router.replace("/(tabs)/cart");
    return null;
  }

  return (
    <Screen>
      <PageHeader
        onBack={() => router.back()}
        subtitle="確認現場取貨與電子發票資料後，前往線上付款。"
        title="確認結帳"
      />
      {quote.isLoading ? (
        <LoadingState label="計算訂單金額" />
      ) : (
        <View style={styles.content}>
          <View style={styles.orderPanel}>
            <View style={styles.panelHeading}>
              <Text style={[styles.sectionTitle, styles.orderTitle]}>
                訂購內容
              </Text>
              <Text style={styles.identity}>
                {membershipLabel(user?.membership_type ?? "nonmember")}價格
              </Text>
            </View>
            {(quote.data?.items ?? []).map((item, index) => (
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
            <View style={styles.totalRow}>
              <Text style={styles.totalLabel}>付款金額</Text>
              <Text style={styles.total}>
                {money(quote.data?.amount_total ?? 0)}
              </Text>
            </View>
          </View>

          <View style={styles.panel}>
            <Text style={styles.sectionTitle}>取貨方式</Text>
            <View style={styles.selectedLine}>
              <View style={styles.check} />
              <View>
                <Text style={styles.lineTitle}>合作社現場取貨</Text>
                <Text style={styles.lineHint}>可取貨時會透過 App 與 Email 通知</Text>
              </View>
            </View>
          </View>

          <View style={styles.panel}>
            <Text style={styles.sectionTitle}>電子發票</Text>
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
            <Text style={styles.fieldLabel}>選擇載具</Text>
            <View style={styles.carriers}>
              {[
                { value: "ecpay" as const, label: "綠界載具＋Email" },
                { value: "mobile_barcode" as const, label: "手機條碼" },
              ].map((option) => (
                <Pressable
                  key={option.value}
                  onPress={() => setCarrier(option.value)}
                  style={[
                    styles.carrier,
                    carrier === option.value && styles.carrierSelected,
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
          <Button
            disabled={!email.includes("@")}
            icon="card-outline"
            label={`前往付款 ${money(quote.data?.amount_total ?? 0)}`}
            loading={submit.isPending}
            onPress={() => submit.mutate()}
          />
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
  identity: { color: "#D7E2DA", fontSize: 10 },
  item: {
    alignItems: "center",
    flexDirection: "row",
    paddingTop: 13,
  },
  itemCopy: { flex: 1 },
  itemName: { color: colors.white, fontSize: 12, fontWeight: "800" },
  itemMeta: { color: "#C8D5CC", fontSize: 9, marginTop: 3 },
  subtotal: { color: colors.white, fontSize: 12, fontWeight: "900" },
  rule: { backgroundColor: "#49695E", height: 1, marginVertical: 14 },
  totalRow: {
    alignItems: "baseline",
    flexDirection: "row",
    justifyContent: "space-between",
  },
  totalLabel: { color: "#C8D5CC", fontSize: 10 },
  total: { color: "#EEC8A4", fontSize: 27, fontWeight: "900" },
  panel: {
    backgroundColor: colors.paper,
    borderRadius: radii.md,
    gap: 10,
    padding: spacing.md,
  },
  selectedLine: { alignItems: "center", flexDirection: "row", gap: 10 },
  check: {
    backgroundColor: colors.forest,
    borderColor: colors.sage,
    borderRadius: 8,
    borderWidth: 4,
    height: 16,
    width: 16,
  },
  lineTitle: { color: colors.forest, fontSize: 12, fontWeight: "900" },
  lineHint: { color: colors.muted, fontSize: 9, marginTop: 3 },
  fieldLabel: {
    color: colors.forest,
    fontSize: 11,
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
  carrierText: { color: colors.forest, fontSize: 10, fontWeight: "800" },
  invoiceHint: { color: colors.muted, fontSize: 9, lineHeight: 14 },
});
