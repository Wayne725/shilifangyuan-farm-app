import { Ionicons } from "@expo/vector-icons";
import { useQuery } from "@tanstack/react-query";
import { router } from "expo-router";
import { Image, Pressable, StyleSheet, Text, View } from "react-native";

import {
  Button,
  EmptyState,
  LoadingState,
  PageHeader,
  QuantityControl,
  Screen,
} from "../../src/components/ui";
import { money } from "../../src/lib/format";
import { imageFor } from "../../src/lib/images";
import { api } from "../../src/services/api";
import { useAuth } from "../../src/store/AuthContext";
import { useCart } from "../../src/store/CartContext";
import { colors, radii, spacing } from "../../src/theme";

export default function CartScreen() {
  const { user, isAuthenticated } = useAuth();
  const { items, setQuantity, removeItem, totalFor } = useCart();
  const query = useQuery({ queryKey: ["products"], queryFn: api.products });
  const products = query.data ?? [];
  const lines = items.flatMap((item) => {
    const product = products.find(
      (candidate) => candidate.id === item.product_id,
    );
    return product ? [{ item, product }] : [];
  });
  const total = totalFor(products);
  // Shipping is limited to one temperature zone per order; warn here rather
  // than at the last step of checkout.
  const mixedTemperature =
    new Set(
      lines.map(({ product }) => product.temperature_zone ?? "ambient"),
    ).size > 1;

  return (
    <Screen
      bottom={
        lines.length ? (
          <View style={styles.bottom}>
            <View>
              <Text style={styles.bottomLabel}>合計</Text>
              <Text style={styles.bottomTotal}>{money(total)}</Text>
            </View>
            <Button
              icon="card-outline"
              label="前往結帳"
              onPress={() =>
                isAuthenticated
                  ? router.push("/checkout")
                  : router.push("/login")
              }
            />
          </View>
        ) : null
      }
    >
      <PageHeader
        subtitle={
          user
            ? `目前套用${user.membership_type === "member" ? "社員" : "非社員"}價格`
            : "目前顯示非社員價格，登入後結帳"
        }
        title="購物車"
      />
      {query.isLoading ? (
        <LoadingState label="整理購物車" />
      ) : lines.length ? (
        <View style={styles.content}>
          {lines.map(({ item, product }) => {
            const unitPrice =
              user?.membership_type === "member"
                ? product.member_price
                : product.nonmember_price;
            return (
              <View key={product.id} style={styles.line}>
                <Image
                  source={imageFor(product.image_key ?? product.id)}
                  style={styles.image}
                />
                <View style={styles.lineCopy}>
                  <View style={styles.lineTop}>
                    <View style={styles.nameWrap}>
                      <Text style={styles.name}>{product.name}</Text>
                      <Text style={styles.price}>
                        {money(unitPrice)}／{product.unit}
                      </Text>
                    </View>
                    <Pressable
                      accessibilityLabel={`移除${product.name}`}
                      onPress={() => removeItem(product.id)}
                      style={styles.remove}
                    >
                      <Ionicons
                        color={colors.muted}
                        name="trash-outline"
                        size={18}
                      />
                    </Pressable>
                  </View>
                  <View style={styles.lineBottom}>
                    <QuantityControl
                      max={product.stock ?? 20}
                      onChange={(quantity) =>
                        setQuantity(product.id, quantity)
                      }
                      value={item.quantity}
                    />
                    <Text style={styles.subtotal}>
                      {money(unitPrice * item.quantity)}
                    </Text>
                  </View>
                </View>
              </View>
            );
          })}
          <View style={styles.pickup}>
            <Ionicons
              color={colors.forest}
              name="storefront-outline"
              size={21}
            />
            <View style={styles.pickupCopy}>
              <Text style={styles.pickupTitle}>
                下一步選擇現場取貨或物流配送
              </Text>
              <Text style={styles.pickupText}>
                {mixedTemperature
                  ? "購物車含不同溫層商品，物流需分開結帳；現場取貨不受限制。"
                  : "結帳時可選合作社取貨，或宅配與超商取貨（運費另計）。"}
              </Text>
            </View>
          </View>
        </View>
      ) : (
        <EmptyState
          action={isAuthenticated ? "前往選購" : "登入後選購"}
          description="把需要的農產加入購物車，再一起完成付款。"
          icon="basket-outline"
          onAction={() =>
            router.push(isAuthenticated ? "/(tabs)/home" : "/login")
          }
          title="購物車是空的"
        />
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  content: { gap: 11, padding: spacing.md },
  line: {
    backgroundColor: colors.paper,
    borderRadius: radii.md,
    flexDirection: "row",
    padding: 11,
  },
  image: { borderRadius: radii.sm, height: 84, width: 84 },
  lineCopy: { flex: 1, marginLeft: 11 },
  lineTop: { flexDirection: "row", justifyContent: "space-between" },
  nameWrap: { flex: 1 },
  name: { color: colors.forest, fontSize: 14, fontWeight: "900" },
  price: { color: colors.muted, fontSize: 13, marginTop: 4 },
  remove: { padding: 4 },
  lineBottom: {
    alignItems: "flex-end",
    flexDirection: "row",
    justifyContent: "space-between",
    marginTop: 10,
  },
  subtotal: { color: colors.forest, fontSize: 16, fontWeight: "900" },
  pickup: {
    alignItems: "center",
    backgroundColor: colors.sageLight,
    borderRadius: radii.md,
    flexDirection: "row",
    padding: 13,
  },
  pickupCopy: { flex: 1, marginLeft: 10 },
  pickupTitle: { color: colors.forest, fontSize: 14, fontWeight: "900" },
  pickupText: { color: colors.muted, fontSize: 13, marginTop: 3 },
  bottom: {
    alignItems: "center",
    backgroundColor: colors.paper,
    borderTopColor: colors.line,
    borderTopWidth: 1,
    flexDirection: "row",
    justifyContent: "space-between",
    padding: spacing.md,
  },
  bottomLabel: { color: colors.muted, fontSize: 13 },
  bottomTotal: { color: colors.forest, fontSize: 24, fontWeight: "900" },
});
