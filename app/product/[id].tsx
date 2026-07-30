import { useQuery } from "@tanstack/react-query";
import { router, useLocalSearchParams } from "expo-router";
import { useState } from "react";
import { Image, StyleSheet, Text, View } from "react-native";

import {
  Button,
  EmptyState,
  InfoRow,
  LoadingState,
  PageHeader,
  QuantityControl,
  Screen,
  StatusPill,
} from "../../src/components/ui";
import { money } from "../../src/lib/format";
import { imageFor } from "../../src/lib/images";
import { api } from "../../src/services/api";
import { useAuth } from "../../src/store/AuthContext";
import { useCart } from "../../src/store/CartContext";
import { colors, radii, spacing } from "../../src/theme";

export default function ProductDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const [quantity, setQuantity] = useState(1);
  const { user, isAuthenticated } = useAuth();
  const { addItem } = useCart();
  const query = useQuery({ queryKey: ["products"], queryFn: api.products });
  const product = query.data?.find((item) => item.id === id);

  if (query.isLoading) return <LoadingState label="載入商品資料" />;
  if (!product) {
    return (
      <Screen>
        <PageHeader onBack={() => router.back()} title="商品詳情" />
        <EmptyState
          action="回到首頁"
          description="這項商品目前可能已經下架。"
          onAction={() => router.replace("/(tabs)/home")}
          title="找不到商品"
        />
      </Screen>
    );
  }

  const membership = user?.membership_type ?? "nonmember";
  const price =
    membership === "member" ? product.member_price : product.nonmember_price;

  return (
    <Screen
      bottom={
        <View style={styles.bottom}>
          <View>
            <Text style={styles.bottomLabel}>小計</Text>
            <Text style={styles.bottomTotal}>{money(price * quantity)}</Text>
          </View>
          <Button
            icon="basket-outline"
            label="加入購物車"
            onPress={() => {
              if (!isAuthenticated) {
                router.push("/login");
                return;
              }
              addItem(product.id, quantity);
              router.push("/(tabs)/cart");
            }}
          />
        </View>
      }
    >
      <PageHeader onBack={() => router.back()} title="商品詳情" />
      <Image
        source={imageFor(product.image_key ?? product.id, product.image_url)}
        style={styles.image}
      />
      <View style={styles.content}>
        <StatusPill label={product.badge ?? product.category} />
        <Text style={styles.name}>{product.name}</Text>
        <Text style={styles.origin}>
          {product.origin ?? "合作小農聯合供應"}・每{product.unit}
        </Text>

        <View style={styles.priceRow}>
          <View>
            <Text style={styles.priceLabel}>
              {membership === "member" ? "社員價" : "一般價"}
            </Text>
            <Text style={styles.price}>{money(price)}</Text>
          </View>
          {membership === "member" ? (
            <View style={styles.saving}>
              <Text style={styles.savingText}>
                比一般價省 {money(product.nonmember_price - product.member_price)}
              </Text>
            </View>
          ) : null}
        </View>

        <View style={styles.divider} />
        <Text style={styles.sectionTitle}>關於這項農產</Text>
        <Text style={styles.description}>{product.description}</Text>

        <View style={styles.infoPanel}>
          <InfoRow
            icon="location-outline"
            label="產地"
            value={product.origin ?? "合作小農聯合供應"}
          />
          <View style={styles.infoDivider} />
          <InfoRow
            icon="leaf-outline"
            label="稅別與供應"
            value={`${product.tax_type === "tax_exempt" ? "免稅農產" : "應稅商品"}・${
              product.is_shippable === false
                ? "僅限現場取貨"
                : "可選現場取貨或物流配送"
            }`}
          />
        </View>

        <View style={styles.quantityRow}>
          <View>
            <Text style={styles.sectionTitle}>選擇數量</Text>
            <Text style={styles.stock}>
              目前可訂 {product.stock ?? 20} {product.unit}
            </Text>
          </View>
          <QuantityControl
            max={product.stock ?? 20}
            onChange={setQuantity}
            value={quantity}
          />
        </View>
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
  content: { padding: spacing.lg },
  name: {
    color: colors.forest,
    fontSize: 34,
    fontWeight: "900",
    letterSpacing: 0.5,
    marginTop: 12,
  },
  origin: { color: colors.muted, fontSize: 12, marginTop: 6 },
  priceRow: {
    alignItems: "flex-end",
    flexDirection: "row",
    justifyContent: "space-between",
    marginTop: spacing.lg,
  },
  priceLabel: { color: colors.muted, fontSize: 12 },
  price: {
    color: colors.orange,
    fontSize: 35,
    fontWeight: "900",
    marginTop: 2,
  },
  saving: {
    backgroundColor: colors.orangeSoft,
    borderRadius: radii.sm,
    paddingHorizontal: 10,
    paddingVertical: 7,
  },
  savingText: { color: colors.danger, fontSize: 12, fontWeight: "800" },
  divider: {
    backgroundColor: colors.line,
    height: 1,
    marginVertical: spacing.lg,
  },
  sectionTitle: { color: colors.forest, fontSize: 18, fontWeight: "900" },
  description: {
    color: colors.charcoal,
    fontSize: 14,
    lineHeight: 23,
    marginTop: 8,
  },
  infoPanel: {
    backgroundColor: colors.paper,
    borderRadius: radii.md,
    gap: 12,
    marginTop: spacing.lg,
    padding: spacing.md,
  },
  infoDivider: { backgroundColor: colors.line, height: 1 },
  quantityRow: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    marginTop: spacing.lg,
  },
  stock: { color: colors.muted, fontSize: 12, marginTop: 4 },
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
