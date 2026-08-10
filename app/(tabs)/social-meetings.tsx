import { useQuery } from "@tanstack/react-query";
import { router } from "expo-router";
import { StyleSheet, Text, View } from "react-native";

import {
  EmptyState,
  InlineMessage,
  LoadingState,
  PageHeader,
  ProgressBar,
  Screen,
} from "../../src/components/ui";
import { dateTime } from "../../src/lib/format";
import { hasFormalMemberAccess } from "../../src/lib/membership";
import { api, getErrorMessage } from "../../src/services/api";
import { useAuth } from "../../src/store/AuthContext";
import { colors, radii, spacing } from "../../src/theme";

export default function SocialMeetingsScreen() {
  const { isAuthenticated, user } = useAuth();
  const allowed = hasFormalMemberAccess(
    user?.membership_type ?? "nonmember",
  );
  const meetings = useQuery({
    queryKey: ["meetings"],
    queryFn: api.meetings,
    enabled: allowed,
  });

  if (!isAuthenticated || !user) {
    return (
      <Screen>
        <PageHeader onBack={() => router.back()} title="會議" />
        <EmptyState
          action="登入帳號"
          description="登入正式社員帳號後查看會議。"
          onAction={() => router.push("/login")}
          title="請先登入"
        />
      </Screen>
    );
  }
  if (!allowed) {
    return (
      <Screen>
        <PageHeader onBack={() => router.back()} title="會議" />
        <EmptyState
          action="查看社員資料"
          description="轉為正式社員後才可查看社員大會與社務會議。"
          icon="lock-closed-outline"
          onAction={() => router.push("/(tabs)/members")}
          title="正式社員限定"
        />
      </Screen>
    );
  }
  if (meetings.isLoading) return <LoadingState label="載入會議" />;

  return (
    <Screen>
      <PageHeader
        onBack={() => router.back()}
        subtitle="社員大會、社務會議與出席狀況集中顯示。"
        title="會議"
      />
      <View style={styles.content}>
        {meetings.error ? (
          <InlineMessage
            text={getErrorMessage(meetings.error)}
            tone="danger"
          />
        ) : null}
        {(meetings.data ?? []).map((meeting) => (
          <View key={meeting.id} style={styles.card}>
            <Text style={styles.type}>{meeting.meeting_type}</Text>
            <Text style={styles.title}>{meeting.title}</Text>
            <Text style={styles.meta}>
              {dateTime(meeting.starts_at)}・{meeting.location}
            </Text>
            <ProgressBar value={meeting.attendance_rate * 100} />
            <Text style={styles.meta}>
              {meeting.attended_count}/{meeting.eligible_member_count} 人出席・
              {(meeting.attendance_rate * 100).toFixed(1)}%
            </Text>
          </View>
        ))}
        {!meetings.data?.length ? (
          <EmptyState
            description="管理員建立會議後會顯示在這裡。"
            title="目前沒有會議"
          />
        ) : null}
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  content: { gap: 12, padding: spacing.md },
  card: {
    backgroundColor: colors.paper,
    borderRadius: radii.lg,
    gap: 8,
    padding: spacing.md,
  },
  type: { color: colors.moss, fontSize: 11, fontWeight: "900" },
  title: { color: colors.forest, fontSize: 17, fontWeight: "900" },
  meta: { color: colors.muted, fontSize: 12, lineHeight: 18 },
});
