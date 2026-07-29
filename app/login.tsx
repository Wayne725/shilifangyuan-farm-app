import { Ionicons } from "@expo/vector-icons";
import { useQueryClient } from "@tanstack/react-query";
import { router } from "expo-router";
import { useState } from "react";
import {
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import {
  BrandLockup,
  Button,
  InlineMessage,
  PageHeader,
  Screen,
} from "../src/components/ui";
import { getApiBaseUrl, getErrorMessage } from "../src/services/api";
import { useAuth } from "../src/store/AuthContext";
import { colors, radii, spacing } from "../src/theme";

const accounts = [
  {
    label: "社員",
    email: "member@shilifangyuan.tw",
    password: "member123",
  },
  {
    label: "非社員",
    email: "customer@shilifangyuan.tw",
    password: "customer123",
  },
  {
    label: "管理員",
    email: "admin@shilifangyuan.tw",
    password: "admin123",
  },
];

export default function LoginScreen() {
  const usingBuiltInData = !getApiBaseUrl();
  const { login } = useAuth();
  const queryClient = useQueryClient();
  const [email, setEmail] = useState(accounts[0]!.email);
  const [password, setPassword] = useState(
    usingBuiltInData ? accounts[0]!.password : "",
  );
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async () => {
    setError("");
    setLoading(true);
    try {
      const user = await login(email, password);
      queryClient.clear();
      router.replace(user.user_role === "admin" ? "/admin" : "/(tabs)/home");
    } catch (submitError) {
      setError(getErrorMessage(submitError));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Screen contentStyle={styles.content}>
      <PageHeader onBack={() => router.back()} title="登入" />
      <View style={styles.brandPanel}>
        <BrandLockup light />
        <Text style={styles.brandMessage}>
          登入後即可投票、加入共同購買與查看個人訂單。
        </Text>
      </View>

      <View style={styles.form}>
        <Text style={styles.label}>Email</Text>
        <TextInput
          autoCapitalize="none"
          autoComplete="email"
          keyboardType="email-address"
          onChangeText={setEmail}
          placeholder="name@example.com"
          placeholderTextColor={colors.sage}
          style={styles.input}
          value={email}
        />
        <Text style={styles.label}>密碼</Text>
        <TextInput
          autoCapitalize="none"
          onChangeText={setPassword}
          placeholder="輸入密碼"
          placeholderTextColor={colors.sage}
          secureTextEntry
          style={styles.input}
          value={password}
        />

        {error ? <InlineMessage text={error} tone="danger" /> : null}
        <Button label="登入" loading={loading} onPress={submit} />
      </View>

      {usingBuiltInData ? (
        <View style={styles.accountPanel}>
          <Text style={styles.accountTitle}>選擇登入身分</Text>
          <Text style={styles.accountHint}>點選身分後，再按下登入。</Text>
          <View style={styles.accountRow}>
            {accounts.map((account) => (
              <Pressable
                key={account.label}
                onPress={() => {
                  setEmail(account.email);
                  setPassword(account.password);
                }}
                style={({ pressed }) => [
                  styles.account,
                  email === account.email && styles.accountSelected,
                  pressed && styles.pressed,
                ]}
              >
                <Ionicons
                  color={colors.forest}
                  name={
                    account.label === "管理員"
                      ? "settings-outline"
                      : "person-outline"
                  }
                  size={17}
                />
                <Text style={styles.accountLabel}>{account.label}</Text>
              </Pressable>
            ))}
          </View>
        </View>
      ) : null}
    </Screen>
  );
}

const styles = StyleSheet.create({
  content: { paddingBottom: spacing.xl },
  brandPanel: {
    backgroundColor: colors.forest,
    borderRadius: radii.lg,
    margin: spacing.md,
    padding: spacing.lg,
  },
  brandMessage: {
    color: "#D7E1DA",
    fontSize: 12,
    lineHeight: 19,
    marginTop: spacing.lg,
    maxWidth: 310,
  },
  form: { gap: 10, padding: spacing.md },
  label: {
    color: colors.forest,
    fontSize: 12,
    fontWeight: "800",
    marginTop: 3,
  },
  input: {
    backgroundColor: colors.paper,
    borderColor: colors.line,
    borderRadius: radii.md,
    borderWidth: 1,
    color: colors.charcoal,
    fontSize: 15,
    minHeight: 52,
    paddingHorizontal: 15,
  },
  accountPanel: {
    borderTopColor: colors.line,
    borderTopWidth: 1,
    marginHorizontal: spacing.md,
    marginTop: spacing.lg,
    paddingTop: spacing.lg,
  },
  accountTitle: { color: colors.forest, fontSize: 14, fontWeight: "900" },
  accountHint: { color: colors.muted, fontSize: 11, marginTop: 3 },
  accountRow: { flexDirection: "row", gap: 8, marginTop: 12 },
  account: {
    alignItems: "center",
    backgroundColor: colors.paper,
    borderColor: colors.line,
    borderRadius: radii.sm,
    borderWidth: 1,
    flex: 1,
    gap: 5,
    paddingVertical: 11,
  },
  accountSelected: {
    backgroundColor: colors.sageLight,
    borderColor: colors.sage,
  },
  accountLabel: { color: colors.forest, fontSize: 11, fontWeight: "800" },
  pressed: { opacity: 0.7 },
});
