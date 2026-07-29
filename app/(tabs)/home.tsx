import { Ionicons } from "@expo/vector-icons";
import { useQuery } from "@tanstack/react-query";
import { router } from "expo-router";
import { useMemo, useState } from "react";
import {
  ImageBackground,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import {
  BrandLockup,
  IconButton,
  LoadingState,
  ProductCard,
  ProgressBar,
  Screen,
  SectionHeader,
  SegmentControl,
  StatusPill,
} from "../../src/components/ui";
import { campaignLabels, money, shortDate } from "../../src/lib/format";
import { imageFor } from "../../src/lib/images";
import { api } from "../../src/services/api";
import { useAuth } from "../../src/store/AuthContext";
import { useCart } from "../../src/store/CartContext";
import { colors, radii, spacing } from "../../src/theme";
import type { Category } from "../../src/types";

type CategoryFilter = "全部" | Category;

const categoryOptions: { value: CategoryFilter; label: string }[] = [
  { value: "全部", label: "全部" },
  { value: "當季蔬果", label: "當季蔬果" },
  { value: "米・雜糧", label: "米・雜糧" },
  { value: "蛋品", label: "蛋品" },
  { value: "加工品", label: "加工品" },
];

export default function HomeScreen() {
  const { user, isAuthenticated } = useAuth();
  const { addItem } = useCart();
  const [category, setCategory] = useState<CategoryFilter>("全部");
  const [keyword, setKeyword] = useState("");
  const productsQuery = useQuery({
    queryKey: ["products"],
    queryFn: api.products,
  });
  const campaignsQuery = useQuery({
    queryKey: ["campaigns"],
    queryFn: api.campaigns,
  });
  const notificationsQuery = useQuery({
    queryKey: ["notifications"],
    queryFn: api.notifications,
    enabled: isAuthenticated,
  });

  const products = useMemo(
    () =>
      (productsQuery.data ?? []).filter(
        (product) =>
          product.is_active &&
          (category === "全部" || product.category === category) &&
          (!keyword.trim() ||
            `${product.name}${product.origin ?? ""}`.includes(keyword.trim())),
      ),
    [category, keyword, productsQuery.data],
  );
  const featuredCampaign = (campaignsQuery.data ?? []).find(
    (campaign) => campaign.intake_status === "open",
  );
  const unread = (notificationsQuery.data ?? []).filter(
    (notice) => !notice.read_at,
  ).length;
  const membership = user?.membership_type ?? "nonmember";

  return (
    <Screen>
      <View style={styles.topbar}>
        <BrandLockup />
        <View style={styles.topActions}>
          {isAuthenticated ? (
            <IconButton
              badge={unread}
              icon="notifications-outline"
              label="通知"
              onPress={() => router.push("/notifications")}
            />
          ) : (
            <Pressable
              onPress={() => router.push("/login")}
              style={styles.loginButton}
            >
              <Text style={styles.loginText}>登入</Text>
            </Pressable>
          )}
        </View>
      </View>

      <View style={styles.search}>
        <Ionicons color={colors.muted} name="search" size={18} />
        <TextInput
          onChangeText={setKeyword}
          placeholder="找農產、產地或加工品"
          placeholderTextColor={colors.muted}
          style={styles.searchInput}
          value={keyword}
        />
      </View>

      {featuredCampaign ? (
        <Pressable
          onPress={() =>
            router.push({
              pathname: "/campaign/[id]",
              params: { id: featuredCampaign.id },
            })
          }
          style={({ pressed }) => [
            styles.hero,
            pressed && styles.pressed,
          ]}
        >
          <ImageBackground
            imageStyle={styles.heroImage}
            source={imageFor(
              featuredCampaign.image_key,
              featuredCampaign.image_url,
            )}
            style={styles.heroImage}
          >
            <View style={styles.heroOverlay}>
              <StatusPill
                label={campaignLabels[featuredCampaign.decision_status]}
                tone="warning"
              />
              <View>
                <Text style={styles.heroEyebrow}>本週共同購買</Text>
                <Text style={styles.heroTitle}>
                  {featuredCampaign.title.replace("共同購買", "")}
                </Text>
                <Text style={styles.heroMeta}>
                  {featuredCampaign.paid_quantity}／
                  {featuredCampaign.min_paid_quantity} 組・
                  {shortDate(featuredCampaign.deadline)} 截止
                </Text>
                <ProgressBar
                  color="#E9A06D"
                  value={
                    (featuredCampaign.paid_quantity /
                      featuredCampaign.min_paid_quantity) *
                    100
                  }
                />
              </View>
            </View>
          </ImageBackground>
        </Pressable>
      ) : null}

      <View style={styles.section}>
        <SectionHeader
          action="看團購"
          onAction={() => router.push("/(tabs)/group-buy")}
          title="產地選物"
        />
      </View>
      <SegmentControl
        onChange={setCategory}
        options={categoryOptions}
        value={category}
      />

      {productsQuery.isLoading ? (
        <LoadingState label="正在整理本週農產" />
      ) : (
        <View style={styles.grid}>
          {products.map((product) => (
            <ProductCard
              compact
              key={product.id}
              membership={membership}
              onAdd={() =>
                isAuthenticated
                  ? addItem(product.id)
                  : router.push("/login")
              }
              onPress={() =>
                router.push({
                  pathname: "/product/[id]",
                  params: { id: product.id },
                })
              }
              product={product}
            />
          ))}
        </View>
      )}

      {products.length === 0 && !productsQuery.isLoading ? (
        <View style={styles.noResult}>
          <Text style={styles.noResultTitle}>沒有符合的商品</Text>
          <Text style={styles.noResultText}>換個分類或關鍵字再找找看。</Text>
        </View>
      ) : null}

      <View style={styles.pickupStrip}>
        <View style={styles.pickupIcon}>
          <Ionicons color={colors.forest} name="storefront-outline" size={21} />
        </View>
        <View style={styles.pickupCopy}>
          <Text style={styles.pickupTitle}>合作社現場取貨</Text>
          <Text style={styles.pickupBody}>
            完成付款後，依訂單通知的時間前來取貨。
          </Text>
        </View>
        <Text style={styles.priceHint}>
          {membership === "member" ? "社員價" : "一般價"}
        </Text>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  topbar: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    padding: spacing.md,
    paddingTop: spacing.lg,
  },
  topActions: { flexDirection: "row" },
  loginButton: {
    backgroundColor: colors.forest,
    borderRadius: radii.pill,
    paddingHorizontal: 16,
    paddingVertical: 9,
  },
  loginText: { color: colors.white, fontSize: 12, fontWeight: "900" },
  search: {
    alignItems: "center",
    backgroundColor: colors.paper,
    borderColor: colors.line,
    borderRadius: radii.md,
    borderWidth: 1,
    flexDirection: "row",
    marginHorizontal: spacing.md,
    paddingHorizontal: 14,
  },
  searchInput: {
    color: colors.charcoal,
    flex: 1,
    fontSize: 13,
    minHeight: 48,
    paddingLeft: 9,
  },
  hero: {
    borderRadius: radii.lg,
    margin: spacing.md,
    overflow: "hidden",
  },
  heroImage: { height: 235, width: "100%" },
  heroOverlay: {
    backgroundColor: "rgba(20,53,44,0.60)",
    flex: 1,
    justifyContent: "space-between",
    padding: spacing.md,
  },
  heroEyebrow: {
    color: "#EBD2B9",
    fontSize: 10,
    fontWeight: "900",
    letterSpacing: 1.5,
  },
  heroTitle: {
    color: colors.white,
    fontSize: 29,
    fontWeight: "900",
    marginTop: 5,
  },
  heroMeta: {
    color: "#F4EEE6",
    fontSize: 11,
    marginBottom: 9,
    marginTop: 7,
  },
  section: {
    paddingHorizontal: spacing.md,
    paddingTop: spacing.sm,
  },
  grid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 12,
    padding: spacing.md,
  },
  noResult: { alignItems: "center", padding: spacing.xl },
  noResultTitle: { color: colors.forest, fontSize: 16, fontWeight: "900" },
  noResultText: { color: colors.muted, fontSize: 12, marginTop: 5 },
  pickupStrip: {
    alignItems: "center",
    backgroundColor: colors.sageLight,
    borderRadius: radii.lg,
    flexDirection: "row",
    margin: spacing.md,
    padding: 14,
  },
  pickupIcon: {
    alignItems: "center",
    backgroundColor: colors.paper,
    borderRadius: 12,
    height: 42,
    justifyContent: "center",
    width: 42,
  },
  pickupCopy: { flex: 1, marginLeft: 11 },
  pickupTitle: { color: colors.forest, fontSize: 13, fontWeight: "900" },
  pickupBody: {
    color: colors.muted,
    fontSize: 10,
    lineHeight: 15,
    marginTop: 2,
  },
  priceHint: { color: colors.forest, fontSize: 10, fontWeight: "900" },
  pressed: { opacity: 0.85, transform: [{ scale: 0.99 }] },
});
