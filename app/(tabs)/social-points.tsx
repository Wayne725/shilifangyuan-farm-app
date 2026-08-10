import { useQuery } from "@tanstack/react-query";
import { router } from "expo-router";
import { StyleSheet, Text, View } from "react-native";

import {
  EmptyState,
  InlineMessage,
  LoadingState,
  PageHeader,
  Screen,
} from "../../src/components/ui";
import { dateTime } from "../../src/lib/format";
import { hasFormalMemberAccess } from "../../src/lib/membership";
import { api, getErrorMessage } from "../../src/services/api";
import { useAuth } from "../../src/store/AuthContext";
import { colors, radii, spacing } from "../../src/theme";

export default function SocialPointsScreen() {
  const { isAuthenticated, user } = useAuth();
  const allowed = hasFormalMemberAccess(
    user?.membership_type ?? "nonmember",
  );
  const points = useQuery({
    queryKey: ["my-points"],
    queryFn: api.myPoints,
    enabled: allowed,
  });
  const badges = useQuery({
    queryKey: ["my-badges"],
    queryFn: api.myBadges,
    enabled: allowed,
  });

  if (!isAuthenticated || !user) {
    return (
      <Screen>
        <PageHeader onBack={() => router.back()} title="積點與徽章" />
        <EmptyState
          action="登入帳號"
          description="登入正式社員帳號後查看合作參與紀錄。"
          onAction={() => router.push("/login")}
          title="請先登入"
        />
      </Screen>
    );
  }
  if (!allowed) {
    return (
      <Screen>
        <PageHeader onBack={() => router.back()} title="積點與徽章" />
        <EmptyState
          action="查看社員資料"
          description="實習社員轉為正式社員後，才會開始使用積點與徽章服務。"
          icon="lock-closed-outline"
          onAction={() => router.push("/(tabs)/members")}
          title="正式社員限定"
        />
      </Screen>
    );
  }
  if (points.isLoading || badges.isLoading) {
    return <LoadingState label="載入積點與徽章" />;
  }

  return (
    <Screen>
      <PageHeader
        onBack={() => router.back()}
        subtitle="積點是合作參與紀錄，不是現金或購物金。"
        title="積點與徽章"
      />
      <View style={styles.content}>
        {points.error || badges.error ? (
          <InlineMessage
            text={getErrorMessage(points.error ?? badges.error)}
            tone="danger"
          />
        ) : null}
        <View style={styles.metric}>
          <Text style={styles.metricLabel}>目前積點</Text>
          <Text style={styles.metricValue}>{points.data?.balance ?? 0}</Text>
        </View>
        <Text style={styles.heading}>我的徽章</Text>
        {(badges.data ?? []).map((badge) => (
          <View key={badge.key} style={styles.card}>
            <Text style={styles.title}>{badge.name}</Text>
            <Text style={styles.body}>{badge.description}</Text>
          </View>
        ))}
        {!badges.data?.length ? (
          <Text style={styles.muted}>參與活動或願望成案後，徽章會顯示在這裡。</Text>
        ) : null}
        <Text style={styles.heading}>積點紀錄</Text>
        {(points.data?.transactions ?? []).map((transaction) => (
          <View key={transaction.id} style={styles.transaction}>
            <View style={styles.transactionCopy}>
              <Text style={styles.title}>{transaction.note}</Text>
              <Text style={styles.muted}>{dateTime(transaction.created_at)}</Text>
            </View>
            <Text
              style={[
                styles.amount,
                transaction.amount < 0 && styles.amountNegative,
              ]}
            >
              {transaction.amount > 0 ? "+" : ""}
              {transaction.amount}
            </Text>
          </View>
        ))}
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  content: { gap: 12, padding: spacing.md },
  metric: {
    backgroundColor: colors.forest,
    borderRadius: radii.lg,
    padding: spacing.lg,
  },
  metricLabel: { color: "#C9D8D0", fontSize: 13 },
  metricValue: {
    color: colors.white,
    fontSize: 38,
    fontWeight: "900",
    marginTop: 5,
  },
  heading: { color: colors.forest, fontSize: 19, fontWeight: "900", marginTop: 5 },
  card: {
    backgroundColor: colors.paper,
    borderRadius: radii.md,
    gap: 5,
    padding: 13,
  },
  transaction: {
    alignItems: "center",
    backgroundColor: colors.paper,
    borderRadius: radii.md,
    flexDirection: "row",
    padding: 13,
  },
  transactionCopy: { flex: 1, gap: 4 },
  title: { color: colors.charcoal, fontSize: 14, fontWeight: "800" },
  body: { color: colors.muted, fontSize: 13, lineHeight: 19 },
  muted: { color: colors.muted, fontSize: 12 },
  amount: { color: colors.success, fontSize: 17, fontWeight: "900" },
  amountNegative: { color: colors.danger },
});
