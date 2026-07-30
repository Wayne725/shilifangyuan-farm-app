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
  fulfillmentStatusLabel,
  money,
  paymentLabels,
  proposalLabels,
} from "../src/lib/format";
import { api, getApiBaseUrl, getErrorMessage } from "../src/services/api";
import { useAuth } from "../src/store/AuthContext";
import { colors, radii, spacing } from "../src/theme";
import type {
  Category,
  Order,
  TaxType,
  TemperatureZone,
} from "../src/types";

type AdminTab =
  | "overview"
  | "sales"
  | "fulfillment"
  | "social"
  | "settings";

const tabs: { value: AdminTab; label: string }[] = [
  { value: "overview", label: "總覽" },
  { value: "sales", label: "販售" },
  { value: "fulfillment", label: "訂單與物流" },
  { value: "social", label: "社務" },
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
  isShippable: boolean;
  temperatureZone: TemperatureZone;
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
  isShippable: true,
  temperatureZone: "ambient",
};

export default function AdminScreen() {
  const [tab, setTab] = useState<AdminTab>("overview");
  const [message, setMessage] = useState("");
  const [productDraft, setProductDraft] =
    useState<ProductDraft>(emptyProductDraft);
  const [productFormError, setProductFormError] = useState("");
  const [resetConfirmation, setResetConfirmation] = useState("");
  const { user, isAdmin, logout } = useAuth();
  const queryClient = useQueryClient();

  const products = useQuery({ queryKey: ["products"], queryFn: api.products });
  const bundles = useQuery({ queryKey: ["bundles"], queryFn: api.bundles });
  const proposals = useQuery({
    queryKey: ["proposals"],
    queryFn: api.proposals,
  });
  const campaigns = useQuery({
    queryKey: ["campaigns"],
    queryFn: api.campaigns,
  });
  const orders = useQuery({ queryKey: ["orders"], queryFn: api.orders });
  const mealEvents = useQuery({
    queryKey: ["meal-events"],
    queryFn: api.mealEvents,
  });
  const membershipApplications = useQuery({
    queryKey: ["admin-membership-applications"],
    queryFn: api.adminMembershipApplications,
  });
  const activities = useQuery({
    queryKey: ["activities"],
    queryFn: api.activities,
  });
  const memberProposals = useQuery({
    queryKey: ["member-proposals"],
    queryFn: api.memberProposals,
  });

  const refresh = async () => {
    await queryClient.invalidateQueries();
  };
  const announce = async (text: string) => {
    setMessage(text);
    await refresh();
  };
  const toggleProduct = useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) =>
      api.toggleProduct(id, active),
    onSuccess: () => announce("商品狀態已更新"),
  });
  const createProduct = useMutation({
    mutationFn: api.createProduct,
    onSuccess: async () => {
      setProductDraft(emptyProductDraft);
      setProductFormError("");
      await announce("商品已上架");
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
        ...(action === "reject"
          ? { reason: "目前供應條件不適合開放" }
          : {}),
      }),
    onSuccess: () => announce("團購投票提案已更新"),
  });
  const convertProposal = useMutation({
    mutationFn: (proposalId: string) => {
      const proposal = (proposals.data ?? []).find(
        (item) => item.id === proposalId,
      );
      if (!proposal) throw new Error("找不到提案");
      const target =
        proposal.target_type === "product"
          ? (products.data ?? []).find((item) => item.id === proposal.target_id)
          : (bundles.data ?? []).find((item) => item.id === proposal.target_id);
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
    onSuccess: () => announce("已建立正式團購"),
  });
  const campaignDecision = useMutation({
    mutationFn: ({
      id,
      confirm,
      pickupAt,
    }: {
      id: string;
      confirm: boolean;
      pickupAt: string;
    }) =>
      confirm
        ? api.confirmCampaign(id, pickupAt)
        : api.rejectCampaign(id, "供應條件無法確認"),
    onSuccess: () => announce("團購成團狀態已更新"),
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
    mutationFn: (id: string) => api.adminRefundOrder(id, "管理員核准全額退款"),
    onSuccess: () => announce("訂單已進入退款處理"),
  });
  const advanceShipment = useMutation({
    mutationFn: (id: string) => api.adminAdvanceShipment(id, "delivered"),
    onSuccess: () => announce("Sandbox 物流貨態已推進"),
  });
  const mealEventAction = useMutation({
    mutationFn: ({
      id,
      action,
    }: {
      id: string;
      action: "publish" | "cancel" | "open_pickup" | "complete";
    }) => api.adminMealEventAction(id, action),
    onSuccess: () => announce("便當場次狀態已更新"),
  });
  const duplicateMealEvent = useMutation({
    mutationFn: api.adminDuplicateMealEvent,
    onSuccess: () => announce("便當場次已複製為草稿"),
  });
  const reviewMembership = useMutation({
    mutationFn: ({
      id,
      action,
    }: {
      id: string;
      action: "request_revision" | "approve" | "reject";
    }) =>
      api.reviewMembershipApplication(
        id,
        action,
        action === "request_revision"
          ? "請補齊測試證件後再次送件"
          : action === "reject"
            ? "目前資料未符合入社條件"
            : "資料與測試證件已確認",
      ),
    onSuccess: () => announce("入社申請狀態已更新"),
  });
  const reviewActivity = useMutation({
    mutationFn: ({
      id,
      action,
    }: {
      id: string;
      action: "approve" | "cancel" | "complete";
    }) => api.adminReviewActivity(id, action),
    onSuccess: () => announce("社員活動狀態已更新"),
  });
  const reviewMemberProposal = useMutation({
    mutationFn: ({
      id,
      action,
    }: {
      id: string;
      action: "approve" | "reject" | "close";
    }) => api.adminReviewMemberProposal(id, action),
    onSuccess: () => announce("社員提案狀態已更新"),
  });
  const reset = useMutation({
    mutationFn: api.resetDemo,
    onSuccess: async () => {
      setResetConfirmation("");
      await announce("展示資料已恢復為初始狀態");
    },
  });

  const loading = [
    products,
    bundles,
    proposals,
    campaigns,
    orders,
    mealEvents,
    membershipApplications,
    activities,
    memberProposals,
  ].some((query) => query.isLoading);
  const mutationError = [
    toggleProduct,
    createProduct,
    reviewProposal,
    convertProposal,
    campaignDecision,
    advanceOrder,
    refundOrder,
    advanceShipment,
    mealEventAction,
    duplicateMealEvent,
    reviewMembership,
    reviewActivity,
    reviewMemberProposal,
    reset,
  ].find((mutation) => mutation.error)?.error;
  const summary = useMemo(
    () => ({
      pendingOrders: (orders.data ?? []).filter(
        (order) =>
          (order.fulfillment?.status ?? order.fulfillment_status) ===
          "pending_confirmation",
      ).length,
      sellingItems:
        (products.data ?? []).filter((product) => product.is_active).length +
        (campaigns.data ?? []).filter(
          (campaign) => campaign.intake_status === "open",
        ).length,
      socialReviews: (membershipApplications.data ?? []).filter((item) =>
        ["submitted", "needs_revision"].includes(item.status),
      ).length,
      shipments: (orders.data ?? []).filter(
        (order) => order.shipment && order.shipment.status !== "delivered",
      ).length,
    }),
    [
      campaigns.data,
      membershipApplications.data,
      orders.data,
      products.data,
    ],
  );

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
      is_shippable: productDraft.isShippable,
      temperature_zone: productDraft.temperatureZone,
      allowed_logistics: productDraft.isShippable
        ? ["home_delivery", "seven_eleven", "family_mart", "hilife"]
        : [],
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
        <Button
          compact
          icon="storefront-outline"
          label="顧客畫面"
          onPress={() => router.push("/(tabs)/home")}
          variant="quiet"
        />
      </View>
      <View style={styles.titleRow}>
        <View>
          <Text style={styles.title}>合作社管理</Text>
          <Text style={styles.operator}>{user.display_name}</Text>
        </View>
        <Ionicons color={colors.orange} name="settings-outline" size={27} />
      </View>
      <SegmentControl onChange={setTab} options={tabs} value={tab} />
      {message ? (
        <View style={styles.message}>
          <InlineMessage text={message} tone="positive" />
        </View>
      ) : null}
      {mutationError ? (
        <View style={styles.message}>
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
                label: "販售項目",
                value: summary.sellingItems,
                icon: "storefront-outline" as const,
                target: "sales" as const,
              },
              {
                label: "待確認訂單",
                value: summary.pendingOrders,
                icon: "receipt-outline" as const,
                target: "fulfillment" as const,
              },
              {
                label: "進行中物流",
                value: summary.shipments,
                icon: "cube-outline" as const,
                target: "fulfillment" as const,
              },
              {
                label: "待處理社務",
                value: summary.socialReviews,
                icon: "people-outline" as const,
                target: "social" as const,
              },
            ].map((metric) => (
              <Pressable
                key={metric.label}
                onPress={() => setTab(metric.target)}
                style={({ pressed }) => [
                  styles.metric,
                  pressed && styles.pressed,
                ]}
              >
                <Ionicons color={colors.forest} name={metric.icon} size={22} />
                <Text style={styles.metricValue}>{metric.value}</Text>
                <Text style={styles.metricLabel}>{metric.label}</Text>
              </Pressable>
            ))}
          </View>
          <View style={styles.notice}>
            <Text style={styles.sectionTitle}>今日處理順序</Text>
            <Text style={styles.noticeText}>
              先處理待審核入社申請與已達門檻團購，再確認訂單履約及物流貨態。
            </Text>
          </View>
        </View>
      ) : null}

      {!loading && tab === "sales" ? (
        <View style={styles.content}>
          <Text style={styles.sectionTitle}>上架新商品</Text>
          <View style={styles.form}>
            <Field
              label="商品名稱"
              onChange={(name) =>
                setProductDraft((current) => ({ ...current, name }))
              }
              placeholder="例如：友善栽培高麗菜"
              value={productDraft.name}
            />
            <Field
              label="商品介紹"
              multiline
              onChange={(description) =>
                setProductDraft((current) => ({ ...current, description }))
              }
              placeholder="產地、栽培方式或料理方式"
              value={productDraft.description}
            />
            <Text style={styles.fieldLabel}>分類</Text>
            <View style={styles.optionWrap}>
              {productCategories.map((category) => (
                <Choice
                  key={category}
                  label={category}
                  onPress={() =>
                    setProductDraft((current) => ({ ...current, category }))
                  }
                  selected={productDraft.category === category}
                />
              ))}
            </View>
            <Field
              label="販售單位"
              onChange={(unit) =>
                setProductDraft((current) => ({ ...current, unit }))
              }
              placeholder="盒、包、袋、台斤"
              value={productDraft.unit}
            />
            <View style={styles.fieldRow}>
              <View style={styles.fieldHalf}>
                <Field
                  keyboardType="number-pad"
                  label="社員價"
                  onChange={(memberPrice) =>
                    setProductDraft((current) => ({
                      ...current,
                      memberPrice,
                    }))
                  }
                  placeholder="0"
                  value={productDraft.memberPrice}
                />
              </View>
              <View style={styles.fieldHalf}>
                <Field
                  keyboardType="number-pad"
                  label="非社員價"
                  onChange={(nonmemberPrice) =>
                    setProductDraft((current) => ({
                      ...current,
                      nonmemberPrice,
                    }))
                  }
                  placeholder="0"
                  value={productDraft.nonmemberPrice}
                />
              </View>
            </View>
            <View style={styles.fieldRow}>
              <View style={styles.fieldHalf}>
                <Field
                  keyboardType="number-pad"
                  label="庫存"
                  onChange={(stock) =>
                    setProductDraft((current) => ({ ...current, stock }))
                  }
                  placeholder="0"
                  value={productDraft.stock}
                />
              </View>
              <View style={styles.fieldHalf}>
                <Text style={styles.fieldLabel}>稅別</Text>
                <View style={styles.taxRow}>
                  <Choice
                    label="應稅"
                    onPress={() =>
                      setProductDraft((current) => ({
                        ...current,
                        taxType: "taxable",
                      }))
                    }
                    selected={productDraft.taxType === "taxable"}
                  />
                  <Choice
                    label="免稅"
                    onPress={() =>
                      setProductDraft((current) => ({
                        ...current,
                        taxType: "tax_exempt",
                      }))
                    }
                    selected={productDraft.taxType === "tax_exempt"}
                  />
                </View>
              </View>
            </View>
            <View style={styles.shippingSetting}>
              <View style={styles.cardCopy}>
                <Text style={styles.fieldLabel}>允許物流配送</Text>
                <Text style={styles.rowMeta}>
                  關閉後只能選合作社現場取貨
                </Text>
              </View>
              <Switch
                onValueChange={(isShippable) =>
                  setProductDraft((current) => ({
                    ...current,
                    isShippable,
                  }))
                }
                trackColor={{ false: colors.sage, true: colors.forestSoft }}
                value={productDraft.isShippable}
              />
            </View>
            {productDraft.isShippable ? (
              <>
                <Text style={styles.fieldLabel}>配送溫層</Text>
                <View style={styles.optionWrap}>
                  {[
                    { value: "ambient" as const, label: "常溫" },
                    { value: "chilled" as const, label: "冷藏" },
                    { value: "frozen" as const, label: "冷凍" },
                  ].map((option) => (
                    <Choice
                      key={option.value}
                      label={option.label}
                      onPress={() =>
                        setProductDraft((current) => ({
                          ...current,
                          temperatureZone: option.value,
                        }))
                      }
                      selected={productDraft.temperatureZone === option.value}
                    />
                  ))}
                </View>
              </>
            ) : null}
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

          <Text style={styles.sectionTitle}>商品清單</Text>
          {(products.data ?? []).map((product) => (
            <View key={product.id} style={styles.adminCard}>
              <View style={styles.cardHeading}>
                <View style={styles.cardCopy}>
                  <Text style={styles.rowTitle}>{product.name}</Text>
                  <Text style={styles.rowMeta}>
                    {product.category}　社員 {money(product.member_price)}
                    　非社員 {money(product.nonmember_price)}
                  </Text>
                </View>
                <Switch
                  disabled={toggleProduct.isPending}
                  onValueChange={(active) =>
                    toggleProduct.mutate({ id: product.id, active })
                  }
                  trackColor={{ false: colors.sage, true: colors.forestSoft }}
                  value={product.is_active}
                />
              </View>
            </View>
          ))}

          <Text style={styles.sectionTitle}>團購提案與正式團購</Text>
          {(proposals.data ?? [])
            .filter((proposal) => proposal.status === "pending_review")
            .map((proposal) => (
              <View key={proposal.id} style={styles.adminCard}>
                <CardTitle
                  status={proposalLabels[proposal.status]}
                  title={proposal.target_name}
                />
                <View style={styles.actions}>
                  <Button
                    compact
                    label="通過投票"
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
                    variant="danger"
                  />
                </View>
              </View>
            ))}
          {(proposals.data ?? [])
            .filter(
              (proposal) =>
                proposal.status === "conversion_pending" ||
                (proposal.status === "voting" &&
                  proposal.vote_count >= proposal.threshold),
            )
            .map((proposal) => (
              <View key={`convert-${proposal.id}`} style={styles.adminCard}>
                <CardTitle status="可正式開團" title={proposal.target_name} />
                <Button
                  compact
                  label="建立正式團購"
                  onPress={() => convertProposal.mutate(proposal.id)}
                />
              </View>
            ))}
          {(campaigns.data ?? []).map((campaign) => (
            <View key={campaign.id} style={styles.adminCard}>
              <CardTitle
                status={campaignLabels[campaign.decision_status]}
                title={campaign.title}
              />
              <Text style={styles.rowMeta}>
                已付款 {campaign.paid_quantity}/{campaign.min_paid_quantity} 組
              </Text>
              {campaign.decision_status === "pending_confirmation" ? (
                <View style={styles.actions}>
                  <Button
                    compact
                    label="確認成團"
                    onPress={() =>
                      campaignDecision.mutate({
                        id: campaign.id,
                        confirm: true,
                        pickupAt: campaign.estimated_pickup_start,
                      })
                    }
                  />
                  <Button
                    compact
                    label="拒絕成團"
                    onPress={() =>
                      campaignDecision.mutate({
                        id: campaign.id,
                        confirm: false,
                        pickupAt: campaign.estimated_pickup_start,
                      })
                    }
                    variant="danger"
                  />
                </View>
              ) : null}
            </View>
          ))}

          <Text style={styles.sectionTitle}>便當場次</Text>
          {(mealEvents.data ?? []).map((event) => (
            <View key={event.id} style={styles.adminCard}>
              <CardTitle status={event.status} title={event.title} />
              <Text style={styles.rowMeta}>{event.venue_name}</Text>
              <View style={styles.actions}>
                <Button
                  compact
                  label="複製場次"
                  onPress={() => duplicateMealEvent.mutate(event.id)}
                  variant="quiet"
                />
                {event.status === "published" ? (
                  <Button
                    compact
                    label="開放取餐"
                    onPress={() =>
                      mealEventAction.mutate({
                        id: event.id,
                        action: "open_pickup",
                      })
                    }
                    variant="secondary"
                  />
                ) : null}
                {!["completed", "cancelled"].includes(event.status) ? (
                  <Button
                    compact
                    label="取消場次"
                    onPress={() =>
                      mealEventAction.mutate({
                        id: event.id,
                        action: "cancel",
                      })
                    }
                    variant="danger"
                  />
                ) : null}
              </View>
            </View>
          ))}
        </View>
      ) : null}

      {!loading && tab === "fulfillment" ? (
        <View style={styles.content}>
          <InlineMessage text="後台貨態推進是 Sandbox 功能，正式環境只接受已驗證的物流回呼。" />
          {(orders.data ?? []).map((order) => {
            const nextStatus = order.available_actions.includes("start_preparing")
              ? "preparing"
              : order.available_actions.includes("mark_ready")
                ? "ready_for_pickup"
                : order.available_actions.includes("mark_picked_up")
                  ? "picked_up"
                  : null;
            return (
              <View key={order.id} style={styles.adminCard}>
                <View style={styles.cardHeading}>
                  <View style={styles.cardCopy}>
                    <Text style={styles.rowTitle}>{order.order_number}</Text>
                    <Text style={styles.rowMeta}>
                      {order.order_kind === "group"
                        ? "團購"
                        : order.order_kind === "meal_preorder"
                          ? "便當"
                          : "一般訂單"}
                      　{money(order.amount_total)}
                    </Text>
                  </View>
                  <StatusPill label={paymentLabels[order.payment_status]} />
                </View>
                <View style={styles.statusLine}>
                  <StatusPill
                    label={fulfillmentStatusLabel(
                      order.fulfillment?.status ?? order.fulfillment_status,
                    )}
                  />
                  {order.shipment ? (
                    <StatusPill
                      label={`物流 ${order.shipment.status}`}
                      tone={
                        order.shipment.status === "delivered"
                          ? "positive"
                          : "warning"
                      }
                    />
                  ) : null}
                </View>
                {order.shipment ? (
                  <Text style={styles.rowMeta}>
                    {order.shipment.tracking_number}　運費{" "}
                    {money(order.shipment.shipping_fee)}
                  </Text>
                ) : null}
                <View style={styles.actions}>
                  {order.available_actions.includes("refund") ? (
                    <Button
                      compact
                      label="全額退款"
                      onPress={() => refundOrder.mutate(order.id)}
                      variant="danger"
                    />
                  ) : null}
                  {nextStatus ? (
                    <Button
                      compact
                      label="推進履約"
                      onPress={() =>
                        advanceOrder.mutate({ id: order.id, status: nextStatus })
                      }
                      variant="secondary"
                    />
                  ) : null}
                  {order.shipment &&
                  order.shipment.status !== "delivered" ? (
                    <Button
                      compact
                      label="Sandbox 標記送達"
                      onPress={() => advanceShipment.mutate(order.id)}
                      variant="quiet"
                    />
                  ) : null}
                </View>
              </View>
            );
          })}
        </View>
      ) : null}

      {!loading && tab === "social" ? (
        <View style={styles.content}>
          <Text style={styles.sectionTitle}>入社申請</Text>
          {(membershipApplications.data ?? []).map((application) => (
            <View key={application.id} style={styles.adminCard}>
              <CardTitle
                status={application.status}
                title={application.legal_name}
              />
              <Text style={styles.rowMeta}>
                測試證件 {application.confirmed_documents.length}/
                {application.required_documents.length} 份
              </Text>
              {application.review_note ? (
                <Text style={styles.rowMeta}>{application.review_note}</Text>
              ) : null}
              {!["approved", "rejected", "withdrawn"].includes(
                application.status,
              ) ? (
                <View style={styles.actions}>
                  <Button
                    compact
                    label="核准"
                    onPress={() =>
                      reviewMembership.mutate({
                        id: application.id,
                        action: "approve",
                      })
                    }
                  />
                  <Button
                    compact
                    label="要求補件"
                    onPress={() =>
                      reviewMembership.mutate({
                        id: application.id,
                        action: "request_revision",
                      })
                    }
                    variant="secondary"
                  />
                  <Button
                    compact
                    label="駁回"
                    onPress={() =>
                      reviewMembership.mutate({
                        id: application.id,
                        action: "reject",
                      })
                    }
                    variant="danger"
                  />
                </View>
              ) : null}
            </View>
          ))}

          <Text style={styles.sectionTitle}>社員活動</Text>
          {(activities.data ?? []).map((activity) => (
            <View key={activity.id} style={styles.adminCard}>
              <CardTitle status={activity.status} title={activity.title} />
              <Text style={styles.rowMeta}>
                {activity.registered_count}/{activity.capacity} 人，候補{" "}
                {activity.waitlist_count} 人
              </Text>
              <View style={styles.actions}>
                {activity.status === "pending_review" ? (
                  <Button
                    compact
                    label="審核發布"
                    onPress={() =>
                      reviewActivity.mutate({
                        id: activity.id,
                        action: "approve",
                      })
                    }
                  />
                ) : null}
                {activity.status === "published" ? (
                  <Button
                    compact
                    label="結束活動"
                    onPress={() =>
                      reviewActivity.mutate({
                        id: activity.id,
                        action: "complete",
                      })
                    }
                    variant="secondary"
                  />
                ) : null}
              </View>
            </View>
          ))}

          <Text style={styles.sectionTitle}>社員治理提案</Text>
          {(memberProposals.data ?? []).map((proposal) => (
            <View key={proposal.id} style={styles.adminCard}>
              <CardTitle status={proposal.status} title={proposal.title} />
              <Text style={styles.rowMeta}>
                贊成 {proposal.yes_count}　反對 {proposal.no_count}　棄權{" "}
                {proposal.abstain_count}
              </Text>
              <View style={styles.actions}>
                {proposal.status === "pending_review" ? (
                  <>
                    <Button
                      compact
                      label="進入討論"
                      onPress={() =>
                        reviewMemberProposal.mutate({
                          id: proposal.id,
                          action: "approve",
                        })
                      }
                    />
                    <Button
                      compact
                      label="不通過"
                      onPress={() =>
                        reviewMemberProposal.mutate({
                          id: proposal.id,
                          action: "reject",
                        })
                      }
                      variant="danger"
                    />
                  </>
                ) : null}
                {["passed", "rejected"].includes(proposal.status) ? (
                  <Button
                    compact
                    label="記錄結案"
                    onPress={() =>
                      reviewMemberProposal.mutate({
                        id: proposal.id,
                        action: "close",
                      })
                    }
                    variant="secondary"
                  />
                ) : null}
              </View>
            </View>
          ))}
        </View>
      ) : null}

      {!loading && tab === "settings" ? (
        <View style={styles.content}>
          <View style={styles.notice}>
            <Text style={styles.sectionTitle}>服務設定</Text>
            <Text style={styles.settingLabel}>API 位址</Text>
            <Text style={styles.settingValue}>
              {getApiBaseUrl() || "使用內建資料來源"}
            </Text>
            <Text style={styles.settingLabel}>資料重設確認碼</Text>
            <TextInput
              autoCapitalize="none"
              onChangeText={setResetConfirmation}
              placeholder={
                getApiBaseUrl() ? "由部署管理員保管" : "內建確認碼為 RESET"
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

function Field({
  label,
  value,
  placeholder,
  onChange,
  multiline,
  keyboardType,
}: {
  label: string;
  value: string;
  placeholder: string;
  onChange: (value: string) => void;
  multiline?: boolean;
  keyboardType?: "default" | "number-pad";
}) {
  return (
    <View style={styles.field}>
      <Text style={styles.fieldLabel}>{label}</Text>
      <TextInput
        keyboardType={keyboardType}
        multiline={multiline}
        onChangeText={onChange}
        placeholder={placeholder}
        placeholderTextColor={colors.sage}
        style={[styles.input, multiline && styles.textarea]}
        textAlignVertical={multiline ? "top" : "center"}
        value={value}
      />
    </View>
  );
}

function Choice({
  label,
  selected,
  onPress,
}: {
  label: string;
  selected: boolean;
  onPress: () => void;
}) {
  return (
    <Pressable
      onPress={onPress}
      style={[styles.choice, selected && styles.choiceSelected]}
    >
      <Text style={[styles.choiceLabel, selected && styles.choiceLabelSelected]}>
        {label}
      </Text>
    </Pressable>
  );
}

function CardTitle({ title, status }: { title: string; status: string }) {
  return (
    <View style={styles.cardHeading}>
      <Text style={styles.rowTitle}>{title}</Text>
      <StatusPill label={status} />
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
  titleRow: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    padding: spacing.md,
  },
  title: { color: colors.forest, fontSize: 27, fontWeight: "900" },
  operator: { color: colors.muted, fontSize: 12, marginTop: 4 },
  message: { paddingHorizontal: spacing.md, paddingTop: 10 },
  content: { gap: 12, padding: spacing.md },
  metrics: { flexDirection: "row", flexWrap: "wrap", gap: 12 },
  metric: {
    backgroundColor: colors.paper,
    borderRadius: radii.lg,
    minHeight: 130,
    padding: 14,
    width: "48.3%",
  },
  metricValue: {
    color: colors.orange,
    fontSize: 30,
    fontWeight: "900",
    marginTop: 14,
  },
  metricLabel: { color: colors.forest, fontSize: 12, fontWeight: "800" },
  notice: {
    backgroundColor: colors.paper,
    borderRadius: radii.lg,
    gap: 10,
    padding: spacing.md,
  },
  noticeText: { color: colors.muted, fontSize: 13, lineHeight: 20 },
  sectionTitle: { color: colors.forest, fontSize: 19, fontWeight: "900" },
  form: {
    backgroundColor: colors.paper,
    borderRadius: radii.lg,
    gap: 11,
    padding: spacing.md,
  },
  field: { gap: 6 },
  fieldLabel: { color: colors.forest, fontSize: 12, fontWeight: "800" },
  input: {
    backgroundColor: colors.cream,
    borderColor: colors.line,
    borderRadius: radii.md,
    borderWidth: 1,
    color: colors.charcoal,
    fontSize: 14,
    minHeight: 50,
    paddingHorizontal: 13,
  },
  textarea: { minHeight: 92, paddingTop: 12 },
  fieldRow: { flexDirection: "row", gap: 10 },
  fieldHalf: { flex: 1 },
  optionWrap: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  taxRow: { flexDirection: "row", gap: 7 },
  shippingSetting: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    minHeight: 54,
  },
  choice: {
    alignItems: "center",
    borderColor: colors.line,
    borderRadius: radii.md,
    borderWidth: 1,
    justifyContent: "center",
    minHeight: 44,
    paddingHorizontal: 12,
  },
  choiceSelected: { backgroundColor: colors.forest, borderColor: colors.forest },
  choiceLabel: { color: colors.muted, fontSize: 12, fontWeight: "800" },
  choiceLabelSelected: { color: colors.white },
  adminCard: {
    backgroundColor: colors.paper,
    borderRadius: radii.lg,
    gap: 10,
    padding: spacing.md,
  },
  cardHeading: {
    alignItems: "flex-start",
    flexDirection: "row",
    gap: 10,
    justifyContent: "space-between",
  },
  cardCopy: { flex: 1 },
  rowTitle: { color: colors.forest, flex: 1, fontSize: 15, fontWeight: "900" },
  rowMeta: { color: colors.muted, fontSize: 12, lineHeight: 18 },
  actions: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  statusLine: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  settingLabel: {
    color: colors.forest,
    fontSize: 12,
    fontWeight: "800",
    marginTop: 5,
  },
  settingValue: { color: colors.muted, fontSize: 13 },
  pressed: { opacity: 0.74, transform: [{ scale: 0.99 }] },
});
