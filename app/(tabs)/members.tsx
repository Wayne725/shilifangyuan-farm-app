import { Ionicons } from "@expo/vector-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { router } from "expo-router";
import { useEffect, useState } from "react";
import {
  Pressable,
  StyleSheet,
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
import { money } from "../../src/lib/format";
import { api, getErrorMessage } from "../../src/services/api";
import { useAuth } from "../../src/store/AuthContext";
import { colors, radii, spacing } from "../../src/theme";
import type { MembershipApplication } from "../../src/types";

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

export default function MembersScreen() {
  const { isAuthenticated } = useAuth();
  const queryClient = useQueryClient();
  const [form, setForm] = useState<FormState>(emptyForm);
  const [message, setMessage] = useState("");
  const membership = useQuery({
    queryKey: ["membership"],
    queryFn: api.membership,
    enabled: isAuthenticated,
  });
  const application = useQuery({
    queryKey: ["membership-application"],
    queryFn: api.membershipApplication,
    enabled: isAuthenticated,
  });
  const charges = useQuery({
    queryKey: ["membership-charges"],
    queryFn: api.membershipCharges,
    enabled: isAuthenticated,
  });
  const directory = useQuery({
    queryKey: ["member-directory"],
    queryFn: api.memberDirectory,
    enabled: membership.data?.status === "active",
  });

  useEffect(() => {
    if (!application.data) return;
    setForm({
      legal_name: application.data.legal_name,
      phone: application.data.phone,
      birth_date: application.data.birth_date,
      address: application.data.address,
      emergency_contact_name: application.data.emergency_contact_name,
      emergency_contact_phone: application.data.emergency_contact_phone,
    });
  }, [application.data]);

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
      const upload = await api.membershipDocumentUploadUrl({
        document_type,
        content_type: "image/png",
        file_size: 1024,
        checksum: `sandbox-${document_type}`,
      });
      return api.confirmMembershipDocument({
        document_type,
        object_key: upload.object_key,
        checksum: `sandbox-${document_type}`,
      });
    },
    onSuccess: async () => {
      setMessage("測試證件已加入");
      await refresh();
    },
  });
  const submit = useMutation({
    mutationFn: api.submitMembershipApplication,
    onSuccess: async () => {
      setMessage("入社申請已送出");
      await refresh();
    },
  });
  const pay = useMutation({
    mutationFn: api.payMembershipCharge,
    onSuccess: async () => {
      setMessage("款項已完成，系統收據已建立");
      await refresh();
    },
  });
  const error =
    save.error ?? addDocument.error ?? submit.error ?? pay.error;

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

  const isMember = membership.data?.status === "active";
  const confirmed = application.data?.confirmed_documents ?? [];
  const canSubmit =
    Object.values(form).every((value) => value.trim()) &&
    confirmed.length === 3 &&
    !["submitted", "approved"].includes(application.data?.status ?? "");

  return (
    <Screen>
      <PageHeader
        subtitle={
          isMember
            ? "管理會籍資訊，並認識自願公開資料的社員。"
            : "完成資料、測試證件與審核後，再繳交入社費及股金。"
        }
        title={isMember ? "社員服務" : "入社申請"}
      />
      <View style={styles.content}>
        {message ? <InlineMessage text={message} tone="positive" /> : null}
        {error ? (
          <InlineMessage text={getErrorMessage(error)} tone="danger" />
        ) : null}

        {isMember ? (
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
                  <Text style={styles.memberNumber}>
                    {membership.data?.member_number}
                  </Text>
                </View>
              </View>
              <StatusPill label="有效會籍" tone="positive" />
              <Text style={styles.memberBio}>{membership.data?.bio}</Text>
              <Text style={styles.visibility}>
                社員名錄：
                {membership.data?.directory_visible ? "自願公開" : "不公開"}
              </Text>
            </View>

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
                      label="測試付款"
                      loading={pay.isPending}
                      onPress={() => pay.mutate(charge.id)}
                      variant="secondary"
                    />
                  ) : (
                    <StatusPill label="已繳" tone="positive" />
                  )}
                </View>
              </View>
            ))}

            <Text style={styles.sectionTitle}>社員名錄</Text>
            {directory.isLoading ? (
              <LoadingState label="載入社員名錄" />
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
        ) : (
          <>
            <View style={styles.progressCard}>
              <View>
                <Text style={styles.progressLabel}>目前進度</Text>
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
                  展示時只加入系統產生的測試檔，不會讀取手機檔案。
                </Text>
              </View>
            </View>
            <Text style={styles.sectionTitle}>測試證件</Text>
            {(Object.keys(documentLabels) as (keyof typeof documentLabels)[]).map(
              (documentType) => {
                const done = confirmed.includes(documentType);
                return (
                  <Pressable
                    disabled={done || addDocument.isPending}
                    key={documentType}
                    onPress={() => addDocument.mutate(documentType)}
                    style={({ pressed }) => [
                      styles.documentRow,
                      done && styles.documentDone,
                      pressed && styles.pressed,
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
                    <Text style={styles.documentAction}>
                      {done ? "已加入" : "加入測試檔"}
                    </Text>
                  </Pressable>
                );
              },
            )}
            <Button
              disabled={!canSubmit}
              label="送出入社申請"
              loading={submit.isPending}
              onPress={() => submit.mutate()}
            />

            {application.data?.status === "approved" ? (
              <>
                <Text style={styles.sectionTitle}>入社款項</Text>
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
                          label="測試付款"
                          onPress={() => pay.mutate(charge.id)}
                        />
                      ) : (
                        <StatusPill label="已繳" tone="positive" />
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
  progressCard: {
    alignItems: "center",
    backgroundColor: colors.forest,
    borderRadius: radii.lg,
    flexDirection: "row",
    justifyContent: "space-between",
    padding: spacing.md,
  },
  progressLabel: { color: "#C9D8D0", fontSize: 12 },
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
  documentAction: { color: colors.orange, fontSize: 12, fontWeight: "900" },
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
  pressed: { opacity: 0.72, transform: [{ scale: 0.99 }] },
});
