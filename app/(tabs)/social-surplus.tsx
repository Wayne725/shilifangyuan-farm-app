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
import { money } from "../../src/lib/format";
import { hasFormalMemberAccess } from "../../src/lib/membership";
import { api, getErrorMessage } from "../../src/services/api";
import { useAuth } from "../../src/store/AuthContext";
import { colors, radii, spacing } from "../../src/theme";

export default function SocialSurplusScreen() {
  const { isAuthenticated, user } = useAuth();
  const allowed = hasFormalMemberAccess(
    user?.membership_type ?? "nonmember",
  );
  const distributions = useQuery({
    queryKey: ["my-surplus"],
    queryFn: api.mySurplusDistributions,
    enabled: allowed,
  });

  if (!isAuthenticated || !user) {
    return (
      <Screen>
        <PageHeader onBack={() => router.back()} title="結餘分配" />
        <EmptyState
          action="登入帳號"
          description="登入正式社員帳號後查看分配紀錄。"
          onAction={() => router.push("/login")}
          title="請先登入"
        />
      </Screen>
    );
  }
  if (!allowed) {
    return (
      <Screen>
        <PageHeader onBack={() => router.back()} title="結餘分配" />
        <EmptyState
          action="查看社員資料"
          description="結餘分配與消費貢獻度只對正式社員開放。"
          icon="lock-closed-outline"
          onAction={() => router.push("/(tabs)/members")}
          title="正式社員限定"
        />
      </Screen>
    );
  }
  if (distributions.isLoading) return <LoadingState label="載入結餘分配" />;

  return (
    <Screen>
      <PageHeader
        onBack={() => router.back()}
        subtitle="年度結餘確認後，依正式社員的消費貢獻度顯示結果。"
        title="結餘分配"
      />
      <View style={styles.content}>
        {distributions.error ? (
          <InlineMessage
            text={getErrorMessage(distributions.error)}
            tone="danger"
          />
        ) : null}
        {(distributions.data ?? []).map((item) => (
          <View key={item.fiscal_year_id} style={styles.card}>
            <Text style={styles.title}>{item.label}</Text>
            <View style={styles.metricRow}>
              <View style={styles.metric}>
                <Text style={styles.label}>消費貢獻</Text>
                <Text style={styles.value}>{money(item.contribution_amount)}</Text>
              </View>
              <View style={styles.metric}>
                <Text style={styles.label}>分配金額</Text>
                <Text style={styles.value}>{money(item.distribution_amount)}</Text>
              </View>
            </View>
            <Text style={styles.meta}>
              確認日期 {new Date(item.confirmed_at).toLocaleDateString("zh-TW")}
            </Text>
          </View>
        ))}
        {!distributions.data?.length ? (
          <EmptyState
            description="目前尚無已確認的年度分配。"
            title="尚無分配紀錄"
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
    gap: 13,
    padding: spacing.md,
  },
  title: { color: colors.forest, fontSize: 18, fontWeight: "900" },
  metricRow: { flexDirection: "row", gap: 9 },
  metric: {
    backgroundColor: colors.cream,
    borderRadius: radii.md,
    flex: 1,
    gap: 5,
    padding: 12,
  },
  label: { color: colors.muted, fontSize: 11 },
  value: { color: colors.charcoal, fontSize: 16, fontWeight: "900" },
  meta: { color: colors.muted, fontSize: 12 },
});
