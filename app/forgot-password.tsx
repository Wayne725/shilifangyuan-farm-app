import { useMutation } from "@tanstack/react-query";
import { router } from "expo-router";
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

export default function ForgotPasswordScreen() {
  const [email, setEmail] = useState("");
  const [token, setToken] = useState("");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");
  const forgot = useMutation({
    mutationFn: () => api.forgotPassword(email.trim()),
    onSuccess: (result) => setMessage(result.message),
  });
  const reset = useMutation({
    mutationFn: () => api.resetPassword(token.trim(), password),
    onSuccess: (result) => setMessage(result.message),
  });
  const error = forgot.error ?? reset.error;

  return (
    <Screen>
      <PageHeader onBack={() => router.back()} title="重設密碼" />
      <View style={styles.content}>
        <View style={styles.field}>
          <Text style={styles.label}>帳號 Email</Text>
          <TextInput
            autoCapitalize="none"
            keyboardType="email-address"
            onChangeText={setEmail}
            placeholder="name@example.com"
            placeholderTextColor={colors.sage}
            style={styles.input}
            value={email}
          />
        </View>
        <Button
          disabled={!email.trim()}
          label="寄送重設密碼信"
          loading={forgot.isPending}
          onPress={() => forgot.mutate()}
        />
        <View style={styles.resetPanel}>
          <Text style={styles.panelTitle}>已有重設代碼</Text>
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
          <Button
            disabled={!token.trim() || password.length < 8}
            label="更新密碼"
            loading={reset.isPending}
            onPress={() => reset.mutate()}
            variant="secondary"
          />
        </View>
        {message ? <InlineMessage text={message} tone="positive" /> : null}
        {error ? (
          <InlineMessage text={getErrorMessage(error)} tone="danger" />
        ) : null}
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  content: { gap: 12, padding: spacing.md },
  field: { gap: 6 },
  label: { color: colors.forest, fontSize: 12, fontWeight: "800" },
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
  resetPanel: {
    backgroundColor: colors.paper,
    borderRadius: radii.lg,
    gap: 11,
    marginTop: spacing.md,
    padding: spacing.md,
  },
  panelTitle: { color: colors.forest, fontSize: 18, fontWeight: "900" },
});
