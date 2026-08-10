import { Ionicons } from "@expo/vector-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { router, useLocalSearchParams } from "expo-router";
import { useEffect, useState } from "react";
import {
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from "react-native";

import {
  Button,
  EmptyState,
  InlineMessage,
  LoadingState,
  PageHeader,
  Screen,
  StatusPill,
} from "../../src/components/ui";
import {
  pickMembershipDocument,
  putDocumentToStorage,
} from "../../src/lib/documentUpload";
import { money } from "../../src/lib/format";
import { confirmAction } from "../../src/lib/confirm";
import { membershipIdentityNeedsSync } from "../../src/lib/membership";
import { openPaymentPage } from "../../src/lib/payment";
import { api, getErrorMessage } from "../../src/services/api";
import { useAuth } from "../../src/store/AuthContext";
import { colors, radii, spacing } from "../../src/theme";
import type {
  MembershipApplication,
  PaymentStatus,
} from "../../src/types";

const applicationLabels: Record<MembershipApplication["status"], string> = {
  draft: "填寫中",
  submitted: "審核中",
  needs_revision: "等待補件",
  approved: "審核通過",
  rejected: "未通過",
  withdrawn: "已撤回",
};

const documentLabels = {
  id_front: "身分證正面測試檔",
  id_back: "身分證反面測試檔",
  secondary: "第二證件測試檔",
} as const;

const paymentLabels: Record<PaymentStatus, string> = {
  pending: "待付款",
  paid: "已繳",
  late_paid_refund_required: "待退款",
  refund_pending: "退款處理中",
  refunded: "已退款",
  failed: "付款失敗",
  expired: "已失效",
};

type FormState = {
  legal_name: string;
  phone: string;
  birth_date: string;
  address: string;
  emergency_contact_name: string;
  emergency_contact_phone: string;
};

const emptyForm: FormState = {
  legal_name: "",
  phone: "",
  birth_date: "",
  address: "",
  emergency_contact_name: "",
  emergency_contact_phone: "",
};

type DirectoryFormState = {
  is_public: boolean;
  nickname: string;
  expertise: string;
  bio: string;
};

const emptyDirectoryForm: DirectoryFormState = {
  is_public: false,
  nickname: "",
  expertise: "",
  bio: "",
};

export default function MembersScreen() {
  const { isAuthenticated, refreshUser, user } = useAuth();
  const queryClient = useQueryClient();
  const params = useLocalSearchParams<{
    membership_charge_id?: string;
    payment?: string;
  }>();
  const [form, setForm] = useState<FormState>(emptyForm);
  const [directoryForm, setDirectoryForm] =
    useState<DirectoryFormState>(emptyDirectoryForm);
  const [message, setMessage] = useState("");
  const [paymentSyncUntil, setPaymentSyncUntil] = useState(0);
  const application = useQuery({
    queryKey: ["membership-application"],
    queryFn: api.membershipApplication,
    enabled: isAuthenticated,
  });
  const charges = useQuery({
    queryKey: ["membership-charges"],
    queryFn: api.membershipCharges,
    enabled: isAuthenticated,
    refetchInterval: (query) =>
      paymentSyncUntil > Date.now() &&
      query.state.data?.some((charge) => charge.payment_status === "pending")
        ? 4000
        : false,
  });
  const allChargesPaid = Boolean(charges.data?.length) &&
    charges.data!.every((charge) => charge.payment_status === "paid");
  const membership = useQuery({
    queryKey: ["membership"],
    queryFn: api.membership,
    enabled: isAuthenticated,
    refetchInterval: (query) =>
      paymentSyncUntil > Date.now() &&
      allChargesPaid &&
      query.state.data?.status === "pending_payment"
        ? 2000
        : false,
  });
  const membershipNeedsAuthSync = Boolean(
    membership.data &&
      user &&
      membershipIdentityNeedsSync(
        membership.data.status,
        user.membership_type,
      ),
  );
  const authMembershipSync = useQuery({
    queryKey: ["auth-membership-sync", user?.id],
    queryFn: refreshUser,
    enabled: membershipNeedsAuthSync,
    retry: 4,
    retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
  });
  const directory = useQuery({
    queryKey: ["member-directory"],
    queryFn: api.memberDirectory,
    enabled: user?.membership_type === "member",
  });

  useEffect(() => {
    if (!application.data) return;
    setForm({
      legal_name: application.data.legal_name ?? "",
      phone: application.data.phone ?? "",
      birth_date: application.data.birth_date ?? "",
      address: application.data.address ?? "",
      emergency_contact_name: application.data.emergency_contact_name ?? "",
      emergency_contact_phone: application.data.emergency_contact_phone ?? "",
    });
  }, [application.data]);

  useEffect(() => {
    if (!membership.data) return;
    setDirectoryForm({
      is_public: membership.data.directory_visible,
      nickname: membership.data.nickname,
      expertise: membership.data.expertise ?? "",
      bio: membership.data.bio ?? "",
    });
  }, [membership.data]);

  useEffect(() => {
    if (!params.payment) return;
    setPaymentSyncUntil(Date.now() + 60_000);
    setMessage(
      params.payment === "paid"
        ? "付款完成，正在同步款項與實習社員資格"
        : ["failed", "expired"].includes(params.payment)
          ? "這次付款未完成，可重新發起付款"
          : params.payment === "late_paid_refund_required"
            ? "付款逾時入帳，系統正在確認退款狀態"
            : "已從綠界返回，正在確認付款與會籍狀態",
    );
  }, [params.membership_charge_id, params.payment]);

  useEffect(() => {
    if (
      paymentSyncUntil > Date.now() &&
      allChargesPaid &&
      membership.data?.status === "pending_payment"
    ) {
      void membership.refetch();
    }
  }, [allChargesPaid, membership.data?.status, paymentSyncUntil]);

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["membership"] }),
      queryClient.invalidateQueries({ queryKey: ["membership-application"] }),
      queryClient.invalidateQueries({ queryKey: ["membership-charges"] }),
    ]);
  };
  const save = useMutation({
    mutationFn: () =>
      api.saveMembershipApplication({
        ...form,
        consented_at:
          application.data?.consented_at ?? new Date().toISOString(),
      }),
    onSuccess: async () => {
      setMessage("入社資料已儲存");
      await refresh();
    },
  });
  const addDocument = useMutation({
    mutationFn: async (
      document_type: "id_front" | "id_back" | "secondary",
    ) => {
      if (!application.data) {
        await save.mutateAsync();
      }
      const picked = await pickMembershipDocument();
      const upload = await api.membershipDocumentUploadUrl({
        document_type,
        content_type: picked.content_type,
        size_bytes: picked.size_bytes,
        checksum_sha256: picked.checksum_sha256,
      });
      if (upload.upload_url.startsWith("https://")) {
        await putDocumentToStorage(
          upload.upload_url,
          picked,
          upload.required_headers ?? {},
        );
      }
      return api.confirmMembershipDocument({
        document_id: upload.document_id,
        document_type,
        checksum_sha256: picked.checksum_sha256,
      });
    },
    onSuccess: async (result) => {
      setMessage(result ? "證件已上傳" : "已取消選擇");
      if (result) await refresh();
    },
  });
  const submit = useMutation({
    mutationFn: () =>
      api.submitMembershipApplication({
        ...form,
        consented_at:
          application.data?.consented_at ?? new Date().toISOString(),
      }),
    onSuccess: async () => {
      setMessage("入社申請已送出，可開始繳交入社費與股金");
      await refresh();
    },
  });
  const removeDocument = useMutation({
    mutationFn: async (documentId: string) => {
      const confirmed = await confirmAction({
        title: "移除測試證件",
        message: "移除後必須重新加入測試檔，才能再次送出申請。確定移除嗎？",
        confirmLabel: "確認移除",
        destructive: true,
      });
      if (!confirmed) return false;
      await api.deleteMembershipDocument(documentId);
      return true;
    },
    onSuccess: async (removed) => {
      if (!removed) return;
      setMessage("測試證件已移除");
      await refresh();
    },
  });
  const withdraw = useMutation({
    mutationFn: async () => {
      const confirmed = await confirmAction({
        title: "撤回入社申請",
        message:
          "撤回後此申請將結束；已繳款項會建立 Sandbox 退款紀錄。確定要撤回嗎？",
        confirmLabel: "確認撤回",
        destructive: true,
      });
      if (!confirmed) return null;
      return api.withdrawMembershipApplication();
    },
    onSuccess: async (result) => {
      if (!result) return;
      setMessage("入社申請已撤回");
      await refresh();
    },
  });
  const updateDirectory = useMutation({
    mutationFn: () =>
      api.updateMemberDirectory({
        ...directoryForm,
        nickname: directoryForm.nickname.trim(),
        expertise: directoryForm.expertise.trim(),
        bio: directoryForm.bio.trim(),
        avatar_url: membership.data?.avatar_url ?? null,
      }),
    onSuccess: async () => {
      setMessage("公開名錄設定已更新");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["membership"] }),
        queryClient.invalidateQueries({ queryKey: ["member-directory"] }),
      ]);
    },
  });
  const pay = useMutation({
    mutationFn: api.payMembershipCharge,
    onSuccess: async (payment) => {
      if (payment.payment_url) {
        setMessage("正在前往綠界測試付款頁");
        await openPaymentPage(payment.payment_url);
        setMessage("已返回 App，正在確認付款與會籍狀態");
      } else {
        setMessage("款項已完成，系統收據已建立");
      }
      setPaymentSyncUntil(Date.now() + 60_000);
      await refresh();
    },
  });
  const error =
    save.error ??
    addDocument.error ??
    removeDocument.error ??
    submit.error ??
    withdraw.error ??
    updateDirectory.error ??
    pay.error;

  if (!isAuthenticated) {
    return (
      <Screen>
        <PageHeader title="社員" />
        <EmptyState
          action="登入帳號"
          description="登入後可申請入社，或查看社員資料與服務。"
          icon="id-card-outline"
          onAction={() => router.push("/login")}
          title="請先登入"
        />
      </Screen>
    );
  }
  if (membership.isLoading || application.isLoading) {
    return <LoadingState label="載入社員資料" />;
  }
  if (membership.isError || application.isError || charges.isError) {
    return (
      <Screen>
        <PageHeader title="社員" />
        <EmptyState
          action="重新載入"
          description="目前無法取得社員或入社申請資料，請檢查網路後重試。"
          icon="cloud-offline-outline"
          onAction={() => void refresh()}
          title="社員資料載入失敗"
        />
      </Screen>
    );
  }

  const isFormalMember = user?.membership_type === "member";
  const isTrainee = user?.membership_type === "trainee";
  const hasActiveMembership =
    ["trainee", "active"].includes(membership.data?.status ?? "") &&
    (isFormalMember || isTrainee);
  const wasMembershipActivated = Boolean(
    membership.data?.trainee_number ||
      membership.data?.member_number ||
      membership.data?.started_at,
  );
  const canPayMembershipCharges =
    membership.data?.status === "pending_payment";
  const confirmed = application.data?.confirmed_documents ?? [];
  const canEditApplication =
    !application.data ||
    ["draft", "needs_revision"].includes(application.data.status);
  const canWithdraw =
    Boolean(application.data) &&
    !wasMembershipActivated &&
    !["rejected", "withdrawn"].includes(application.data?.status ?? "");
  const canSubmit =
    canEditApplication &&
    Object.values(form).every((value) => value.trim()) &&
    confirmed.length === 3;
  const messageTone = ["failed", "expired"].includes(params.payment ?? "")
    ? "danger"
    : params.payment && params.payment !== "paid"
      ? "warning"
      : "positive";

  return (
    <Screen>
      <PageHeader
        subtitle={
          isFormalMember
            ? "管理會籍資訊，並認識自願公開資料的社員。"
            : isTrainee
              ? "已享有社員價；完成線下流程後，由管理員轉為正式社員。"
            : "完成資料與測試證件，送出申請後即可繳交入社費與股金。"
        }
        title={
          isFormalMember
            ? "正式社員服務"
            : isTrainee
              ? "實習社員"
              : "入社申請"
        }
      />
      <View style={styles.content}>
        {message ? <InlineMessage text={message} tone={messageTone} /> : null}
        {error ? (
          <InlineMessage text={getErrorMessage(error)} tone="danger" />
        ) : null}
        {membershipNeedsAuthSync && authMembershipSync.isFetching ? (
          <InlineMessage text="會籍狀態已更新，正在同步帳號權限。" />
        ) : membershipNeedsAuthSync && authMembershipSync.isError ? (
          <View style={styles.syncError}>
            <InlineMessage
              text="會籍狀態已更新，但帳號權限尚未同步。"
              tone="danger"
            />
            <Button
              compact
              label="重新同步社員資格"
              onPress={() => void authMembershipSync.refetch()}
              variant="secondary"
            />
          </View>
        ) : null}

        {hasActiveMembership ? (
          <>
            <View style={styles.memberCard}>
              <View style={styles.memberIdentity}>
                <View style={styles.avatar}>
                  <Text style={styles.avatarText}>
                    {membership.data?.nickname.slice(0, 1)}
                  </Text>
                </View>
                <View style={styles.memberCopy}>
                  <Text style={styles.memberName}>
                    {membership.data?.nickname}
                  </Text>
                  <Text style={styles.memberNumberLabel}>
                    {isTrainee ? "實習社員編號" : "正式社員編號"}
                  </Text>
                  <Text style={styles.memberNumber}>
                    {isTrainee
                      ? membership.data?.trainee_number
                      : membership.data?.member_number}
                  </Text>
                </View>
              </View>
              <StatusPill
                label={isTrainee ? "實習社員・社員價" : "正式社員"}
                tone="positive"
              />
              {membership.data?.bio ? (
                <Text style={styles.memberBio}>{membership.data.bio}</Text>
              ) : null}
              {isFormalMember ? (
                <Text style={styles.visibility}>
                  社員名錄：
                  {membership.data?.directory_visible ? "自願公開" : "不公開"}
                </Text>
              ) : null}
            </View>

            {isTrainee ? (
              <InlineMessage
                text="入社費與股金已繳清，現在享有社員價。線下流程完成後，請等待管理員轉為正式社員；活動、提案、投票、積點與結餘分配尚未開放。"
                tone="positive"
              />
            ) : null}

            {isFormalMember ? (
              <>
                <Text style={styles.sectionTitle}>我的公開名錄</Text>
                <View style={styles.formCard}>
              <View style={styles.switchRow}>
                <View style={styles.switchCopy}>
                  <Text style={styles.fieldLabel}>公開給其他有效社員</Text>
                  <Text style={styles.helperText}>
                    只會顯示下方暱稱、專長與自我介紹。
                  </Text>
                </View>
                <Switch
                  accessibilityLabel="公開社員名錄資料"
                  onValueChange={(is_public) =>
                    setDirectoryForm((current) => ({ ...current, is_public }))
                  }
                  trackColor={{ false: colors.line, true: colors.sage }}
                  value={directoryForm.is_public}
                />
              </View>
              <View style={styles.field}>
                <Text style={styles.fieldLabel}>暱稱</Text>
                <TextInput
                  maxLength={80}
                  onChangeText={(nickname) =>
                    setDirectoryForm((current) => ({ ...current, nickname }))
                  }
                  placeholder="社員名錄顯示名稱"
                  placeholderTextColor={colors.sage}
                  style={styles.input}
                  value={directoryForm.nickname}
                />
              </View>
              <View style={styles.field}>
                <Text style={styles.fieldLabel}>專長</Text>
                <TextInput
                  maxLength={240}
                  onChangeText={(expertise) =>
                    setDirectoryForm((current) => ({ ...current, expertise }))
                  }
                  placeholder="例如：食農教育、步道植物"
                  placeholderTextColor={colors.sage}
                  style={styles.input}
                  value={directoryForm.expertise}
                />
              </View>
              <View style={styles.field}>
                <Text style={styles.fieldLabel}>自我介紹</Text>
                <TextInput
                  maxLength={2000}
                  multiline
                  onChangeText={(bio) =>
                    setDirectoryForm((current) => ({ ...current, bio }))
                  }
                  placeholder="分享你願意公開給社員認識的內容"
                  placeholderTextColor={colors.sage}
                  style={[styles.input, styles.textarea]}
                  textAlignVertical="top"
                  value={directoryForm.bio}
                />
              </View>
              <Button
                disabled={!directoryForm.nickname.trim()}
                label="儲存名錄設定"
                loading={updateDirectory.isPending}
                onPress={() => updateDirectory.mutate()}
                variant="secondary"
              />
                </View>
              </>
            ) : null}

            <Text style={styles.sectionTitle}>款項與系統收據</Text>
            {(charges.data ?? []).map((charge) => (
              <View key={charge.id} style={styles.chargeRow}>
                <View>
                  <Text style={styles.chargeTitle}>
                    {charge.charge_type === "joining_fee" ? "入社費" : "股金"}
                  </Text>
                  <Text style={styles.chargeMeta}>
                    {charge.receipt_number ?? "尚未產生收據"}
                  </Text>
                </View>
                <View style={styles.chargeEnd}>
                  <Text style={styles.chargeAmount}>{money(charge.amount)}</Text>
                  {charge.payment_status === "pending" ? (
                    <Button
                      compact
                      disabled={!canPayMembershipCharges}
                      label="測試付款"
                      loading={pay.isPending}
                      onPress={() => pay.mutate(charge.id)}
                      variant="secondary"
                    />
                  ) : (
                    <StatusPill
                      label={paymentLabels[charge.payment_status]}
                      tone={
                        charge.payment_status === "paid"
                          ? "positive"
                          : charge.payment_status === "refunded"
                            ? "neutral"
                            : "warning"
                      }
                    />
                  )}
                </View>
              </View>
            ))}

            {isFormalMember ? (
              <>
                <Text style={styles.sectionTitle}>社員名錄</Text>
                {directory.isLoading ? (
                  <LoadingState label="載入社員名錄" />
                ) : directory.isError ? (
                  <EmptyState
                    action="重新載入"
                    description="目前無法取得社員名錄。"
                    onAction={() => directory.refetch()}
                    title="社員名錄載入失敗"
                  />
                ) : directory.data?.length ? (
                  <View style={styles.directoryGrid}>
                    {directory.data.map((member) => (
                      <View key={member.id} style={styles.directoryCard}>
                        <View style={styles.smallAvatar}>
                          <Text style={styles.smallAvatarText}>
                            {member.nickname.slice(0, 1)}
                          </Text>
                        </View>
                        <Text style={styles.directoryName}>{member.nickname}</Text>
                        <Text style={styles.directoryExpertise}>
                          {member.expertise ?? "社員"}
                        </Text>
                        <Text numberOfLines={3} style={styles.directoryBio}>
                          {member.bio}
                        </Text>
                      </View>
                    ))}
                  </View>
                ) : (
                  <EmptyState
                    description="目前沒有社員選擇公開資料。"
                    title="名錄尚無資料"
                  />
                )}
              </>
            ) : null}
          </>
        ) : (
          <>
            <View style={styles.progressCard}>
              <View>
                <Text style={styles.progressLabel}>一般買家編號</Text>
                <Text style={styles.customerNumber}>{user?.customer_number}</Text>
                <Text style={styles.progressLabel}>入社進度</Text>
                <Text style={styles.progressTitle}>
                  {application.data
                    ? applicationLabels[application.data.status]
                    : "尚未開始"}
                </Text>
              </View>
              <StatusPill
                label={`${confirmed.length}/3 份測試證件`}
                tone={confirmed.length === 3 ? "positive" : "warning"}
              />
            </View>
            {application.data?.review_note ? (
              <InlineMessage text={application.data.review_note} />
            ) : null}
            {canWithdraw ? (
              <Button
                label="撤回入社申請"
                loading={withdraw.isPending}
                onPress={() => withdraw.mutate()}
                variant="danger"
              />
            ) : null}

            <Text style={styles.sectionTitle}>基本資料</Text>
            {([
              ["legal_name", "姓名", "例如：林雨青"],
              ["phone", "手機", "0912-345-678"],
              ["birth_date", "生日", "YYYY-MM-DD"],
              ["address", "地址", "縣市、區域與路段"],
              ["emergency_contact_name", "緊急聯絡人", "聯絡人姓名"],
              ["emergency_contact_phone", "緊急聯絡電話", "聯絡人手機"],
            ] as const).map(([key, label, placeholder]) => (
              <View key={key} style={styles.field}>
                <Text style={styles.fieldLabel}>{label}</Text>
                <TextInput
                  editable={canEditApplication}
                  onChangeText={(value) =>
                    setForm((current) => ({ ...current, [key]: value }))
                  }
                  placeholder={placeholder}
                  placeholderTextColor={colors.sage}
                  style={styles.input}
                  value={form[key as keyof FormState]}
                />
              </View>
            ))}
            <Button
              disabled={!canEditApplication}
              label="儲存入社資料"
              loading={save.isPending}
              onPress={() => save.mutate()}
              variant="secondary"
            />

            <View style={styles.warning}>
              <Ionicons color={colors.danger} name="warning-outline" size={22} />
              <View style={styles.warningCopy}>
                <Text style={styles.warningTitle}>
                  Sandbox 禁止上傳真實證件
                </Text>
                <Text style={styles.warningText}>
                  按下加入後會建立標有 Sandbox 的合成 PDF，不會開啟相簿或讀取手機檔案。
                </Text>
              </View>
            </View>
            <Text style={styles.sectionTitle}>測試證件</Text>
            {(Object.keys(documentLabels) as (keyof typeof documentLabels)[]).map(
              (documentType) => {
                const done = confirmed.includes(documentType);
                const documentId = application.data?.documents?.find(
                  (document) =>
                    document.document_type === documentType &&
                    document.status === "confirmed",
                )?.id;
                return (
                  <View
                    key={documentType}
                    style={[
                      styles.documentRow,
                      done && styles.documentDone,
                    ]}
                  >
                    <Ionicons
                      color={done ? colors.success : colors.forest}
                      name={done ? "checkmark-circle" : "document-attach-outline"}
                      size={22}
                    />
                    <Text style={styles.documentLabel}>
                      {documentLabels[documentType]}
                    </Text>
                    <View style={styles.documentActions}>
                      <Button
                        compact
                        disabled={!canEditApplication}
                        label={done ? "替換" : "加入測試檔"}
                        loading={addDocument.isPending}
                        onPress={() => addDocument.mutate(documentType)}
                        variant="secondary"
                      />
                      {done && documentId ? (
                        <Button
                          compact
                          disabled={!canEditApplication}
                          label="移除"
                          loading={removeDocument.isPending}
                          onPress={() => removeDocument.mutate(documentId)}
                          variant="danger"
                        />
                      ) : null}
                    </View>
                  </View>
                );
              },
            )}
            <Button
              disabled={!canSubmit}
              label="送出入社申請"
              loading={submit.isPending}
              onPress={() => submit.mutate()}
            />

            {(charges.data?.length ?? 0) > 0 &&
            ["submitted", "needs_revision", "approved"].includes(
              application.data?.status ?? "",
            ) ? (
              <>
                <Text style={styles.sectionTitle}>入社款項</Text>
                <InlineMessage text="資料審核可與付款後續並行；入社費與股金都付清後，先取得實習社員資格與社員價。" />
                {!canPayMembershipCharges ? (
                  <InlineMessage
                    text="目前會籍狀態不可付款，請洽合作社確認。"
                    tone="danger"
                  />
                ) : null}
                {(charges.data ?? []).map((charge) => (
                  <View key={charge.id} style={styles.chargeRow}>
                    <View>
                      <Text style={styles.chargeTitle}>
                        {charge.charge_type === "joining_fee"
                          ? "入社費"
                          : "股金"}
                      </Text>
                      <Text style={styles.chargeMeta}>
                        僅產生系統收據，不開電子發票
                      </Text>
                    </View>
                    <View style={styles.chargeEnd}>
                      <Text style={styles.chargeAmount}>{money(charge.amount)}</Text>
                      {charge.payment_status === "pending" ? (
                        <Button
                          compact
                          disabled={!canPayMembershipCharges}
                          label="測試付款"
                          loading={pay.isPending}
                          onPress={() => pay.mutate(charge.id)}
                        />
                      ) : (
                        <StatusPill
                          label={paymentLabels[charge.payment_status]}
                          tone={
                            charge.payment_status === "paid"
                              ? "positive"
                              : charge.payment_status === "refunded"
                                ? "neutral"
                                : "warning"
                          }
                        />
                      )}
                    </View>
                  </View>
                ))}
              </>
            ) : null}
          </>
        )}
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  content: { gap: 12, padding: spacing.md },
  syncError: { alignItems: "flex-start", gap: 8 },
  progressCard: {
    alignItems: "center",
    backgroundColor: colors.forest,
    borderRadius: radii.lg,
    flexDirection: "row",
    justifyContent: "space-between",
    padding: spacing.md,
  },
  progressLabel: { color: "#C9D8D0", fontSize: 12 },
  customerNumber: {
    color: colors.white,
    fontSize: 15,
    fontWeight: "900",
    marginBottom: 8,
    marginTop: 2,
  },
  progressTitle: {
    color: colors.white,
    fontSize: 21,
    fontWeight: "900",
    marginTop: 3,
  },
  sectionTitle: {
    color: colors.forest,
    fontSize: 19,
    fontWeight: "900",
    marginTop: 10,
  },
  field: { gap: 6 },
  fieldLabel: { color: colors.forest, fontSize: 12, fontWeight: "800" },
  input: {
    backgroundColor: colors.paper,
    borderColor: colors.line,
    borderRadius: radii.md,
    borderWidth: 1,
    color: colors.charcoal,
    fontSize: 14,
    minHeight: 50,
    paddingHorizontal: 14,
  },
  textarea: { minHeight: 100, paddingTop: 14 },
  formCard: {
    backgroundColor: colors.paper,
    borderColor: colors.line,
    borderRadius: radii.lg,
    borderWidth: 1,
    gap: 12,
    padding: spacing.md,
  },
  switchRow: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    minHeight: 52,
  },
  switchCopy: { flex: 1, paddingRight: 12 },
  helperText: { color: colors.muted, fontSize: 12, lineHeight: 18, marginTop: 3 },
  documentActions: { flexDirection: "row", gap: 6 },
  warning: {
    alignItems: "flex-start",
    backgroundColor: colors.dangerSoft,
    borderColor: "#E4B7AA",
    borderRadius: radii.md,
    borderWidth: 1,
    flexDirection: "row",
    gap: 10,
    marginTop: 8,
    padding: 13,
  },
  warningCopy: { flex: 1 },
  warningTitle: { color: colors.danger, fontSize: 14, fontWeight: "900" },
  warningText: {
    color: colors.charcoal,
    fontSize: 12,
    lineHeight: 18,
    marginTop: 4,
  },
  documentRow: {
    alignItems: "center",
    backgroundColor: colors.paper,
    borderColor: colors.line,
    borderRadius: radii.md,
    borderWidth: 1,
    flexDirection: "row",
    gap: 10,
    minHeight: 54,
    paddingHorizontal: 13,
  },
  documentDone: { backgroundColor: colors.successSoft },
  documentLabel: { color: colors.forest, flex: 1, fontSize: 13, fontWeight: "800" },
  memberCard: {
    backgroundColor: colors.forest,
    borderRadius: radii.lg,
    gap: 12,
    padding: spacing.md,
  },
  memberIdentity: { alignItems: "center", flexDirection: "row" },
  avatar: {
    alignItems: "center",
    backgroundColor: colors.sageLight,
    borderRadius: 17,
    height: 52,
    justifyContent: "center",
    width: 52,
  },
  avatarText: { color: colors.forest, fontSize: 20, fontWeight: "900" },
  memberCopy: { marginLeft: 11 },
  memberName: { color: colors.white, fontSize: 18, fontWeight: "900" },
  memberNumberLabel: { color: "#C9D8D0", fontSize: 11, marginTop: 4 },
  memberNumber: { color: "#C9D8D0", fontSize: 12, marginTop: 4 },
  memberBio: { color: "#E0E7E2", fontSize: 13, lineHeight: 20 },
  visibility: { color: "#C9D8D0", fontSize: 12 },
  chargeRow: {
    alignItems: "center",
    backgroundColor: colors.paper,
    borderRadius: radii.md,
    flexDirection: "row",
    justifyContent: "space-between",
    minHeight: 76,
    padding: 13,
  },
  chargeTitle: { color: colors.forest, fontSize: 14, fontWeight: "900" },
  chargeMeta: { color: colors.muted, fontSize: 12, marginTop: 4 },
  chargeEnd: { alignItems: "flex-end", gap: 5 },
  chargeAmount: { color: colors.orange, fontSize: 18, fontWeight: "900" },
  directoryGrid: { flexDirection: "row", flexWrap: "wrap", gap: 12 },
  directoryCard: {
    backgroundColor: colors.paper,
    borderRadius: radii.lg,
    minHeight: 180,
    padding: 13,
    width: "48.3%",
  },
  smallAvatar: {
    alignItems: "center",
    backgroundColor: colors.sageLight,
    borderRadius: 14,
    height: 44,
    justifyContent: "center",
    width: 44,
  },
  smallAvatarText: { color: colors.forest, fontSize: 17, fontWeight: "900" },
  directoryName: {
    color: colors.forest,
    fontSize: 15,
    fontWeight: "900",
    marginTop: 10,
  },
  directoryExpertise: { color: colors.orange, fontSize: 12, marginTop: 3 },
  directoryBio: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 18,
    marginTop: 7,
  },
});
