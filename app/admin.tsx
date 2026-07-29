import { Ionicons } from "@expo/vector-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { router } from "expo-router";
import { useMemo, useState } from "react";
import {
  Pressable,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from "react-native";

import {
  BrandLockup,
  Button,
  EmptyState,
  InlineMessage,
  LoadingState,
  Screen,
  SegmentControl,
  StatusPill,
} from "../src/components/ui";
import {
  campaignLabels,
  fulfillmentLabels,
  money,
  paymentLabels,
  proposalLabels,
} from "../src/lib/format";
import { api, getApiBaseUrl, getErrorMessage } from "../src/services/api";
import { useAuth } from "../src/store/AuthContext";
import { colors, radii, spacing } from "../src/theme";
import type { Category, Order, TaxType } from "../src/types";

type AdminTab = "overview" | "orders" | "products" | "groups" | "settings";

const tabs: { value: AdminTab; label: string }[] = [
  { value: "overview", label: "總覽" },
  { value: "orders", label: "訂單" },
  { value: "products", label: "商品" },
  { value: "groups", label: "團購" },
  { value: "settings", label: "設定" },
];

const productCategories: Category[] = [
  "當季蔬果",
  "米・雜糧",
  "蛋品",
  "加工品",
];

type ProductDraft = {
  name: string;
  description: string;
  category: Category;
  unit: string;
  memberPrice: string;
  nonmemberPrice: string;
  stock: string;
  taxType: TaxType;
};

const emptyProductDraft: ProductDraft = {
  name: "",
  description: "",
  category: "當季蔬果",
  unit: "",
  memberPrice: "",
  nonmemberPrice: "",
  stock: "",
  taxType: "taxable",
};

export default function AdminScreen() {
  const [tab, setTab] = useState<AdminTab>("overview");
  const { user, isAdmin, logout } = useAuth();
  const queryClient = useQueryClient();
  const products = useQuery({ queryKey: ["products"], queryFn: api.products });
  const proposals = useQuery({
    queryKey: ["proposals"],
    queryFn: api.proposals,
  });
  const campaigns = useQuery({
    queryKey: ["campaigns"],
    queryFn: api.campaigns,
  });
  const orders = useQuery({ queryKey: ["orders"], queryFn: api.orders });
  const bundles = useQuery({ queryKey: ["bundles"], queryFn: api.bundles });
  const [message, setMessage] = useState("");
  const [productDraft, setProductDraft] =
    useState<ProductDraft>(emptyProductDraft);
  const [productFormError, setProductFormError] = useState("");
  const [resetConfirmation, setResetConfirmation] = useState("");

  const refresh = async () => {
    await queryClient.invalidateQueries();
  };
  const toggleProduct = useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) =>
      api.toggleProduct(id, active),
    onSuccess: refresh,
  });
  const createProduct = useMutation({
    mutationFn: api.createProduct,
    onSuccess: async () => {
      setProductDraft(emptyProductDraft);
      setProductFormError("");
      setMessage("商品已上架");
      await refresh();
    },
  });
  const reviewProposal = useMutation({
    mutationFn: ({
      id,
      action,
    }: {
      id: string;
      action: "approve" | "reject";
    }) =>
      api.reviewProposal(id, action, {
        threshold: 10,
        deadline: new Date(Date.now() + 7 * 86400000).toISOString(),
        ...(action === "reject" ? { reason: "目前供應條件不適合開放" } : {}),
      }),
    onSuccess: async () => {
      setMessage("提案狀態已更新");
      await refresh();
    },
  });
  const confirmCampaign = useMutation({
    mutationFn: ({
      id,
      pickupAt,
    }: {
      id: string;
      pickupAt: string;
    }) => api.confirmCampaign(id, pickupAt),
    onSuccess: async () => {
      setMessage("已確認成團並發布取貨時間");
      await refresh();
    },
  });
  const convertProposal = useMutation({
    mutationFn: (proposalId: string) => {
      const proposal = (proposals.data ?? []).find(
        (item) => item.id === proposalId,
      );
      if (!proposal) throw new Error("找不到提案");
      const target =
        proposal.target_type === "product"
          ? (products.data ?? []).find(
              (item) => item.id === proposal.target_id,
            )
          : (bundles.data ?? []).find(
              (item) => item.id === proposal.target_id,
            );
      if (!target) throw new Error("找不到提案項目");
      const now = Date.now();
      return api.convertProposal(proposal.id, {
        source_proposal_id: proposal.id,
        target_type: proposal.target_type,
        target_id: proposal.target_id,
        title: `${proposal.target_name}共同購買`,
        description: "依投票需求開放付款，達門檻後由合作社確認成團。",
        image_url: target.image_url,
        member_price: Math.round(target.member_price * 0.92),
        nonmember_price: Math.round(target.nonmember_price * 0.95),
        min_paid_quantity: 10,
        supply_cap: 30,
        per_user_cap: 5,
        deadline: new Date(now + 7 * 86400000).toISOString(),
        estimated_pickup_start: new Date(now + 10 * 86400000).toISOString(),
        estimated_pickup_end: new Date(now + 11 * 86400000).toISOString(),
      });
    },
    onSuccess: async () => {
      setMessage("已建立正式團購");
      await refresh();
    },
  });
  const advanceOrder = useMutation({
    mutationFn: ({
      id,
      status,
    }: {
      id: string;
      status: Order["fulfillment_status"];
    }) => api.advanceOrder(id, status),
    onSuccess: refresh,
  });
  const refundOrder = useMutation({
    mutationFn: (id: string) =>
      api.adminRefundOrder(id, "管理員核准全額退款"),
    onSuccess: async () => {
      setMessage("訂單已進入退款處理");
      await refresh();
    },
  });
  const rejectCampaign = useMutation({
    mutationFn: (id: string) =>
      api.rejectCampaign(id, "供應條件無法確認，管理員拒絕成團"),
    onSuccess: async () => {
      setMessage("已拒絕成團並啟動退款");
      await refresh();
    },
  });
  const reset = useMutation({
    mutationFn: api.resetDemo,
    onSuccess: async () => {
      setResetConfirmation("");
      setMessage("展示資料已恢復為初始狀態");
      await refresh();
    },
  });

  const summary = useMemo(
    () => ({
      pendingOrders: (orders.data ?? []).filter(
        (order) => order.fulfillment_status === "pending_confirmation",
      ).length,
      activeCampaigns: (campaigns.data ?? []).filter(
        (campaign) => campaign.intake_status === "open",
      ).length,
      pendingProposals: (proposals.data ?? []).filter(
        (proposal) => proposal.status === "pending_review",
      ).length,
      pickupOrders: (orders.data ?? []).filter(
        (order) => order.fulfillment_status === "ready_for_pickup",
      ).length,
    }),
    [campaigns.data, orders.data, proposals.data],
  );
  const loading =
    products.isLoading ||
    bundles.isLoading ||
    proposals.isLoading ||
    campaigns.isLoading ||
    orders.isLoading;
  const mutationError =
    createProduct.error ??
    toggleProduct.error ??
    reviewProposal.error ??
    confirmCampaign.error ??
    convertProposal.error ??
    advanceOrder.error ??
    refundOrder.error ??
    rejectCampaign.error ??
    reset.error;

  const submitProduct = () => {
    const name = productDraft.name.trim();
    const unit = productDraft.unit.trim();
    const memberPrice = Number(productDraft.memberPrice);
    const nonmemberPrice = Number(productDraft.nonmemberPrice);
    const stock = Number(productDraft.stock);
    if (!name || !unit) {
      setProductFormError("請填寫商品名稱與單位");
      return;
    }
    if (
      !productDraft.memberPrice ||
      !productDraft.nonmemberPrice ||
      !productDraft.stock ||
      ![memberPrice, nonmemberPrice, stock].every(
        (value) => Number.isInteger(value) && value >= 0,
      )
    ) {
      setProductFormError("價格與庫存請輸入大於或等於 0 的整數");
      return;
    }
    if (memberPrice > nonmemberPrice) {
      setProductFormError("社員價不可高於非社員價");
      return;
    }
    setProductFormError("");
    createProduct.mutate({
      name,
      description: productDraft.description.trim(),
      category: productDraft.category,
      unit,
      member_price: memberPrice,
      nonmember_price: nonmemberPrice,
      stock_quantity: stock,
      tax_type: productDraft.taxType,
    });
  };

  if (!isAdmin || !user) {
    return (
      <Screen>
        <EmptyState
          action="管理員登入"
          description="這個區域只開放管理帳號使用。"
          icon="lock-closed-outline"
          onAction={() => router.replace("/login")}
          title="需要管理權限"
        />
      </Screen>
    );
  }

  return (
    <Screen>
      <View style={styles.header}>
        <BrandLockup light />
        <Pressable
          onPress={() => router.push("/(tabs)/home")}
          style={styles.storeButton}
        >
          <Ionicons color={colors.forest} name="storefront-outline" size={17} />
          <Text style={styles.storeButtonText}>顧客畫面</Text>
        </Pressable>
      </View>
      <View style={styles.adminTitleRow}>
        <View>
          <Text style={styles.eyebrow}>OPERATIONS</Text>
          <Text style={styles.adminTitle}>合作社管理</Text>
        </View>
        <Text style={styles.operator}>{user.display_name}</Text>
      </View>
      <SegmentControl onChange={setTab} options={tabs} value={tab} />

      {message ? (
        <View style={styles.messageWrap}>
          <InlineMessage text={message} tone="positive" />
        </View>
      ) : null}
      {mutationError ? (
        <View style={styles.messageWrap}>
          <InlineMessage
            text={getErrorMessage(mutationError)}
            tone="danger"
          />
        </View>
      ) : null}
      {loading ? <LoadingState label="載入管理資料" /> : null}

      {!loading && tab === "overview" ? (
        <View style={styles.content}>
          <View style={styles.metrics}>
            {[
              {
                label: "待確認訂單",
                value: summary.pendingOrders,
                icon: "receipt-outline" as const,
              },
              {
                label: "進行中團購",
                value: summary.activeCampaigns,
                icon: "people-outline" as const,
              },
              {
                label: "待審核提案",
                value: summary.pendingProposals,
                icon: "chatbubbles-outline" as const,
              },
              {
                label: "待取貨",
                value: summary.pickupOrders,
                icon: "storefront-outline" as const,
              },
            ].map((metric) => (
              <Pressable
                key={metric.label}
                onPress={() =>
                  setTab(
                    metric.label.includes("團購") ||
                      metric.label.includes("提案")
                      ? "groups"
                      : "orders",
                  )
                }
                style={styles.metric}
              >
                <Ionicons color={colors.forest} name={metric.icon} size={21} />
                <Text style={styles.metricValue}>{metric.value}</Text>
                <Text style={styles.metricLabel}>{metric.label}</Text>
              </Pressable>
            ))}
          </View>
          <View style={styles.noticePanel}>
            <Text style={styles.sectionTitle}>今日處理順序</Text>
            <Text style={styles.noticeText}>
              先確認已達門檻的團購，再處理待確認訂單與可取貨通知。
            </Text>
            <Button
              compact
              label="查看團購"
              onPress={() => setTab("groups")}
              variant="secondary"
            />
          </View>
        </View>
      ) : null}

      {!loading && tab === "orders" ? (
        <View style={styles.content}>
          {(orders.data ?? []).map((order) => (
            <AdminOrderRow
              key={order.id}
              loading={advanceOrder.isPending}
              onAdvance={(status) =>
                advanceOrder.mutate({ id: order.id, status })
              }
              onRefund={() => refundOrder.mutate(order.id)}
              refunding={refundOrder.isPending}
              order={order}
            />
          ))}
        </View>
      ) : null}

      {!loading && tab === "products" ? (
        <View style={styles.content}>
          <View style={styles.productForm}>
            <View style={styles.formHeading}>
              <View>
                <Text style={styles.sectionTitle}>上架新商品</Text>
                <Text style={styles.rowMeta}>商品代碼會由系統自動建立</Text>
              </View>
              <Ionicons
                color={colors.orange}
                name="add-circle-outline"
                size={27}
              />
            </View>
            <View style={styles.field}>
              <Text style={styles.fieldLabel}>商品名稱</Text>
              <TextInput
                onChangeText={(name) =>
                  setProductDraft((draft) => ({ ...draft, name }))
                }
                placeholder="例如：友善栽培高麗菜"
                placeholderTextColor={colors.sage}
                style={styles.input}
                value={productDraft.name}
              />
            </View>
            <View style={styles.field}>
              <Text style={styles.fieldLabel}>商品介紹</Text>
              <TextInput
                multiline
                numberOfLines={3}
                onChangeText={(description) =>
                  setProductDraft((draft) => ({ ...draft, description }))
                }
                placeholder="產地、栽培方式或適合的料理方式"
                placeholderTextColor={colors.sage}
                style={[styles.input, styles.descriptionInput]}
                textAlignVertical="top"
                value={productDraft.description}
              />
            </View>
            <View style={styles.field}>
              <Text style={styles.fieldLabel}>分類</Text>
              <View style={styles.categoryRow}>
                {productCategories.map((category) => {
                  const selected = productDraft.category === category;
                  return (
                    <Pressable
                      accessibilityRole="button"
                      key={category}
                      onPress={() =>
                        setProductDraft((draft) => ({
                          ...draft,
                          category,
                        }))
                      }
                      style={[
                        styles.categoryButton,
                        selected && styles.categoryButtonSelected,
                      ]}
                    >
                      <Text
                        style={[
                          styles.categoryLabel,
                          selected && styles.categoryLabelSelected,
                        ]}
                      >
                        {category}
                      </Text>
                    </Pressable>
                  );
                })}
              </View>
            </View>
            <View style={styles.field}>
              <Text style={styles.fieldLabel}>販售單位</Text>
              <TextInput
                onChangeText={(unit) =>
                  setProductDraft((draft) => ({ ...draft, unit }))
                }
                placeholder="盒、包、袋、台斤"
                placeholderTextColor={colors.sage}
                style={styles.input}
                value={productDraft.unit}
              />
            </View>
            <View style={styles.fieldRow}>
              <View style={styles.fieldHalf}>
                <Text style={styles.fieldLabel}>社員價</Text>
                <TextInput
                  keyboardType="number-pad"
                  onChangeText={(memberPrice) =>
                    setProductDraft((draft) => ({
                      ...draft,
                      memberPrice,
                    }))
                  }
                  placeholder="0"
                  placeholderTextColor={colors.sage}
                  style={styles.input}
                  value={productDraft.memberPrice}
                />
              </View>
              <View style={styles.fieldHalf}>
                <Text style={styles.fieldLabel}>非社員價</Text>
                <TextInput
                  keyboardType="number-pad"
                  onChangeText={(nonmemberPrice) =>
                    setProductDraft((draft) => ({
                      ...draft,
                      nonmemberPrice,
                    }))
                  }
                  placeholder="0"
                  placeholderTextColor={colors.sage}
                  style={styles.input}
                  value={productDraft.nonmemberPrice}
                />
              </View>
            </View>
            <View style={styles.fieldRow}>
              <View style={styles.fieldHalf}>
                <Text style={styles.fieldLabel}>庫存</Text>
                <TextInput
                  keyboardType="number-pad"
                  onChangeText={(stock) =>
                    setProductDraft((draft) => ({ ...draft, stock }))
                  }
                  placeholder="0"
                  placeholderTextColor={colors.sage}
                  style={styles.input}
                  value={productDraft.stock}
                />
              </View>
              <View style={styles.fieldHalf}>
                <Text style={styles.fieldLabel}>稅別</Text>
                <View style={styles.taxRow}>
                  {[
                    { value: "taxable" as const, label: "應稅" },
                    { value: "tax_exempt" as const, label: "免稅" },
                  ].map((option) => {
                    const selected = productDraft.taxType === option.value;
                    return (
                      <Pressable
                        key={option.value}
                        onPress={() =>
                          setProductDraft((draft) => ({
                            ...draft,
                            taxType: option.value,
                          }))
                        }
                        style={[
                          styles.taxButton,
                          selected && styles.taxButtonSelected,
                        ]}
                      >
                        <Text
                          style={[
                            styles.taxLabel,
                            selected && styles.taxLabelSelected,
                          ]}
                        >
                          {option.label}
                        </Text>
                      </Pressable>
                    );
                  })}
                </View>
              </View>
            </View>
            {productFormError ? (
              <InlineMessage text={productFormError} tone="danger" />
            ) : null}
            <Button
              icon="cloud-upload-outline"
              label="上架商品"
              loading={createProduct.isPending}
              onPress={submitProduct}
            />
          </View>
          <View style={styles.productListHeading}>
            <Text style={styles.sectionTitle}>商品清單</Text>
            <Text style={styles.productCount}>
              共 {(products.data ?? []).length} 項
            </Text>
          </View>
          {(products.data ?? []).map((product) => (
            <View
              key={product.id}
              style={[
                styles.productRow,
                !product.is_active && styles.productRowInactive,
              ]}
            >
              <View style={styles.productCopy}>
                <View style={styles.productNameRow}>
                  <Text style={styles.rowTitle}>{product.name}</Text>
                  <StatusPill
                    label={product.is_active ? "上架中" : "已下架"}
                    tone={product.is_active ? "positive" : "neutral"}
                  />
                </View>
                <Text style={styles.rowMeta}>
                  {product.category}・社員 {money(product.member_price)}・非社員{" "}
                  {money(product.nonmember_price)}
                </Text>
                <Text style={styles.rowMeta}>
                  庫存 {product.stock_quantity ?? product.stock ?? 0}{" "}
                  {product.unit}・
                  {product.tax_type === "taxable" ? "應稅" : "免稅"}
                </Text>
              </View>
              <Switch
                disabled={toggleProduct.isPending}
                onValueChange={(active) =>
                  toggleProduct.mutate({ id: product.id, active })
                }
                thumbColor={colors.paper}
                trackColor={{
                  false: colors.sage,
                  true: colors.forestSoft,
                }}
                value={product.is_active}
              />
            </View>
          ))}
        </View>
      ) : null}

      {!loading && tab === "groups" ? (
        <View style={styles.content}>
          <Text style={styles.sectionTitle}>待審核提案</Text>
          {(proposals.data ?? [])
            .filter((proposal) => proposal.status === "pending_review")
            .map((proposal) => (
              <View key={proposal.id} style={styles.adminCard}>
                <View style={styles.cardHeading}>
                  <Text style={styles.rowTitle}>{proposal.target_name}</Text>
                  <StatusPill label={proposalLabels[proposal.status]} tone="warning" />
                </View>
                <Text style={styles.rowMeta}>
                  {proposal.target_type === "bundle" ? "固定套組" : "單一商品"}
                </Text>
                <View style={styles.actions}>
                  <Button
                    compact
                    label="通過並開放投票"
                    loading={reviewProposal.isPending}
                    onPress={() =>
                      reviewProposal.mutate({
                        id: proposal.id,
                        action: "approve",
                      })
                    }
                  />
                  <Button
                    compact
                    label="不通過"
                    onPress={() =>
                      reviewProposal.mutate({
                        id: proposal.id,
                        action: "reject",
                      })
                    }
                    variant="quiet"
                  />
                </View>
              </View>
            ))}
          <Text style={styles.sectionTitle}>正式團購</Text>
          {(proposals.data ?? [])
            .filter(
              (proposal) =>
                proposal.status === "conversion_pending" ||
                (proposal.status === "voting" &&
                  proposal.vote_count >= proposal.threshold),
            )
            .map((proposal) => (
              <View key={`convert-${proposal.id}`} style={styles.adminCard}>
                <View style={styles.cardHeading}>
                  <View>
                    <Text style={styles.rowTitle}>{proposal.target_name}</Text>
                    <Text style={styles.rowMeta}>
                      已達 {proposal.vote_count}／{proposal.threshold} 人
                    </Text>
                  </View>
                  <StatusPill label="可正式開團" tone="positive" />
                </View>
                <Button
                  compact
                  label="建立正式團購"
                  loading={convertProposal.isPending}
                  onPress={() => convertProposal.mutate(proposal.id)}
                />
              </View>
            ))}
          {(campaigns.data ?? []).map((campaign) => (
            <View key={campaign.id} style={styles.adminCard}>
              <View style={styles.cardHeading}>
                <Text style={styles.rowTitle}>{campaign.title}</Text>
                <StatusPill
                  label={campaignLabels[campaign.decision_status]}
                  tone={
                    campaign.decision_status === "confirmed"
                      ? "positive"
                      : "neutral"
                  }
                />
              </View>
              <Text style={styles.rowMeta}>
                已付款 {campaign.paid_quantity}／
                {campaign.min_paid_quantity} 組・剩餘{" "}
                {campaign.available_quantity} 組
              </Text>
              {campaign.decision_status === "pending_confirmation" ? (
                <View style={styles.actions}>
                  <Button
                    compact
                    label="確認成團"
                    loading={confirmCampaign.isPending}
                    onPress={() =>
                      confirmCampaign.mutate({
                        id: campaign.id,
                        pickupAt: campaign.estimated_pickup_start,
                      })
                    }
                  />
                  <Button
                    compact
                    label="拒絕成團"
                    loading={rejectCampaign.isPending}
                    onPress={() => rejectCampaign.mutate(campaign.id)}
                    variant="danger"
                  />
                </View>
              ) : null}
            </View>
          ))}
        </View>
      ) : null}

      {!loading && tab === "settings" ? (
        <View style={styles.content}>
          <View style={styles.settingsPanel}>
            <Text style={styles.sectionTitle}>服務設定</Text>
            <Text style={styles.settingLabel}>API 位址</Text>
            <Text style={styles.settingValue}>
              {getApiBaseUrl() || "使用內建資料來源"}
            </Text>
            <Text style={styles.settingLabel}>取貨方式</Text>
            <Text style={styles.settingValue}>合作社現場取貨</Text>
            <Text style={styles.settingLabel}>資料重設確認碼</Text>
            <TextInput
              autoCapitalize="none"
              onChangeText={setResetConfirmation}
              placeholder={
                getApiBaseUrl() ? "由部署管理員保管" : "內建資料確認碼為 RESET"
              }
              placeholderTextColor={colors.sage}
              secureTextEntry
              style={styles.input}
              value={resetConfirmation}
            />
          </View>
          <Button
            disabled={resetConfirmation.trim().length < 4}
            label="重設展示資料"
            loading={reset.isPending}
            onPress={() => reset.mutate(resetConfirmation.trim())}
            variant="danger"
          />
          <Button
            label="登出管理帳號"
            onPress={() => {
              logout();
              queryClient.clear();
              router.replace("/");
            }}
            variant="quiet"
          />
        </View>
      ) : null}
    </Screen>
  );
}

