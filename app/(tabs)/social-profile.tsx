import { Ionicons } from "@expo/vector-icons";
import { useQuery } from "@tanstack/react-query";
import { router } from "expo-router";
import { Pressable, StyleSheet, Text, View } from "react-native";

import {
  EmptyState,
  LoadingState,
  PageHeader,
  Screen,
  StatusPill,
} from "../../src/components/ui";
import {
  hasFormalMemberAccess,
  membershipIdentity,
} from "../../src/lib/membership";
import { api } from "../../src/services/api";
import { useAuth } from "../../src/store/AuthContext";
import { colors, radii, spacing } from "../../src/theme";

type MenuItem = {
  title: string;
  detail: string;
  icon: keyof typeof Ionicons.glyphMap;
  route: string;
  formalOnly?: boolean;
};

const menuItems: MenuItem[] = [
  {
    title: "個人資料",
    detail: "帳號、通知、訂單與使用說明",
    icon: "person-circle-outline",
    route: "/(tabs)/social-account",
  },
  {
    title: "積點與徽章",
    detail: "查看合作參與紀錄",
    icon: "ribbon-outline",
    route: "/(tabs)/social-points",
    formalOnly: true,
  },
  {
    title: "願望",
    detail: "提出想引進的商品並集氣",
    icon: "sparkles-outline",
    route: "/(tabs)/social-wishes",
    formalOnly: true,
  },
  {
    title: "會議",
    detail: "查看社員大會與社務會議",
    icon: "people-circle-outline",
    route: "/(tabs)/social-meetings",
    formalOnly: true,
  },
  {
    title: "結餘分配",
    detail: "查看年度消費貢獻與分配紀錄",
    icon: "pie-chart-outline",
    route: "/(tabs)/social-surplus",
    formalOnly: true,
  },
];

export default function SocialProfileScreen() {
  const { isAuthenticated, user } = useAuth();
  const membership = useQuery({
    queryKey: ["membership"],
    queryFn: api.membership,
    enabled: isAuthenticated,
  });

  if (!isAuthenticated || !user) {
    return (
      <Screen>
        <PageHeader title="更多" />
        <EmptyState
          action="登入帳號"
          description="登入後可查看個人資料與社員服務。"
          icon="person-circle-outline"
          onAction={() => router.push("/login")}
          title="請先登入"
        />
      </Screen>
    );
  }
  if (membership.isLoading) return <LoadingState label="載入身分資料" />;

  const formalMember = hasFormalMemberAccess(user.membership_type);
  const identity = membershipIdentity(user, membership.data ?? null);

  return (
    <Screen>
      <PageHeader
        subtitle="個人帳號與社員服務分開整理，需要時再進入。"
        title="更多"
      />
      <View style={styles.content}>
        <View style={styles.identityCard}>
          <View>
            <Text style={styles.identityLabel}>{identity.label}</Text>
            <Text style={styles.identityNumber}>{identity.number}</Text>
          </View>
          <StatusPill
            label={
              user.membership_type === "member"
                ? "正式社員"
                : user.membership_type === "trainee"
                  ? "實習社員・社員價"
                  : "一般買家"
            }
            tone={formalMember ? "positive" : "neutral"}
          />
        </View>

        <Pressable
          onPress={() => router.push("/(tabs)/members")}
          style={({ pressed }) => [styles.membershipRow, pressed && styles.pressed]}
        >
          <Ionicons color={colors.forest} name="id-card-outline" size={22} />
          <View style={styles.menuCopy}>
            <Text style={styles.menuTitle}>社員與入社資料</Text>
            <Text style={styles.menuDetail}>查看申請、款項與目前資格</Text>
          </View>
          <Ionicons color={colors.sage} name="chevron-forward" size={18} />
        </Pressable>

        <View style={styles.menu}>
          {menuItems.map((item, index) => {
            const locked = Boolean(item.formalOnly && !formalMember);
            return (
              <View key={item.title}>
                {index ? <View style={styles.rule} /> : null}
                <Pressable
                  accessibilityState={{ disabled: locked }}
                  disabled={locked}
                  onPress={() => router.push(item.route as never)}
                  style={({ pressed }) => [
                    styles.menuRow,
                    locked && styles.locked,
                    pressed && styles.pressed,
                  ]}
                >
                  <View style={styles.menuIcon}>
                    <Ionicons color={colors.forest} name={item.icon} size={21} />
                  </View>
                  <View style={styles.menuCopy}>
                    <Text style={styles.menuTitle}>{item.title}</Text>
                    <Text style={styles.menuDetail}>
                      {locked ? "正式社員限定" : item.detail}
                    </Text>
                  </View>
                  <Ionicons
                    color={colors.sage}
                    name={locked ? "lock-closed-outline" : "chevron-forward"}
                    size={18}
                  />
                </Pressable>
              </View>
            );
          })}
        </View>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  content: { gap: 12, padding: spacing.md },
  identityCard: {
    alignItems: "center",
    backgroundColor: colors.forest,
    borderRadius: radii.lg,
    flexDirection: "row",
    justifyContent: "space-between",
    padding: spacing.md,
  },
  identityLabel: { color: "#C9D8D0", fontSize: 12 },
  identityNumber: {
    color: colors.white,
    fontSize: 19,
    fontWeight: "900",
    marginTop: 4,
  },
  membershipRow: {
    alignItems: "center",
    backgroundColor: colors.sageLight,
    borderRadius: radii.md,
    flexDirection: "row",
    gap: 11,
    minHeight: 68,
    padding: 12,
  },
  menu: {
    backgroundColor: colors.paper,
    borderColor: colors.line,
    borderRadius: radii.lg,
    borderWidth: 1,
    overflow: "hidden",
  },
  menuRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 11,
    minHeight: 72,
    paddingHorizontal: 13,
  },
  menuIcon: {
    alignItems: "center",
    backgroundColor: colors.sageLight,
    borderRadius: 11,
    height: 40,
    justifyContent: "center",
    width: 40,
  },
  menuCopy: { flex: 1 },
  menuTitle: { color: colors.forest, fontSize: 14, fontWeight: "900" },
  menuDetail: { color: colors.muted, fontSize: 12, marginTop: 4 },
  rule: { backgroundColor: colors.line, height: 1, marginLeft: 64 },
  locked: { opacity: 0.5 },
  pressed: { opacity: 0.72 },
});
