import { useMutation } from "@tanstack/react-query";
import { router, useLocalSearchParams } from "expo-router";
import { useState } from "react";
import { StyleSheet, Text, TextInput, View } from "react-native";

import {
  Button,
  InlineMessage,
  PageHeader,
  Screen,
} from "../src/components/ui";
import { api, getErrorMessage } from "../src/services/api";
import { colors, radii, spacing } from "../src/theme";

/** Target of the reset link sent by the API (`/reset-password?token=…`). */
export default function ResetPasswordScreen() {
  const { token: linkToken } = useLocalSearchParams<{ token?: string }>();
  const [token, setToken] = useState(linkToken ?? "");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [message, setMessage] = useState("");

  const reset = useMutation({
    mutationFn: () => api.resetPassword(token.trim(), password),
    onSuccess: (result) => setMessage(result.message),
  });

  const mismatched = confirmation.length > 0 && confirmation !== password;

  return (
    <Screen>
      <PageHeader
        onBack={() => router.back()}
        subtitle="重設連結有效期限為 1 小時。"
        title="設定新密碼"
      />
      <View style={styles.content}>
        {reset.isSuccess ? (
          <>
            <InlineMessage text={message} tone="positive" />
            <Button label="前往登入" onPress={() => router.replace("/login")} />
          </>
        ) : (
          <>
            <View style={styles.field}>
              <Text style={styles.label}>重設代碼</Text>
              <TextInput
                autoCapitalize="none"
                onChangeText={setToken}
                placeholder="輸入信件中的代碼"
                placeholderTextColor={colors.sage}
                style={styles.input}
                value={token}
              />
            </View>
            <View style={styles.field}>
              <Text style={styles.label}>新密碼</Text>
              <TextInput
                onChangeText={setPassword}
                placeholder="至少 8 碼"
                placeholderTextColor={colors.sage}
                secureTextEntry
                style={styles.input}
                value={password}
              />
            </View>
            <View style={styles.field}>
              <Text style={styles.label}>再次輸入新密碼</Text>
              <TextInput
                onChangeText={setConfirmation}
                placeholder="再輸入一次"
                placeholderTextColor={colors.sage}
                secureTextEntry
                style={styles.input}
                value={confirmation}
              />
            </View>
            {mismatched ? (
              <InlineMessage text="兩次輸入的密碼不一致" tone="danger" />
            ) : null}
            <Button
              disabled={
                !token.trim() || password.length < 8 || password !== confirmation
              }
              label="更新密碼"
              loading={reset.isPending}
              onPress={() => reset.mutate()}
            />
          </>
        )}
        {reset.error ? (
          <InlineMessage text={getErrorMessage(reset.error)} tone="danger" />
        ) : null}
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  content: { gap: 12, padding: spacing.md },
  field: { gap: 6 },
  label: { color: colors.forest, fontSize: 13, fontWeight: "800" },
  input: {
    backgroundColor: colors.paper,
    borderColor: colors.line,
    borderRadius: radii.md,
    borderWidth: 1,
    color: colors.charcoal,
    fontSize: 15,
    minHeight: 50,
    paddingHorizontal: 14,
  },
});
