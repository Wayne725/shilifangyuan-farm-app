import { Ionicons } from "@expo/vector-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { router } from "expo-router";
import { useState } from "react";
import { Image, StyleSheet, Text, TextInput, View } from "react-native";

import {
  Button,
  EmptyState,
  InlineMessage,
  LoadingState,
  PageHeader,
  ProgressBar,
  Screen,
  StatusPill,
} from "../../src/components/ui";
import { dateTime } from "../../src/lib/format";
import { imageFor } from "../../src/lib/images";
import { api, getErrorMessage } from "../../src/services/api";
import { useAuth } from "../../src/store/AuthContext";
import { colors, radii, shadows, spacing } from "../../src/theme";

export default function ActivitiesScreen() {
  const { isAuthenticated } = useAuth();
  const queryClient = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [venue, setVenue] = useState("");
  const [startsAt, setStartsAt] = useState("2026-08-20T01:00:00.000Z");
  const [deadline, setDeadline] = useState("2026-08-16T15:59:00.000Z");
  const [capacity, setCapacity] = useState("16");
  const membership = useQuery({
    queryKey: ["membership"],
    queryFn: api.membership,
    enabled: isAuthenticated,
  });
  const isMember = membership.data?.status === "active";
  const query = useQuery({
    queryKey: ["activities"],
    queryFn: api.activities,
    enabled: isMember,
  });
  const registration = useMutation({
    mutationFn: ({
      id,
      cancel,
    }: {
      id: string;
      cancel: boolean;
    }) =>
      cancel
        ? api.cancelActivityRegistration(id)
        : api.registerActivity(id),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["activities"] }),
  });
  const create = useMutation({
    mutationFn: () =>
      api.createActivity({
        title: title.trim(),
        description: description.trim(),
        venue_name: venue.trim(),
        starts_at: startsAt.trim(),
        registration_deadline: deadline.trim(),
        capacity: Number(capacity),
      }),
    onSuccess: async () => {
      setShowForm(false);
      setTitle("");
      setDescription("");
      setVenue("");
      await queryClient.invalidateQueries({ queryKey: ["activities"] });
    },
  });

  if (!isAuthenticated) {
    return (
      <Screen>
        <PageHeader title="社員活動" />
        <EmptyState
          action="登入帳號"
          description="活動只對有效社員開放。"
          icon="lock-closed-outline"
          onAction={() => router.push("/login")}
          title="請先登入"
        />
      </Screen>
    );
  }
  if (membership.isLoading) return <LoadingState label="確認社員資格" />;
  if (!isMember) {
    return (
      <Screen>
        <PageHeader title="社員活動" />
        <EmptyState
          action="查看入社程序"
          description="會籍啟用後即可查看活動詳情與報名。"
          icon="id-card-outline"
          onAction={() => router.push("/(tabs)/members")}
          title="社員限定"
        />
      </Screen>
    );
  }

  return (
    <Screen>
      <PageHeader
        right={
          <Button
            compact
            icon={showForm ? "close" : "add"}
            label={showForm ? "收合" : "發起活動"}
            onPress={() => setShowForm((current) => !current)}
            variant="secondary"
          />
        }
        subtitle="由社員發起、社務小組審核；額滿後會依序加入候補。"
        title="社員活動"
      />
      {registration.error || create.error ? (
        <View style={styles.message}>
          <InlineMessage
            text={getErrorMessage(registration.error ?? create.error)}
            tone="danger"
          />
        </View>
      ) : null}
      {showForm ? (
        <View style={styles.createForm}>
          <Text style={styles.formTitle}>發起社員活動</Text>
          {[
            { label: "活動名稱", value: title, onChange: setTitle },
            { label: "活動說明", value: description, onChange: setDescription },
            { label: "集合地點", value: venue, onChange: setVenue },
            { label: "活動時間（ISO）", value: startsAt, onChange: setStartsAt },
            { label: "報名截止（ISO）", value: deadline, onChange: setDeadline },
            { label: "名額", value: capacity, onChange: setCapacity },
          ].map((field) => (
            <View key={field.label} style={styles.field}>
              <Text style={styles.fieldLabel}>{field.label}</Text>
              <TextInput
                onChangeText={field.onChange}
                placeholder={field.label}
                placeholderTextColor={colors.sage}
                style={styles.input}
                value={field.value}
              />
            </View>
          ))}
          <Button
            disabled={
              !title.trim() ||
              !description.trim() ||
              !venue.trim() ||
              !Number.isInteger(Number(capacity)) ||
              Number(capacity) < 1
            }
            label="送交活動審核"
            loading={create.isPending}
            onPress={() => create.mutate()}
          />
        </View>
      ) : null}
      {query.isLoading ? (
        <LoadingState label="載入社員活動" />
      ) : query.isError ? (
        <EmptyState
          action="重新載入"
          description="目前無法取得活動資料。"
          onAction={() => query.refetch()}
          title="活動載入失敗"
        />
      ) : query.data?.length ? (
        <View style={styles.list}>
          {query.data.map((activity) => {
            const full = activity.registered_count >= activity.capacity;
            const registered = ["registered", "waitlisted"].includes(
              activity.my_registration_status ?? "",
            );
            return (
              <View key={activity.id} style={styles.card}>
                <View style={styles.imageWrap}>
                  <Image
                    resizeMode="cover"
                    source={imageFor(activity.image_key, activity.image_url)}
                    style={styles.image}
                  />
                </View>
                <View style={styles.copy}>
                  <View style={styles.statusRow}>
                    <StatusPill
                      label={
                        activity.my_registration_status === "registered"
                          ? "已報名"
                          : activity.my_registration_status === "waitlisted"
                            ? "候補中"
                            : full
                              ? "候補開放"
                              : "開放報名"
                      }
                      tone={registered ? "positive" : full ? "warning" : "neutral"}
                    />
                    <Text style={styles.organizer}>
                      發起人 {activity.created_by_name}
                    </Text>
                  </View>
                  <Text style={styles.title}>{activity.title}</Text>
                  <Text style={styles.description}>{activity.description}</Text>
                  <View style={styles.metaRow}>
                    <Ionicons
                      color={colors.forest}
                      name="calendar-outline"
                      size={18}
                    />
                    <Text style={styles.meta}>{dateTime(activity.starts_at)}</Text>
                  </View>
                  <View style={styles.metaRow}>
                    <Ionicons
                      color={colors.forest}
                      name="location-outline"
                      size={18}
                    />
                    <Text style={styles.meta}>{activity.venue_name}</Text>
                  </View>
                  <View style={styles.capacityTop}>
                    <Text style={styles.capacityStrong}>
                      {activity.registered_count}/{activity.capacity} 人
                    </Text>
                    <Text style={styles.capacityMeta}>
                      候補 {activity.waitlist_count} 人
                    </Text>
                  </View>
                  <ProgressBar
                    value={
                      (activity.registered_count / activity.capacity) * 100
                    }
                  />
                  <View style={styles.action}>
                    <Button
                      label={registered ? "取消報名" : full ? "加入候補" : "報名活動"}
                      loading={registration.isPending}
                      onPress={() =>
                        registration.mutate({
                          id: activity.id,
                          cancel: registered,
                        })
                      }
                      variant={registered ? "quiet" : "primary"}
                    />
                  </View>
                </View>
              </View>
            );
          })}
        </View>
      ) : (
        <EmptyState
          description="新活動審核發布後會顯示在這裡。"
          icon="calendar-outline"
          title="目前沒有活動"
        />
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  message: { paddingHorizontal: spacing.md },
  createForm: {
    backgroundColor: colors.paper,
    borderRadius: radii.lg,
    gap: 10,
    margin: spacing.md,
    padding: spacing.md,
  },
  formTitle: { color: colors.forest, fontSize: 18, fontWeight: "900" },
  field: { gap: 6 },
  fieldLabel: { color: colors.forest, fontSize: 12, fontWeight: "800" },
  input: {
    backgroundColor: colors.cream,
    borderColor: colors.line,
    borderRadius: radii.md,
    borderWidth: 1,
    color: colors.charcoal,
    fontSize: 13,
    minHeight: 48,
    paddingHorizontal: 13,
  },
  list: { gap: 14, padding: spacing.md },
  card: {
    backgroundColor: colors.paper,
    borderRadius: radii.lg,
    overflow: "hidden",
    ...shadows.card,
  },
  imageWrap: {
    aspectRatio: 16 / 9,
    backgroundColor: colors.sageLight,
    overflow: "hidden",
    width: "100%",
  },
  image: { height: "100%", width: "100%" },
  copy: { padding: spacing.md },
  statusRow: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
  },
  organizer: { color: colors.muted, fontSize: 12 },
  title: {
    color: colors.forest,
    fontSize: 22,
    fontWeight: "900",
    marginTop: 12,
  },
  description: {
    color: colors.charcoal,
    fontSize: 13,
    lineHeight: 21,
    marginTop: 6,
  },
  metaRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 8,
    marginTop: 11,
  },
  meta: { color: colors.muted, flex: 1, fontSize: 12, lineHeight: 18 },
  capacityTop: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginBottom: 7,
    marginTop: 14,
  },
  capacityStrong: { color: colors.forest, fontSize: 12, fontWeight: "900" },
  capacityMeta: { color: colors.muted, fontSize: 12 },
  action: { marginTop: spacing.md },
});
