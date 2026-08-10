import { Ionicons } from "@expo/vector-icons";
import { useQuery } from "@tanstack/react-query";
import { router } from "expo-router";
import { useMemo, useState } from "react";
import {
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import {
  CatalogCard,
  EmptyState,
  InlineMessage,
  LoadingState,
  PageHeader,
  ProductCard,
  Screen,
  SectionHeader,
  SegmentControl,
} from "../../src/components/ui";
import { campaignLabels, money, shortDate } from "../../src/lib/format";
import { hasMemberPricing } from "../../src/lib/membership";
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
  const featuredCampaigns = (campaignsQuery.data ?? [])
    .filter((campaign) => campaign.intake_status === "open")
    .slice(0, 2);
  const membership = user?.membership_type ?? "nonmember";
  const memberPricing = hasMemberPricing(membership);

  return (
    <Screen>
      <PageHeader
        subtitle="選購在地農產，也能一起累積共同購買的需要。"
        title="本週選物"
      />

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

      <View style={styles.section}>
        <SectionHeader
          action="查看全部"
          onAction={() => router.push("/(tabs)/group-buy")}
          title="進行中的團購"
        />
        {campaignsQuery.isError ? (
          <InlineMessage text="目前無法載入團購，請稍後再試。" tone="danger" />
        ) : (
          <View style={styles.gridWithoutPadding}>
            {featuredCampaigns.map((campaign) => (
              <CatalogCard
                badge={campaignLabels[campaign.decision_status]}
                imageKey={campaign.image_key}
                imageUrl={campaign.image_url}
                key={campaign.id}
                meta={`${shortDate(campaign.deadline)} 截止`}
                onPress={() =>
                  router.push({
                    pathname: "/campaign/[id]",
                    params: { id: campaign.id },
                  })
                }
                price={money(
                  memberPricing
                    ? campaign.member_price
                    : campaign.nonmember_price,
                )}
                priceLabel={memberPricing ? "社員價" : "一般價"}
                progress={
                  (campaign.paid_quantity / campaign.min_paid_quantity) * 100
                }
                progressLabel={`${campaign.paid_quantity}/${campaign.min_paid_quantity} 組`}
                title={campaign.title.replace("共同購買", "")}
              />
            ))}
          </View>
        )}
      </View>

      <View style={styles.sectionTitleOnly}>
        <SectionHeader title="產地選物" />
      </View>
      <SegmentControl
        onChange={setCategory}
        options={categoryOptions}
        value={category}
      />

      {productsQuery.isLoading ? (
        <LoadingState label="正在整理本週農產" />
      ) : productsQuery.isError ? (
        <EmptyState
          action="重新載入"
          description="目前無法取得商品資料，請稍後再試。"
          icon="cloud-offline-outline"
          onAction={() => productsQuery.refetch()}
          title="商品載入失敗"
        />
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
          {memberPricing ? "社員價" : "一般價"}
        </Text>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
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
  section: {
    paddingHorizontal: spacing.md,
    paddingTop: spacing.lg,
  },
  sectionTitleOnly: {
    paddingHorizontal: spacing.md,
    paddingTop: spacing.lg,
  },
  gridWithoutPadding: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 12,
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
    fontSize: 12,
    lineHeight: 15,
    marginTop: 2,
  },
  priceHint: { color: colors.forest, fontSize: 12, fontWeight: "900" },
});