function AdminOrderRow({
  order,
  onAdvance,
  onRefund,
  loading,
  refunding,
}: {
  order: Order;
  onAdvance: (status: Order["fulfillment_status"]) => void;
  onRefund: () => void;
  loading: boolean;
  refunding: boolean;
}) {
  const nextStatus = order.available_actions.includes("start_preparing")
    ? "preparing"
    : order.available_actions.includes("mark_ready")
      ? "ready_for_pickup"
      : order.available_actions.includes("mark_picked_up")
        ? "picked_up"
        : null;
  const canAdvance = Boolean(nextStatus);
  return (
    <View style={styles.adminCard}>
      <View style={styles.cardHeading}>
        <View>
          <Text style={styles.rowTitle}>{order.order_number}</Text>
          <Text style={styles.rowMeta}>
            {order.order_kind === "group" ? "團購" : "一般"}・
            {money(order.amount_total)}
          </Text>
        </View>
        <StatusPill label={paymentLabels[order.payment_status]} />
      </View>
      <View style={styles.orderBottom}>
        <StatusPill label={fulfillmentLabels[order.fulfillment_status]} />
        <View style={styles.actions}>
          {order.available_actions.includes("refund") ? (
            <Button
              compact
              label="全額退款"
              loading={refunding}
              onPress={onRefund}
              variant="danger"
            />
          ) : null}
          {canAdvance ? (
            <Button
              compact
              label="推進狀態"
              loading={loading}
              onPress={() => {
                if (nextStatus) onAdvance(nextStatus);
              }}
              variant="secondary"
            />
          ) : null}
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  header: {
    alignItems: "center",
    backgroundColor: colors.forest,
    flexDirection: "row",
    justifyContent: "space-between",
    padding: spacing.md,
    paddingTop: spacing.lg,
  },
  storeButton: {
    alignItems: "center",
    backgroundColor: colors.paper,
    borderRadius: radii.pill,
    flexDirection: "row",
    gap: 6,
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  storeButtonText: { color: colors.forest, fontSize: 10, fontWeight: "900" },
  adminTitleRow: {
    alignItems: "flex-end",
    flexDirection: "row",
    justifyContent: "space-between",
    padding: spacing.md,
  },
  eyebrow: {
    color: colors.orange,
    fontSize: 9,
    fontWeight: "900",
    letterSpacing: 1.4,
  },
  adminTitle: {
    color: colors.forest,
    fontSize: 27,
    fontWeight: "900",
    marginTop: 2,
  },
  operator: { color: colors.muted, fontSize: 10 },
  messageWrap: { paddingHorizontal: spacing.md, paddingTop: 11 },
  content: { gap: 12, padding: spacing.md },
  metrics: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  metric: {
    backgroundColor: colors.paper,
    borderRadius: radii.md,
    minHeight: 128,
    padding: 15,
    width: "48.6%",
  },
  metricValue: {
    color: colors.forest,
    fontSize: 31,
    fontWeight: "900",
    marginTop: 15,
  },
  metricLabel: { color: colors.muted, fontSize: 10, marginTop: 3 },
  noticePanel: {
    backgroundColor: colors.orangeSoft,
    borderRadius: radii.md,
    gap: 9,
    padding: spacing.md,
  },
  sectionTitle: {
    color: colors.forest,
    fontSize: 17,
    fontWeight: "900",
    marginTop: 4,
  },
  noticeText: { color: colors.charcoal, fontSize: 11, lineHeight: 18 },
  productRow: {
    alignItems: "center",
    backgroundColor: colors.paper,
    borderRadius: radii.md,
    flexDirection: "row",
    padding: 13,
  },
  productRowInactive: { opacity: 0.72 },
  productCopy: { flex: 1, marginRight: 8 },
  productNameRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 8,
    justifyContent: "space-between",
  },
  productForm: {
    backgroundColor: colors.paper,
    borderRadius: radii.md,
    gap: 13,
    padding: spacing.md,
  },
  formHeading: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
  },
  field: { gap: 6 },
  fieldRow: { flexDirection: "row", gap: 10 },
  fieldHalf: { flex: 1, gap: 6 },
  fieldLabel: {
    color: colors.forest,
    fontSize: 10,
    fontWeight: "800",
  },
  input: {
    backgroundColor: colors.cream,
    borderColor: colors.line,
    borderRadius: radii.sm,
    borderWidth: 1,
    color: colors.charcoal,
    fontSize: 12,
    minHeight: 44,
    paddingHorizontal: 12,
    paddingVertical: 10,
  },
  descriptionInput: { minHeight: 76 },
  categoryRow: { flexDirection: "row", flexWrap: "wrap", gap: 7 },
  categoryButton: {
    backgroundColor: colors.cream,
    borderColor: colors.line,
    borderRadius: radii.pill,
    borderWidth: 1,
    paddingHorizontal: 11,
    paddingVertical: 8,
  },
  categoryButtonSelected: {
    backgroundColor: colors.forest,
    borderColor: colors.forest,
  },
  categoryLabel: {
    color: colors.muted,
    fontSize: 10,
    fontWeight: "800",
  },
  categoryLabelSelected: { color: colors.white },
  taxRow: {
    backgroundColor: colors.cream,
    borderRadius: radii.sm,
    flexDirection: "row",
    minHeight: 44,
    padding: 3,
  },
  taxButton: {
    alignItems: "center",
    borderRadius: radii.xs,
    flex: 1,
    justifyContent: "center",
  },
  taxButtonSelected: { backgroundColor: colors.forest },
  taxLabel: { color: colors.muted, fontSize: 10, fontWeight: "800" },
  taxLabelSelected: { color: colors.white },
  productListHeading: {
    alignItems: "flex-end",
    flexDirection: "row",
    justifyContent: "space-between",
  },
  productCount: { color: colors.muted, fontSize: 10 },
  rowTitle: { color: colors.forest, fontSize: 13, fontWeight: "900" },
  rowMeta: {
    color: colors.muted,
    fontSize: 9,
    lineHeight: 14,
    marginTop: 4,
  },
  adminCard: {
    backgroundColor: colors.paper,
    borderRadius: radii.md,
    gap: 11,
    padding: 13,
  },
  cardHeading: {
    alignItems: "flex-start",
    flexDirection: "row",
    gap: 8,
    justifyContent: "space-between",
  },
  actions: { flexDirection: "row", gap: 8 },
  orderBottom: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
  },
  settingsPanel: {
    backgroundColor: colors.paper,
    borderRadius: radii.md,
    gap: 7,
    padding: spacing.md,
  },
  settingLabel: {
    color: colors.muted,
    fontSize: 9,
    marginTop: 8,
  },
  settingValue: { color: colors.forest, fontSize: 12, fontWeight: "800" },
});
