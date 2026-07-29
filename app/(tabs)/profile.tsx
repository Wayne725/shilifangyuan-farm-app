import { Ionicons } from "@expo/vector-icons";
import { useQueryClient } from "@tanstack/react-query";
import { router } from "expo-router";
import { Pressable, StyleSheet, Text, View } from "react-native";

import {
  BrandLockup,
  Button,
  PageHeader,
  Screen,
  StatusPill,
} from "../../src/components/ui";
import { membershipLabel } from "../../src/lib/format";
import { useAuth } from "../../src/store/AuthContext";
import { useCart } from "../../src/store/CartContext";
import { colors, radii, spacing } from "../../src/theme";

function MenuRow({
  icon,
  title,
  detail,
  onPress,
}: {
  icon: keyof typeof Ionicons.glyphMap;
  title: string;
  detail: string;
  onPress: () => void;
}) {
  return (
    <Pressable onPress={onPress} style={styles.menuRow}>
      <View style={styles.menuIcon}>
        <Ionicons color={colors.forest} name={icon} size={20} />
      </View>
      <View style={styles.menuCopy}>
        <Text style={styles.menuTitle}>{title}</Text>
        <Text style={styles.menuDetail}>{detail}</Text>
      </View>
      <Ionicons color={colors.sage} name="chevron-forward" size={19} />
    </Pressable>
  );
}

export default function ProfileScreen() {
  const { user, isAuthenticated, isAdmin, logout } = useAuth();
  const { clear } = useCart();
  const queryClient = useQueryClient();

  if (!isAuthenticated || !user) {
    return (
      <Screen>
        <PageHeader eyebrow="ACCOUNT" title="我的" />
        <View style={styles.guestHero}>
          <BrandLockup light />
          <Text style={styles.guestTitle}>登入，參與每一次共同選擇</Text>
          <Text style={styles.guestBody}>
            查看社員價格、投票進度、訂單與取貨通知。
          </Text>
          <Button label="登入帳號" onPress={() => router.push("/login")} />
        </View>
      </Screen>
    );
  }

  return (
    <Screen>
      <PageHeader eyebrow="ACCOUNT" title="我的" />
      <View style={styles.content}>
        <View style={styles.identity}>
          <View style={styles.avatar}>
            <Text style={styles.avatarText}>{user.display_name.slice(0, 1)}</Text>
          </View>
          <View style={styles.identityCopy}>
            <Text style={styles.name}>{user.display_name}</Text>
            <Text style={styles.email}>{user.email}</Text>
          </View>
          <StatusPill
            label={isAdmin ? "管理員" : membershipLabel(user.membership_type)}
            tone={isAdmin ? "warning" : "positive"}
          />
        </View>

        <View style={styles.menu}>
          <MenuRow
            detail="查看未讀與歷史通知"
            icon="notifications-outline"
            onPress={() => router.push("/notifications")}
            title="通知中心"
          />
          <View style={styles.rule} />
          <MenuRow
            detail="一般訂單與團購訂單"
            icon="receipt-outline"
            onPress={() => router.push("/(tabs)/orders")}
            title="我的訂單"
          />
          <View style={styles.rule} />
          <MenuRow
            detail="現場取貨與發票說明"
            icon="help-circle-outline"
            onPress={() => router.push("/help")}
            title="使用說明"
          />
        </View>

        {isAdmin ? (
          <Button
            icon="settings-outline"
            label="進入管理後台"
            onPress={() => router.push("/admin")}
            variant="secondary"
          />
        ) : null}

        <Button
          label="登出帳號"
          onPress={() => {
            clear();
            logout();
            queryClient.clear();
            router.replace("/");
          }}
          variant="quiet"
        />
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  content: { gap: 13, padding: spacing.md },
  guestHero: {
    backgroundColor: colors.forest,
    borderRadius: radii.lg,
    gap: 15,
    margin: spacing.md,
    padding: spacing.lg,
  },
  guestTitle: {
    color: colors.white,
    fontSize: 24,
    fontWeight: "900",
    lineHeight: 32,
    marginTop: spacing.lg,
  },
  guestBody: { color: "#D1DED6", fontSize: 12, lineHeight: 19 },
  identity: {
    alignItems: "center",
    backgroundColor: colors.forest,
    borderRadius: radii.lg,
    flexDirection: "row",
    padding: spacing.md,
  },
  avatar: {
    alignItems: "center",
    backgroundColor: "#E0E7DC",
    borderRadius: 17,
    height: 52,
    justifyContent: "center",
    width: 52,
  },
  avatarText: { color: colors.forest, fontSize: 20, fontWeight: "900" },
  identityCopy: { flex: 1, marginLeft: 11 },
  name: { color: colors.white, fontSize: 16, fontWeight: "900" },
  email: { color: "#C9D6CE", fontSize: 9, marginTop: 4 },
  menu: {
    backgroundColor: colors.paper,
    borderRadius: radii.md,
    padding: 4,
  },
  menuRow: { alignItems: "center", flexDirection: "row", padding: 11 },
  menuIcon: {
    alignItems: "center",
    backgroundColor: colors.sageLight,
    borderRadius: 12,
    height: 40,
    justifyContent: "center",
    width: 40,
  },
  menuCopy: { flex: 1, marginLeft: 10 },
  menuTitle: { color: colors.forest, fontSize: 13, fontWeight: "900" },
  menuDetail: { color: colors.muted, fontSize: 9, marginTop: 3 },
  rule: { backgroundColor: colors.line, height: 1, marginHorizontal: 11 },
});
