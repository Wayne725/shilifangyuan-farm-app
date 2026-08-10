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
import { confirmAction } from "../src/lib/confirm";
import { openExternalPage, openPaymentPage } from "../src/lib/payment";
import {
  campaignLabels,
  fulfillmentStatusLabel,
  money,
  nextShipmentStatus,
  paymentLabels,
  proposalLabels,
  shipmentStatusLabel,
} from "../src/lib/format";
import { api, getApiBaseUrl, getErrorMessage } from "../src/services/api";
import { useAuth } from "../src/store/AuthContext";
import { colors, radii, spacing } from "../src/theme";
import type {
  ActivityRegistrationStatus,
  Category,
  Membership,
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

type MealDraft = {
  name: string;
  description: string;
  price: string;
  taxType: TaxType;
};

const emptyMealDraft: MealDraft = {
  name: "",
  description: "",
  price: "",
  taxType: "taxable",
};

type MealEventDraft = {
  title: string;
  location: string;
  mealId: string;
  capacity: string;
};

const emptyMealEventDraft: MealEventDraft = {
  title: "",
  location: "",
  mealId: "",
  capacity: "20",
};

const activityRegistrationLabels: Record<
  ActivityRegistrationStatus,
  string
> = {
  registered: "已報名",
  waitlisted: "候補中",
  cancelled: "已取消",
  attended: "已簽到",
  no_show: "未出席",
};

const membershipDocumentLabels = {
  id_front: "身分證正面測試檔",
  id_back: "身分證反面測試檔",
  secondary: "第二證件測試檔",
} as const;

type AdminMembershipAction =
  | "activate"
  | "suspend"
  | "resign"
  | "terminate"
  | "share-capital-return";

const membershipStatusLabels: Record<Membership["status"], string> = {
  pending_payment: "待繳入社款",
  trainee: "實習社員",
  active: "有效",
  suspended: "停權",
  resigned: "已退社",
  terminated: "已終止",
};

const membershipActionLabels: Record<AdminMembershipAction, string> = {
  activate: "轉為正式社員",
  suspend: "停權",
  resign: "辦理退社",
  terminate: "終止會籍",
  "share-capital-return": "建立股金返還",
};

function membershipActionsFor(
  membership: Membership,
): AdminMembershipAction[] {
  const { status } = membership;
  if (
    status === "trainee" &&
    membership.trainee_number &&
    !membership.member_number
  ) {
    return ["activate", "terminate"];
  }
  if (status === "active") return ["suspend", "resign", "terminate"];
  if (status === "suspended") return ["resign", "terminate"];
  if (status === "pending_payment") return ["terminate"];
  if (status === "resigned") return ["share-capital-return"];
  return [];
}

export default function AdminScreen() {
  const [tab, setTab] = useState<AdminTab>("overview");
  const [message, setMessage] = useState("");
  const [productDraft, setProductDraft] =
    useState<ProductDraft>(emptyProductDraft);
  const [productFormError, setProductFormError] = useState("");
  const [mealDraft, setMealDraft] = useState<MealDraft>(emptyMealDraft);
  const [mealFormError, setMealFormError] = useState("");
  const [mealEventDraft, setMealEventDraft] =
    useState<MealEventDraft>(emptyMealEventDraft);
  const [mealEventFormError, setMealEventFormError] = useState("");
  const [expandedActivityId, setExpandedActivityId] = useState<string | null>(
    null,
  );
  const [expandedMembershipApplicationId, setExpandedMembershipApplicationId] =
    useState<string | null>(null);
  const [resetConfirmation, setResetConfirmation] = useState("");
  const [returnedShareCapitalIds, setReturnedShareCapitalIds] = useState<
    string[]
  >([]);
  const [mealPickupCodes, setMealPickupCodes] = useState<
    Record<string, string>
  >({});
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
    queryKey: ["admin-meal-events"],
    queryFn: api.adminMealEvents,
  });
  const meals = useQuery({
    queryKey: ["admin-meals"],
    queryFn: api.adminMeals,
  });
  const membershipApplications = useQuery({
    queryKey: ["admin-membership-applications"],
    queryFn: api.adminMembershipApplications,
  });
  const members = useQuery({
    queryKey: ["admin-members"],
    queryFn: api.adminMembers,
  });
  const membershipApplicationDetail = useQuery({
    queryKey: [
      "admin-membership-application",
      expandedMembershipApplicationId,
    ],
    queryFn: () =>
      api.adminMembershipApplication(expandedMembershipApplicationId ?? ""),
    enabled: Boolean(expandedMembershipApplicationId),
  });
  const activities = useQuery({
    queryKey: ["admin-activities"],
    queryFn: api.adminActivities,
  });
  const memberProposals = useQuery({
    queryKey: ["admin-member-proposals"],
    queryFn: api.adminMemberProposals,
  });
  const activityRegistrations = useQuery({
    queryKey: ["admin-activity-registrations", expandedActivityId],
    queryFn: () => api.adminActivityRegistrations(expandedActivityId ?? ""),
    enabled: Boolean(expandedActivityId),
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
  const createDirectCampaign = useMutation({
    mutationFn: async (productId: string) => {
      const product = (products.data ?? []).find((item) => item.id === productId);
      if (!product) throw new Error("找不到商品");
      const confirmed = await confirmAction({
        title: "直接建立正式團購",
        message: `將以「${product.name}」建立 7 天團購，門檻 10 組、上限 30 組。確定開團嗎？`,
        confirmLabel: "確認開團",
        destructive: false,
      });
      if (!confirmed) return null;
      const now = Date.now();
      const canShip = product.is_shippable === true;
      const temperature = product.temperature_zone ?? "ambient";
      return api.createCampaign({
        target_type: "product",
        target_id: product.id,
        title: `${product.name}共同購買`,
        description: "由管理員直接開團，付款達門檻後由合作社確認成團。",
        image_url: product.image_url,
        member_price: product.member_price,
        nonmember_price: product.nonmember_price,
        min_paid_quantity: 10,
        supply_cap: 30,
        per_user_cap: 5,
        deadline: new Date(now + 7 * 86400000).toISOString(),
        estimated_pickup_start: new Date(now + 10 * 86400000).toISOString(),
        estimated_pickup_end: new Date(now + 11 * 86400000).toISOString(),
        can_ship: canShip,
        shipping_temperature: canShip ? temperature : null,
        allowed_shipping_channels: canShip
          ? temperature === "ambient"
            ? (product.allowed_logistics ?? ["home_delivery"])
            : ["home_delivery"]
          : [],
      });
    },
    onSuccess: (result) => {
      if (result) announce("已直接建立正式團購");
    },
  });
  const reviewProposal = useMutation({
    mutationFn: async ({
      id,
      action,
    }: {
      id: string;
      action: "approve" | "reject";
    }) => {
      const confirmed = await confirmAction({
        title: action === "approve" ? "通過團購投票提案" : "不通過團購投票提案",
        message:
          action === "approve"
            ? "通過後將開放社員與非社員投票。確定要繼續嗎？"
            : "提案將結束，提案人會看到管理端理由。確定不通過嗎？",
        confirmLabel: action === "approve" ? "確認通過" : "確認不通過",
        destructive: action === "reject",
      });
      if (!confirmed) return null;
      return api.reviewProposal(id, action, {
        threshold: 10,
        deadline: new Date(Date.now() + 7 * 86400000).toISOString(),
        ...(action === "reject"
          ? { reason: "目前供應條件不適合開放" }
          : {}),
      });
    },
    onSuccess: (result) => {
      if (result) announce("團購投票提案已更新");
    },
  });
  const convertProposal = useMutation({
    mutationFn: async (proposalId: string) => {
      const proposal = (proposals.data ?? []).find(
        (item) => item.id === proposalId,
      );
      if (!proposal) throw new Error("找不到提案");
      const target =
        proposal.target_type === "product"
          ? (products.data ?? []).find((item) => item.id === proposal.target_id)
          : (bundles.data ?? []).find((item) => item.id === proposal.target_id);
      if (!target) throw new Error("找不到提案項目");
      const confirmed = await confirmAction({
        title: "建立正式團購",
        message: "建立後將採用目前畫面中的價格、門檻與期限。確定要正式開團嗎？",
        confirmLabel: "確認開團",
        destructive: false,
      });
      if (!confirmed) return null;
      const now = Date.now();
      const targetCanShip =
        proposal.target_type === "product" &&
        "is_shippable" in target &&
        target.is_shippable === true;
      const targetTemperature =
        targetCanShip && "temperature_zone" in target
          ? (target.temperature_zone ?? "ambient")
          : undefined;
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
        can_ship: targetCanShip,
        shipping_temperature: targetTemperature,
        allowed_shipping_channels:
          targetCanShip && "allowed_logistics" in target
            ? (target.allowed_logistics ?? ["home_delivery"])
            : [],
      });
    },
    onSuccess: (result) => {
      if (result) announce("已建立正式團購");
    },
  });
  const campaignDecision = useMutation({
    mutationFn: async ({
      id,
      confirm,
      pickupAt,
    }: {
      id: string;
      confirm: boolean;
      pickupAt: string;
    }) => {
      const confirmed = await confirmAction({
        title: confirm ? "確認團購成團" : "拒絕團購成團",
        message: confirm
          ? "確認後將進入履約，並依設定重新開放或關閉加入。確定成團嗎？"
          : "拒絕後將關閉團購並處理已付款訂單退款。確定拒絕嗎？",
        confirmLabel: confirm ? "確認成團" : "確認拒絕",
        destructive: !confirm,
      });
      if (!confirmed) return null;
      return confirm
        ? api.confirmCampaign(id, pickupAt)
        : api.rejectCampaign(id, "供應條件無法確認");
    },
    onSuccess: (result) => {
      if (result) announce("團購成團狀態已更新");
    },
  });
  const advanceOrder = useMutation({
    mutationFn: async ({
      id,
      status,
    }: {
      id: string;
      status: Order["fulfillment_status"];
    }) => {
      const confirmed = await confirmAction({
        title: "推進訂單履約",
        message: `訂單履約狀態將更新為「${fulfillmentStatusLabel(status)}」。確定要繼續嗎？`,
        confirmLabel: "確認推進",
        destructive: false,
      });
      if (!confirmed) return null;
      return api.advanceOrder(id, status);
    },
    onSuccess: (result) => {
      if (result) announce("訂單履約狀態已更新");
    },
  });
  const refundOrder = useMutation({
    mutationFn: async (id: string) => {
      const confirmed = await confirmAction({
        title: "全額退款",
        message:
          "將取消這筆訂單、釋放庫存並建立退款紀錄，且無法復原。確定要繼續嗎？",
        confirmLabel: "確認退款",
        cancelLabel: "先不要",
      });
      if (!confirmed) return null;
      return api.adminRefundOrder(id, "管理員核准全額退款");
    },
    onSuccess: (result) => {
      if (result) announce("訂單已進入退款處理");
    },
  });
  const advanceShipment = useMutation({
    mutationFn: async ({
      id,
      status,
    }: {
      id: string;
      status: "in_transit" | "delivered";
    }) => {
      const confirmed = await confirmAction({
        title: "推進 Sandbox 物流貨態",
        message: `物流狀態將更新為「${shipmentStatusLabel(status)}」，並留下管理稽核紀錄。確定要繼續嗎？`,
        confirmLabel: "確認推進",
        destructive: false,
      });
      if (!confirmed) return null;
      return api.adminAdvanceShipment(id, status);
    },
    onSuccess: (result) => {
      if (result) announce("Sandbox 物流貨態已推進");
    },
  });
  const createShipment = useMutation({
    mutationFn: async (orderId: string) => {
      const confirmed = await confirmAction({
        title: "建立綠界物流單",
        message: "系統將使用訂單已選定的通路與收件資料建立正式物流單。確定要繼續嗎？",
        confirmLabel: "確認建單",
        destructive: false,
      });
      if (!confirmed) return null;
      return api.adminCreateShipment(orderId);
    },
    onSuccess: (result) => {
      if (result) announce("綠界正式物流單已建立");
    },
  });
  const createMeal = useMutation({
    mutationFn: api.adminCreateMeal,
    onSuccess: async (createdMeal) => {
      setMealDraft(emptyMealDraft);
      setMealFormError("");
      setMealEventDraft((current) => ({
        ...current,
        mealId: createdMeal.id,
      }));
      await announce("便當餐點已建立，可接著建立預購場次");
    },
  });
  const createMealEvent = useMutation({
    mutationFn: api.adminCreateMealEvent,
    onSuccess: async () => {
      setMealEventDraft(emptyMealEventDraft);
      setMealEventFormError("");
      await announce("便當場次已建立為草稿");
    },
  });
  const mealEventAction = useMutation({
    mutationFn: async ({
      id,
      action,
    }: {
      id: string;
      action: "publish" | "cancel" | "open_pickup" | "complete";
    }) => {
      const actionCopy = {
        publish: ["發布便當場次", "發布後顧客可在開賣時間內預購。確定發布嗎？", "確認發布"],
        open_pickup: ["開放便當取餐", "開放後工作人員即可使用六位取餐碼核銷。確定開放嗎？", "確認開放"],
        cancel: ["取消便當場次", "取消後將停止取餐並處理已付款訂單退款。確定取消嗎？", "確認取消"],
        complete: ["結束便當場次", "結束後，尚未核銷的訂單將依規則標記為未取。確定結束嗎？", "確認結束"],
      } as const;
      const [title, message, confirmLabel] = actionCopy[action];
      const confirmed = await confirmAction({
        title,
        message,
        confirmLabel,
        destructive: action === "cancel",
      });
      if (!confirmed) return null;
      return api.adminMealEventAction(
        id,
        action,
        action === "cancel"
          ? "管理員取消便當場次"
          : action === "complete"
            ? "取餐時段已結束"
            : undefined,
      );
    },
    onSuccess: (result) => {
      if (result) announce("便當場次狀態已更新");
    },
  });
  const duplicateMealEvent = useMutation({
    mutationFn: api.adminDuplicateMealEvent,
    onSuccess: () => announce("便當場次已複製為草稿"),
  });
  const reviewMembership = useMutation({
    mutationFn: async ({
      id,
      action,
    }: {
      id: string;
      action: "request_revision" | "approve" | "reject";
    }) => {
      const labels = {
        approve: { title: "核准入社申請", confirm: "確認核准" },
        request_revision: { title: "要求申請人補件", confirm: "確認補件" },
        reject: { title: "駁回入社申請", confirm: "確認駁回" },
      } as const;
      const confirmed = await confirmAction({
        title: labels[action].title,
        message:
          action === "approve"
            ? "核准後會產生入社費與股金兩筆應繳款。確定資料已審查完成嗎？"
            : action === "request_revision"
              ? "申請狀態將改為待補件，並通知申請人。確定要繼續嗎？"
              : "駁回後申請流程將結束。確定要駁回嗎？",
        confirmLabel: labels[action].confirm,
        destructive: action === "reject",
      });
      if (!confirmed) return null;
      return api.reviewMembershipApplication(
        id,
        action,
        action === "request_revision"
          ? "請補齊測試證件後再次送件"
          : action === "reject"
            ? "目前資料未符合入社條件"
            : "資料與測試證件已確認",
      );
    },
    onSuccess: (result) => {
      if (result) announce("入社申請狀態已更新");
    },
  });
  const viewMembershipDocument = useMutation({
    mutationFn: ({
      applicationId,
      documentId,
    }: {
      applicationId: string;
      documentId: string;
    }) => api.adminMembershipDocumentDownloadUrl(applicationId, documentId),
    onSuccess: async (result) => {
      if (!result.download_url) {
        setMessage("內建展示模式已驗證合成測試證件；連接 R2 後可開啟短效檢視網址。");
        return;
      }
      await openExternalPage(result.download_url);
    },
  });
  const manageMembership = useMutation({
    mutationFn: async ({
      id,
      action,
    }: {
      id: string;
      action: AdminMembershipAction;
    }) => {
      const copy: Record<
        AdminMembershipAction,
        {
          title: string;
          message: string;
          confirmLabel: string;
          reason: string;
          destructive: boolean;
        }
      > = {
        activate: {
          title: "轉為正式社員",
          message:
            "確認線下訓練、審核與面試皆已完成後，系統會核發新的正式社員編號；實習社員編號仍永久保留。確定轉正嗎？",
          confirmLabel: "確認轉正",
          reason: "管理員確認線下入社流程已完成",
          destructive: false,
        },
        suspend: {
          title: "停權社員會籍",
          message:
            "停權後社員價、活動、提案與投票權限會立即失效。確定繼續嗎？",
          confirmLabel: "確認停權",
          reason: "管理員於 Sandbox 執行會籍停權",
          destructive: true,
        },
        resign: {
          title: "辦理社員退社",
          message:
            "退社後會籍將結束；股金仍須另行建立返還紀錄。確定繼續嗎？",
          confirmLabel: "確認退社",
          reason: "管理員於 Sandbox 辦理社員退社",
          destructive: true,
        },
        terminate: {
          title: "終止社員會籍",
          message:
            "終止後社員權限會立即失效，且此操作無法在本畫面復原。確定繼續嗎？",
          confirmLabel: "確認終止",
          reason: "管理員於 Sandbox 終止社員會籍",
          destructive: true,
        },
        "share-capital-return": {
          title: "建立股金返還紀錄",
          message:
            "只有已退社且已繳股金的會籍可以返還；Sandbox 不會執行真實退款。確定繼續嗎？",
          confirmLabel: "確認建立",
          reason: "管理員於 Sandbox 建立退社股金返還紀錄",
          destructive: false,
        },
      };
      const selected = copy[action];
      const confirmed = await confirmAction({
        title: selected.title,
        message: selected.message,
        confirmLabel: selected.confirmLabel,
        destructive: selected.destructive,
      });
      if (!confirmed) return null;
      return action === "activate"
        ? api.adminActivateMember(id, selected.reason)
        : api.adminMembershipAction(id, action, selected.reason);
    },
    onSuccess: async (result, variables) => {
      if (!result) return;
      if (variables.action === "share-capital-return") {
        setReturnedShareCapitalIds((current) =>
          current.includes(variables.id)
            ? current
            : [...current, variables.id],
        );
      }
      await announce(
        variables.action === "activate"
          ? "實習社員已轉為正式社員"
          : variables.action === "share-capital-return"
          ? "Sandbox 股金返還紀錄已建立"
          : "社員會籍狀態已更新",
      );
    },
  });
  const reviewActivity = useMutation({
    mutationFn: async ({
      id,
      action,
    }: {
      id: string;
      action: "approve" | "reject" | "cancel" | "complete";
    }) => {
      const actionCopy = {
        approve: ["審核發布社員活動", "發布後有效社員即可查看與報名。確定發布嗎？", "確認發布"],
        reject: ["不通過社員活動", "活動提案將被駁回，且不會對社員發布。確定不通過嗎？", "確認不通過"],
        cancel: ["取消社員活動", "活動將取消，已報名與候補社員會收到狀態更新。確定取消嗎？", "確認取消"],
        complete: ["結束社員活動", "活動將結案，後續不可再報名。確定結束嗎？", "確認結束"],
      } as const;
      const [title, message, confirmLabel] = actionCopy[action];
      const confirmed = await confirmAction({
        title,
        message,
        confirmLabel,
        destructive: ["reject", "cancel"].includes(action),
      });
      if (!confirmed) return null;
      const reason =
        action === "approve"
          ? "活動內容與時間已審核"
          : action === "reject"
            ? "活動內容目前不適合發布"
            : action === "cancel"
              ? "管理員取消活動"
              : "活動已完成";
      return api.adminReviewActivity(id, action, reason);
    },
    onSuccess: (result) => {
      if (result) announce("社員活動狀態已更新");
    },
  });
  const markActivityAttendance = useMutation({
    mutationFn: async ({
      activityId,
      registrationId,
      displayName,
      status,
    }: {
      activityId: string;
      registrationId: string;
      displayName: string;
      status: "attended" | "no_show";
    }) => {
      const confirmed = await confirmAction({
        title: status === "attended" ? "確認社員簽到" : "標記未出席",
        message:
          status === "attended"
            ? `確認 ${displayName} 已到場並完成簽到？`
            : `確認將 ${displayName} 標記為未出席？`,
        confirmLabel: status === "attended" ? "確認簽到" : "確認未出席",
        destructive: status === "no_show",
      });
      if (!confirmed) return null;
      return api.adminMarkActivityAttendance(
        activityId,
        registrationId,
        status,
      );
    },
    onSuccess: (result) => {
      if (result) announce("活動出席狀態已更新");
    },
  });
  const reviewMemberProposal = useMutation({
    mutationFn: async ({
      id,
      action,
    }: {
      id: string;
      action: "approve" | "reject" | "close";
    }) => {
      const confirmed = await confirmAction({
        title:
          action === "approve"
            ? "核准社員提案"
            : action === "reject"
              ? "駁回社員提案"
              : "結案社員提案",
        message:
          action === "approve"
            ? "核准後將進入討論與後續表決流程。確定核准嗎？"
            : action === "reject"
              ? "提案將被駁回並停止後續流程。確定駁回嗎？"
              : "系統會保存目前結果並關閉提案。確定結案嗎？",
        confirmLabel:
          action === "approve"
            ? "確認核准"
            : action === "reject"
              ? "確認駁回"
              : "確認結案",
        destructive: action !== "approve",
      });
      if (!confirmed) return null;
      return api.adminReviewMemberProposal(
        id,
        action,
        action === "reject"
          ? "提案目前不符合審核條件"
          : action === "close"
            ? "管理員已記錄處理結果"
            : undefined,
      );
    },
    onSuccess: (result) => {
      if (result) announce("社員提案狀態已更新");
    },
  });
  const redeemMealOrder = useMutation({
    mutationFn: async ({
      eventId,
      pickupCode,
    }: {
      eventId: string;
      pickupCode: string;
    }) => {
      const confirmed = await confirmAction({
        title: "核銷便當取餐",
        message: `確認取餐碼 ${pickupCode} 已由現場工作人員核對並完成交付？`,
        confirmLabel: "確認核銷",
        destructive: false,
      });
      if (!confirmed) return null;
      return api.adminRedeemMealOrder(eventId, pickupCode);
    },
    onSuccess: (result, variables) => {
      if (!result) return;
      setMealPickupCodes((current) => ({
        ...current,
        [variables.eventId]: "",
      }));
      announce("便當已完成取餐核銷");
    },
  });
  const reset = useMutation({
    mutationFn: api.resetDemo,
    onSuccess: async () => {
      setResetConfirmation("");
      setReturnedShareCapitalIds([]);
      await announce("展示資料已恢復為初始狀態");
    },
  });

  const loading = [
    products,
    bundles,
    proposals,
    campaigns,
    orders,
    meals,
    mealEvents,
    membershipApplications,
    members,
    activities,
    memberProposals,
  ].some((query) => query.isLoading);
  const mutationError = [
    toggleProduct,
    createProduct,
    createDirectCampaign,
    reviewProposal,
    convertProposal,
    campaignDecision,
    advanceOrder,
    refundOrder,
    advanceShipment,
    createShipment,
    createMeal,
    createMealEvent,
    mealEventAction,
    duplicateMealEvent,
    redeemMealOrder,
    reviewMembership,
    viewMembershipDocument,
    manageMembership,
    reviewActivity,
    markActivityAttendance,
    reviewMemberProposal,
    reset,
  ].find((mutation) => mutation.error)?.error;
  const salesQueries = [
    products,
    bundles,
    proposals,
    campaigns,
    meals,
    mealEvents,
  ];
  const fulfillmentQueries = [orders];
  const socialQueries = [
    membershipApplications,
    members,
    activities,
    memberProposals,
  ];
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

  const submitMeal = () => {
    const name = mealDraft.name.trim();
    const price = Number(mealDraft.price);
    if (!name) {
      setMealFormError("請填寫餐點名稱");
      return;
    }
    if (!mealDraft.price || !Number.isInteger(price) || price < 0) {
      setMealFormError("餐點價格請輸入大於或等於 0 的整數");
      return;
    }
    setMealFormError("");
    createMeal.mutate({
      name,
      description: mealDraft.description.trim(),
      price,
      tax_type: mealDraft.taxType,
    });
  };

  const submitMealEvent = () => {
    const title = mealEventDraft.title.trim();
    const location = mealEventDraft.location.trim();
    const capacity = Number(mealEventDraft.capacity);
    const selectedMeal = (meals.data ?? []).find(
      (meal) => meal.id === mealEventDraft.mealId && meal.is_active,
    );
    if (!title || !location) {
      setMealEventFormError("請填寫場次名稱與取餐地點");
      return;
    }
    if (!selectedMeal) {
      setMealEventFormError("請選擇一項有效餐點");
      return;
    }
    if (!Number.isInteger(capacity) || capacity < 1) {
      setMealEventFormError("預購份數請輸入大於 0 的整數");
      return;
    }
    const orderingStartsAt = Date.now() + 5 * 60 * 1000;
    const orderingEndsAt = orderingStartsAt + 24 * 60 * 60 * 1000;
    const pickupStartsAt = orderingEndsAt + 2 * 60 * 60 * 1000;
    const pickupEndsAt = pickupStartsAt + 2 * 60 * 60 * 1000;
    setMealEventFormError("");
    createMealEvent.mutate({
      title,
      location,
      ordering_starts_at: new Date(orderingStartsAt).toISOString(),
      ordering_ends_at: new Date(orderingEndsAt).toISOString(),
      pickup_starts_at: new Date(pickupStartsAt).toISOString(),
      pickup_ends_at: new Date(pickupEndsAt).toISOString(),
      offerings: [
        {
          meal_id: selectedMeal.id,
          price: selectedMeal.price,
          capacity,
          position: 0,
        },
      ],
    });
  };

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
        ? productDraft.temperatureZone === "ambient"
          ? ["home_delivery", "seven_eleven", "family_mart", "hilife"]
          : ["home_delivery"]
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
        <Button
          compact
          icon="analytics-outline"
          label="財務制度"
          onPress={() => router.push("/admin-cooperative" as never)}
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
          <QuerySectionError
            label="管理總覽"
            queries={[...salesQueries, ...fulfillmentQueries, ...socialQueries]}
          />
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
          <QuerySectionError label="販售資料" queries={salesQueries} />
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
          {!products.isError && (products.data ?? []).length === 0 ? (
            <EmptyState
              description="目前沒有商品，可使用上方表單建立第一項商品。"
              icon="storefront-outline"
              title="尚無商品"
            />
          ) : null}
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
              {product.is_active ? (
                <View style={styles.actions}>
                  <Button
                    compact
                    label="直接開團"
                    loading={
                      createDirectCampaign.isPending &&
                      createDirectCampaign.variables === product.id
                    }
                    onPress={() => createDirectCampaign.mutate(product.id)}
                    variant="secondary"
                  />
                </View>
              ) : null}
            </View>
          ))}

          <Text style={styles.sectionTitle}>團購提案與正式團購</Text>
          {!proposals.isError &&
          !campaigns.isError &&
          (proposals.data ?? []).length === 0 &&
          (campaigns.data ?? []).length === 0 ? (
            <EmptyState
              description="目前沒有待審提案或正式團購。"
              icon="people-outline"
              title="尚無團購資料"
            />
          ) : null}
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

          <Text style={styles.sectionTitle}>新增便當餐點</Text>
          <View style={styles.form}>
            <Field
              label="餐點名稱"
              onChange={(name) =>
                setMealDraft((current) => ({ ...current, name }))
              }
              placeholder="例如：季節蔬食便當"
              value={mealDraft.name}
            />
            <Field
              label="餐點介紹"
              multiline
              onChange={(description) =>
                setMealDraft((current) => ({ ...current, description }))
              }
              placeholder="主菜、配菜與食材特色"
              value={mealDraft.description}
            />
            <Field
              keyboardType="number-pad"
              label="單價（元）"
              onChange={(price) =>
                setMealDraft((current) => ({ ...current, price }))
              }
              placeholder="120"
              value={mealDraft.price}
            />
            <Text style={styles.fieldLabel}>稅別</Text>
            <View style={styles.optionWrap}>
              {[
                { value: "taxable" as const, label: "應稅" },
                { value: "tax_exempt" as const, label: "免稅" },
              ].map((option) => (
                <Choice
                  key={option.value}
                  label={option.label}
                  onPress={() =>
                    setMealDraft((current) => ({
                      ...current,
                      taxType: option.value,
                    }))
                  }
                  selected={mealDraft.taxType === option.value}
                />
              ))}
            </View>
            {mealFormError ? (
              <InlineMessage text={mealFormError} tone="danger" />
            ) : null}
            <Button
              icon="add-circle-outline"
              label="建立餐點"
              loading={createMeal.isPending}
              onPress={submitMeal}
              variant="secondary"
            />
          </View>

          <Text style={styles.sectionTitle}>建立便當場次</Text>
          <View style={styles.form}>
            <Field
              label="場次名稱"
              onChange={(title) =>
                setMealEventDraft((current) => ({ ...current, title }))
              }
              placeholder="例如：校園週三午餐預購"
              value={mealEventDraft.title}
            />
            <Field
              label="取餐地點"
              onChange={(location) =>
                setMealEventDraft((current) => ({ ...current, location }))
              }
              placeholder="例如：學生活動中心前廣場"
              value={mealEventDraft.location}
            />
            <Text style={styles.fieldLabel}>本場餐點</Text>
            {(meals.data ?? []).some((meal) => meal.is_active) ? (
              <View style={styles.optionWrap}>
                {(meals.data ?? [])
                  .filter((meal) => meal.is_active)
                  .map((meal) => (
                    <Choice
                      key={meal.id}
                      label={`${meal.name} ${money(meal.price)}`}
                      onPress={() =>
                        setMealEventDraft((current) => ({
                          ...current,
                          mealId: meal.id,
                        }))
                      }
                      selected={mealEventDraft.mealId === meal.id}
                    />
                  ))}
              </View>
            ) : (
              <InlineMessage text="尚無餐點，請先使用上方表單建立第一項餐點。" />
            )}
            <Field
              keyboardType="number-pad"
              label="可預購份數"
              onChange={(capacity) =>
                setMealEventDraft((current) => ({ ...current, capacity }))
              }
              placeholder="20"
              value={mealEventDraft.capacity}
            />
            <InlineMessage text="Sandbox 快速建場：5 分鐘後開賣、24 小時後截止，截止 2 小時後開放取餐，取餐時段 2 小時。" />
            {mealEventFormError ? (
              <InlineMessage text={mealEventFormError} tone="danger" />
            ) : null}
            <Button
              disabled={!(meals.data ?? []).some((meal) => meal.is_active)}
              icon="calendar-outline"
              label="建立草稿場次"
              loading={createMealEvent.isPending}
              onPress={submitMealEvent}
            />
          </View>

          <Text style={styles.sectionTitle}>便當場次</Text>
          {!mealEvents.isError && (mealEvents.data ?? []).length === 0 ? (
            <EmptyState
              description="建立或複製便當場次後，會顯示在這裡。"
              icon="restaurant-outline"
              title="尚無便當場次"
            />
          ) : null}
          {(mealEvents.data ?? []).map((event) => (
            <View key={event.id} style={styles.adminCard}>
              <CardTitle status={event.status} title={event.title} />
              <Text style={styles.rowMeta}>{event.venue_name}</Text>
              {event.status === "pickup_open" ? (
                <View style={styles.redeemPanel}>
                  <Text style={styles.fieldLabel}>現場取餐核銷</Text>
                  <Text style={styles.rowMeta}>
                    請核對買家畫面後輸入六位取餐碼。
                  </Text>
                  <View style={styles.redeemRow}>
                    <TextInput
                      accessibilityLabel={`${event.title}六位取餐碼`}
                      keyboardType="number-pad"
                      maxLength={6}
                      onChangeText={(value) =>
                        setMealPickupCodes((current) => ({
                          ...current,
                          [event.id]: value.replace(/\D/g, ""),
                        }))
                      }
                      placeholder="000000"
                      placeholderTextColor={colors.sage}
                      style={[styles.input, styles.redeemInput]}
                      value={mealPickupCodes[event.id] ?? ""}
                    />
                    <Button
                      compact
                      disabled={!/^\d{6}$/.test(mealPickupCodes[event.id] ?? "")}
                      label="確認核銷"
                      loading={redeemMealOrder.isPending}
                      onPress={() =>
                        redeemMealOrder.mutate({
                          eventId: event.id,
                          pickupCode: mealPickupCodes[event.id] ?? "",
                        })
                      }
                      variant="secondary"
                    />
                  </View>
                </View>
              ) : null}
              <View style={styles.actions}>
                <Button
                  compact
                  label="複製場次"
                  onPress={() => duplicateMealEvent.mutate(event.id)}
                  variant="quiet"
                />
                {event.status === "draft" ? (
                  <Button
                    compact
                    label="發布場次"
                    onPress={() =>
                      mealEventAction.mutate({
                        id: event.id,
                        action: "publish",
                      })
                    }
                  />
                ) : null}
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
                {event.status === "pickup_open" ? (
                  <Button
                    compact
                    label="結束取餐"
                    onPress={() =>
                      mealEventAction.mutate({
                        id: event.id,
                        action: "complete",
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
          <QuerySectionError
            label="訂單與物流資料"
            queries={fulfillmentQueries}
          />
          <InlineMessage text="後台貨態推進是 Sandbox 功能，正式環境只接受已驗證的物流回呼。" />
          {!orders.isError && (orders.data ?? []).length === 0 ? (
            <EmptyState
              description="顧客完成下單後，訂單與物流進度會顯示在這裡。"
              icon="receipt-outline"
              title="尚無訂單"
            />
          ) : null}
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
                      label={`物流 ${shipmentStatusLabel(
                        order.shipment.status,
                      )}`}
                      tone={
                        order.shipment.status === "delivered"
                          ? "positive"
                          : order.shipment.status === "exception"
                            ? "danger"
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
                  {(() => {
                    const next = order.shipment
                      ? nextShipmentStatus(order.shipment.status)
                      : null;
                    return next &&
                      order.available_actions.includes("advance_shipment") ? (
                      <Button
                        compact
                        label={`Sandbox 推進為${shipmentStatusLabel(next)}`}
                        onPress={() =>
                          advanceShipment.mutate({
                            id: order.id,
                            status: next,
                          })
                        }
                        variant="quiet"
                      />
                    ) : null;
                  })()}
                  {order.available_actions.includes("create_shipment") ? (
                    <Button
                      compact
                      label="建立正式物流單"
                      loading={createShipment.isPending}
                      onPress={() => createShipment.mutate(order.id)}
                      variant="secondary"
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
          <QuerySectionError label="社務資料" queries={socialQueries} />
          <Text style={styles.sectionTitle}>入社申請</Text>
          {!membershipApplications.isError &&
          (membershipApplications.data ?? []).length === 0 ? (
            <EmptyState
              description="新申請送件後，會出現在這裡供管理員審查。"
              icon="person-add-outline"
              title="目前沒有入社申請"
            />
          ) : null}
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
              <View style={styles.actions}>
                <Button
                  compact
                  label={
                    expandedMembershipApplicationId === application.id
                      ? "收合審查資料"
                      : "審查私密資料"
                  }
                  onPress={() =>
                    setExpandedMembershipApplicationId((current) =>
                      current === application.id ? null : application.id,
                    )
                  }
                  variant="quiet"
                />
              </View>
              {expandedMembershipApplicationId === application.id ? (
                membershipApplicationDetail.isLoading ? (
                  <LoadingState label="載入私密審查資料" />
                ) : membershipApplicationDetail.isError ||
                  !membershipApplicationDetail.data ? (
                  <InlineMessage
                    text="私密資料載入失敗，尚未執行核准。"
                    tone="danger"
                  />
                ) : (
                  <View style={styles.privateReview}>
                    <InlineMessage text="僅限入社審查使用；每次檢視證件都會寫入稽核紀錄。" />
                    <Text style={styles.rowMeta}>
                      手機：{membershipApplicationDetail.data.phone}
                    </Text>
                    <Text style={styles.rowMeta}>
                      生日：{membershipApplicationDetail.data.birth_date}
                    </Text>
                    <Text style={styles.rowMeta}>
                      地址：{membershipApplicationDetail.data.address}
                    </Text>
                    <Text style={styles.rowMeta}>
                      緊急聯絡人：
                      {membershipApplicationDetail.data.emergency_contact_name}　
                      {membershipApplicationDetail.data.emergency_contact_phone}
                    </Text>
                    <View style={styles.actions}>
                      {(membershipApplicationDetail.data.documents ?? [])
                        .filter((document) => document.status === "confirmed")
                        .map((document) => (
                          <Button
                            compact
                            key={document.id}
                            label={`檢視${membershipDocumentLabels[document.document_type]}`}
                            loading={
                              viewMembershipDocument.isPending &&
                              viewMembershipDocument.variables?.documentId ===
                                document.id
                            }
                            onPress={() =>
                              viewMembershipDocument.mutate({
                                applicationId: application.id,
                                documentId: document.id,
                              })
                            }
                            variant="secondary"
                          />
                        ))}
                    </View>
                  </View>
                )
              ) : null}
              {["submitted", "needs_revision"].includes(application.status) ? (
                <View style={styles.actions}>
                  {application.status === "submitted" ? (
                    <>
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
                    </>
                  ) : null}
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

          <Text style={styles.sectionTitle}>社員管理</Text>
          {!members.isError && (members.data ?? []).length === 0 ? (
            <EmptyState
              description="核准申請並完成入社款項後，會籍會出現在這裡。"
              icon="people-outline"
              title="目前沒有社員會籍"
            />
          ) : null}
          {(members.data ?? []).map((membership) => {
            const isCurrentAdmin = membership.user_id === user.id;
            const availableActions = isCurrentAdmin
              ? []
              : membershipActionsFor(membership).filter(
                  (action) =>
                    action !== "share-capital-return" ||
                    !returnedShareCapitalIds.includes(membership.id),
                );
            return (
              <View key={membership.id} style={styles.adminCard}>
                <CardTitle
                  status={membershipStatusLabels[membership.status]}
                  title={
                    membership.member_number ??
                    membership.trainee_number ??
                    "尚未編社員號"
                  }
                />
                <Text style={styles.rowMeta}>
                  {membership.member_number
                    ? `正式社員編號 ${membership.member_number}`
                    : membership.trainee_number
                      ? `實習社員編號 ${membership.trainee_number}`
                      : "尚未取得實習社員編號"}
                </Text>
                {membership.member_number && membership.trainee_number ? (
                  <Text style={styles.rowMeta}>
                    原實習社員編號 {membership.trainee_number}
                  </Text>
                ) : null}
                <Text style={styles.rowMeta}>
                  {membership.nickname || "社員"}
                  {membership.started_at
                    ? `　啟用於 ${new Date(
                        membership.started_at,
                      ).toLocaleDateString("zh-TW")}`
                    : ""}
                </Text>
                {membership.status_reason ? (
                  <Text style={styles.rowMeta}>
                    處理原因：{membership.status_reason}
                  </Text>
                ) : null}
                {isCurrentAdmin ? (
                  <InlineMessage text="目前登入的管理員會籍不可在自己的工作階段中變更。" />
                ) : null}
                {returnedShareCapitalIds.includes(membership.id) ? (
                  <StatusPill label="Sandbox 股金已返還" tone="positive" />
                ) : null}
                {availableActions.length ? (
                  <View style={styles.actions}>
                    {availableActions.map((action) => (
                      <Button
                        compact
                        key={action}
                        label={membershipActionLabels[action]}
                        loading={
                          manageMembership.isPending &&
                          manageMembership.variables?.id === membership.id &&
                          manageMembership.variables?.action === action
                        }
                        onPress={() =>
                          manageMembership.mutate({
                            id: membership.id,
                            action,
                          })
                        }
                        variant={
                          action === "activate" ||
                          action === "share-capital-return"
                            ? "secondary"
                            : action === "suspend"
                              ? "quiet"
                              : "danger"
                        }
                      />
                    ))}
                  </View>
                ) : null}
              </View>
            );
          })}

          <Text style={styles.sectionTitle}>社員活動</Text>
          {!activities.isError && (activities.data ?? []).length === 0 ? (
            <EmptyState
              description="社員送出活動並進入審核後，會顯示在這裡。"
              icon="calendar-outline"
              title="目前沒有社員活動"
            />
          ) : null}
          {(activities.data ?? []).map((activity) => (
            <View key={activity.id} style={styles.adminCard}>
              <CardTitle status={activity.status} title={activity.title} />
              <Text style={styles.rowMeta}>
                {activity.registered_count}/{activity.capacity} 人，候補{" "}
                {activity.waitlist_count} 人
              </Text>
              <View style={styles.actions}>
                <Button
                  compact
                  label={
                    expandedActivityId === activity.id
                      ? "收合報名名單"
                      : "查看報名名單"
                  }
                  onPress={() =>
                    setExpandedActivityId((current) =>
                      current === activity.id ? null : activity.id,
                    )
                  }
                  variant="quiet"
                />
                {activity.status === "pending_review" ? (
                  <>
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
                    <Button
                      compact
                      label="不通過"
                      onPress={() =>
                        reviewActivity.mutate({
                          id: activity.id,
                          action: "reject",
                        })
                      }
                      variant="danger"
                    />
                  </>
                ) : null}
                {activity.status === "published" ? (
                  <>
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
                    <Button
                      compact
                      label="取消活動"
                      onPress={() =>
                        reviewActivity.mutate({
                          id: activity.id,
                          action: "cancel",
                        })
                      }
                      variant="danger"
                    />
                  </>
                ) : null}
              </View>
              {expandedActivityId === activity.id ? (
                <View style={styles.registrationList}>
                  <Text style={styles.fieldLabel}>報名名單</Text>
                  {activityRegistrations.isFetching ? (
                    <LoadingState label="載入活動報名名單" />
                  ) : null}
                  {activityRegistrations.isError ? (
                    <View style={styles.queryError}>
                      <InlineMessage
                        text={`報名名單載入失敗：${getErrorMessage(
                          activityRegistrations.error,
                        )}`}
                        tone="danger"
                      />
                      <Button
                        compact
                        label="重新載入名單"
                        onPress={() => activityRegistrations.refetch()}
                        variant="secondary"
                      />
                    </View>
                  ) : null}
                  {!activityRegistrations.isFetching &&
                  !activityRegistrations.isError &&
                  (activityRegistrations.data ?? []).length === 0 ? (
                    <InlineMessage text="目前尚無社員報名此活動。" />
                  ) : null}
                  {(activityRegistrations.data ?? []).map((registration) => (
                    <View key={registration.id} style={styles.registrationRow}>
                      <View style={styles.registrationCopy}>
                        <Text style={styles.rowTitle}>
                          {registration.display_name}
                        </Text>
                        <Text style={styles.rowMeta}>{registration.email}</Text>
                        <StatusPill
                          label={activityRegistrationLabels[registration.status]}
                        />
                      </View>
                      {registration.status === "registered" ? (
                        <View style={styles.actions}>
                          <Button
                            compact
                            label="簽到"
                            loading={
                              markActivityAttendance.isPending &&
                              markActivityAttendance.variables
                                ?.registrationId === registration.id &&
                              markActivityAttendance.variables?.status ===
                                "attended"
                            }
                            onPress={() =>
                              markActivityAttendance.mutate({
                                activityId: activity.id,
                                registrationId: registration.id,
                                displayName: registration.display_name,
                                status: "attended",
                              })
                            }
                          />
                          <Button
                            compact
                            label="未出席"
                            loading={
                              markActivityAttendance.isPending &&
                              markActivityAttendance.variables
                                ?.registrationId === registration.id &&
                              markActivityAttendance.variables?.status ===
                                "no_show"
                            }
                            onPress={() =>
                              markActivityAttendance.mutate({
                                activityId: activity.id,
                                registrationId: registration.id,
                                displayName: registration.display_name,
                                status: "no_show",
                              })
                            }
                            variant="danger"
                          />
                        </View>
                      ) : null}
                    </View>
                  ))}
                </View>
              ) : null}
            </View>
          ))}

          <Text style={styles.sectionTitle}>社員治理提案</Text>
          {!memberProposals.isError &&
          (memberProposals.data ?? []).length === 0 ? (
            <EmptyState
              description="社員提案送審後，會顯示在這裡。"
              icon="document-text-outline"
              title="目前沒有社員提案"
            />
          ) : null}
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

function QuerySectionError({
  label,
  queries,
}: {
  label: string;
  queries: {
    error: unknown;
    isError: boolean;
    isFetching: boolean;
    refetch: () => Promise<unknown>;
  }[];
}) {
  const failedQueries = queries.filter((query) => query.isError);
  if (failedQueries.length === 0) return null;
  return (
    <View style={styles.queryError}>
      <InlineMessage
        text={`${label}載入失敗：${getErrorMessage(failedQueries[0]?.error)}`}
        tone="danger"
      />
      <Button
        compact
        label="重新載入"
        loading={failedQueries.some((query) => query.isFetching)}
        onPress={() => {
          void Promise.all(failedQueries.map((query) => query.refetch()));
        }}
        variant="secondary"
      />
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
  privateReview: {
    backgroundColor: colors.cream,
    borderColor: colors.line,
    borderRadius: radii.md,
    borderWidth: 1,
    gap: 8,
    padding: spacing.md,
  },
  statusLine: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  redeemPanel: {
    backgroundColor: colors.cream,
    borderColor: colors.line,
    borderRadius: radii.md,
    borderWidth: 1,
    gap: 7,
    padding: 12,
  },
  redeemRow: { alignItems: "center", flexDirection: "row", gap: 8 },
  redeemInput: {
    flex: 1,
    fontSize: 18,
    fontWeight: "900",
    letterSpacing: 4,
    minHeight: 44,
  },
  registrationList: {
    backgroundColor: colors.cream,
    borderColor: colors.line,
    borderRadius: radii.md,
    borderWidth: 1,
    gap: 8,
    padding: 12,
  },
  registrationRow: {
    alignItems: "flex-start",
    backgroundColor: colors.paper,
    borderRadius: radii.md,
    gap: 10,
    padding: 12,
  },
  registrationCopy: { gap: 5 },
  queryError: { gap: 8 },
  settingLabel: {
    color: colors.forest,
    fontSize: 12,
    fontWeight: "800",
    marginTop: 5,
  },
  settingValue: { color: colors.muted, fontSize: 13 },
  pressed: { opacity: 0.74, transform: [{ scale: 0.99 }] },
});
