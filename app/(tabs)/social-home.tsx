import { Ionicons } from "@expo/vector-icons";
import { useQuery } from "@tanstack/react-query";
import { router } from "expo-router";
import { Pressable, StyleSheet, Text, View } from "react-native";

import {
  Button,
  EmptyState,
  LoadingState,
  PageHeader,
  Screen,
  SectionHeader,
  StatusPill,
} from "../../src/components/ui";
import { dateTime } from "../../src/lib/format";
import { api } from "../../src/services/api";
import { useAuth } from "../../src/store/AuthContext";
import { colors, radii, spacing } from "../../src/theme";

export default function SocialHomeScreen() {
  const { isAuthenticated } = useAuth();
  const membership = useQuery({
    queryKey: ["membership"],
    queryFn: api.membership,
    enabled: isAuthenticated,
  });
  const isMember = membership.data?.status === "active";
  const activities = useQuery({
    queryKey: ["activities"],
    queryFn: api.activities,
    enabled: isMember,
  });
  const proposals = useQuery({
    queryKey: ["member-proposals"],
    queryFn: api.memberProposals,
    enabled: isMember,
  });

  return (
    <Screen>
      <PageHeader
        subtitle="入社、社員活動與合作社提案，都集中在這個工作區。"
        title="社務首頁"
      />
      {!isAuthenticated ? (
        <View style={styles.welcome}>
          <Ionicons color={colors.white} name="people-outline" size={30} />
          <Text style={styles.welcomeTitle}>一起參與合作社的日常</Text>
          <Text style={styles.welcomeText}>
            登入後可提出入社申請；有效社員可查看活動、名錄與治理提案。
          </Text>
          <Button label="登入帳號" onPress={() => router.push("/login")} />
        </View>
      ) : membership.isLoading ? (
        <LoadingState label="確認社員資格" />
      ) : !isMember ? (
        <View style={styles.content}>
          <View style={styles.applicationCard}>
            <View style={styles.iconBox}>
              <Ionicons color={colors.forest} name="id-card-outline" size={25} />
            </View>
            <View style={styles.applicationCopy}>
              <Text style={styles.applicationTitle}>開始入社程序</Text>
              <Text style={styles.applicationText}>
                填寫基本資料、加入測試證件，再送交社務小組審核。
              </Text>
            </View>
          </View>
          <Button
            icon="arrow-forward"
            label="查看入社進度"
            onPress={() => router.push("/(tabs)/members")}
          />
          <View style={styles.memberOnly}>
            <Ionicons color={colors.moss} name="lock-closed-outline" size={20} />
            <Text style={styles.memberOnlyText}>
              活動、社員名錄與治理提案只對有效社員開放。
            </Text>
          </View>
        </View>
      ) : (
        <View style={styles.content}>
          <View style={styles.membershipCard}>
            <View>
              <Text style={styles.membershipLabel}>社員編號</Text>
              <Text style={styles.membershipNumber}>
                {membership.data?.member_number}
              </Text>
            </View>
            <StatusPill label="會籍有效" tone="positive" />
          </View>
          <Button
            icon="leaf-outline"
            label="合作教育、積點、願望與會議"
            onPress={() => router.push("/cooperative" as never)}
            variant="secondary"
          />

          <SectionHeader
            action="全部活動"
            onAction={() => router.push("/(tabs)/activities")}
            title="近期活動"
          />
          {(activities.data ?? []).slice(0, 2).map((activity) => (
            <Pressable
              key={activity.id}
              onPress={() => router.push("/(tabs)/activities")}
              style={({ pressed }) => [
                styles.rowCard,
                pressed && styles.pressed,
              ]}
            >
              <View style={styles.rowIcon}>
                <Ionicons color={colors.forest} name="calendar-outline" size={20} />
              </View>
              <View style={styles.rowCopy}>
                <Text style={styles.rowTitle}>{activity.title}</Text>
                <Text style={styles.rowMeta}>
                  {dateTime(activity.starts_at)}　{activity.venue_name}
                </Text>
              </View>
              <Ionicons color={colors.sage} name="chevron-forward" size={18} />
            </Pressable>
          ))}

          <SectionHeader
            action="查看提案"
            onAction={() => router.push("/(tabs)/member-proposals")}
            title="社員共議"
          />
          {(proposals.data ?? []).slice(0, 2).map((proposal) => (
            <Pressable
              key={proposal.id}
              onPress={() => router.push("/(tabs)/member-proposals")}
              style={({ pressed }) => [
                styles.rowCard,
                pressed && styles.pressed,
              ]}
            >
              <View style={styles.rowIcon}>
                <Ionicons
                  color={colors.forest}
                  name="chatbubbles-outline"
                  size={20}
                />
              </View>
              <View style={styles.rowCopy}>
                <Text style={styles.rowTitle}>{proposal.title}</Text>
                <Text style={styles.rowMeta}>
                  {proposal.status === "voting" ? "表決中" : "討論中"}
                </Text>
              </View>
              <Ionicons color={colors.sage} name="chevron-forward" size={18} />
            </Pressable>
          ))}
          {!activities.data?.length && !proposals.data?.length ? (
            <EmptyState
              description="有新的社員活動或提案時會顯示在這裡。"
              title="目前沒有社務消息"
            />
          ) : null}
        </View>
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  welcome: {
    backgroundColor: colors.forest,
    borderRadius: radii.lg,
    gap: 14,
    margin: spacing.md,
    padding: spacing.lg,
  },
  welcomeTitle: {
    color: colors.white,
    fontSize: 24,
    fontWeight: "900",
    marginTop: 8,
  },
  welcomeText: { color: "#D8E2DC", fontSize: 13, lineHeight: 21 },
  content: { gap: 12, padding: spacing.md },
  applicationCard: {
    alignItems: "center",
    backgroundColor: colors.paper,
    borderRadius: radii.lg,
    flexDirection: "row",
    gap: 13,
    padding: spacing.md,
  },
  iconBox: {
    alignItems: "center",
    backgroundColor: colors.sageLight,
    borderRadius: 14,
    height: 50,
    justifyContent: "center",
    width: 50,
  },
  applicationCopy: { flex: 1 },
  applicationTitle: { color: colors.forest, fontSize: 17, fontWeight: "900" },
  applicationText: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 18,
    marginTop: 4,
  },
  memberOnly: {
    alignItems: "center",
    backgroundColor: colors.creamDeep,
    borderRadius: radii.md,
    flexDirection: "row",
    gap: 9,
    padding: 13,
  },
  memberOnlyText: { color: colors.muted, flex: 1, fontSize: 12, lineHeight: 18 },
  membershipCard: {
    alignItems: "center",
    backgroundColor: colors.forest,
    borderRadius: radii.lg,
    flexDirection: "row",
    justifyContent: "space-between",
    marginBottom: 8,
    padding: spacing.md,
  },
  membershipLabel: { color: "#C9D8D0", fontSize: 12 },
  membershipNumber: {
    color: colors.white,
    fontSize: 21,
    fontWeight: "900",
    marginTop: 4,
  },
  rowCard: {
    alignItems: "center",
    backgroundColor: colors.paper,
    borderRadius: radii.md,
    flexDirection: "row",
    gap: 11,
    minHeight: 72,
    padding: 12,
  },
  rowIcon: {
    alignItems: "center",
    backgroundColor: colors.sageLight,
    borderRadius: 12,
    height: 42,
    justifyContent: "center",
    width: 42,
  },
  rowCopy: { flex: 1 },
  rowTitle: { color: colors.forest, fontSize: 14, fontWeight: "900" },
  rowMeta: { color: colors.muted, fontSize: 12, marginTop: 4 },
  pressed: { opacity: 0.76, transform: [{ scale: 0.99 }] },
});
