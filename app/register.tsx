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

export default function RegisterScreen() {
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [verificationToken, setVerificationToken] = useState("");
  const [message, setMessage] = useState("");
  const register = useMutation({
    mutationFn: () =>
      api.register({
        display_name: displayName.trim(),
        email: email.trim(),
        password,
      }),
    onSuccess: (result) => setMessage(result.message),
  });
  const verify = useMutation({
    mutationFn: () => api.verifyEmail(verificationToken.trim()),
    onSuccess: (result) => setMessage(result.message),
  });
  const resend = useMutation({
    mutationFn: () => api.resendVerification(email.trim()),
    onSuccess: (result) => setMessage(result.message),
  });
  const error = register.error ?? verify.error ?? resend.error;

  return (
    <Screen>
      <PageHeader onBack={() => router.back()} title="建立帳號" />
      <View style={styles.content}>
        <Text style={styles.intro}>
          帳號建立後需完成 Email 驗證，才能送出入社申請與進行購買。
        </Text>
        <View style={styles.field}>
          <Text style={styles.label}>顯示名稱</Text>
          <TextInput
            onChangeText={setDisplayName}
            placeholder="你的稱呼"
            placeholderTextColor={colors.sage}
            style={styles.input}
            value={displayName}
          />
        </View>
        <View style={styles.field}>
          <Text style={styles.label}>Email</Text>
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
        <View style={styles.field}>
          <Text style={styles.label}>密碼</Text>
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
          disabled={!displayName.trim() || !email.trim() || password.length < 8}
          label="建立帳號並寄送驗證信"
          loading={register.isPending}
          onPress={() => register.mutate()}
        />
        {message ? <InlineMessage text={message} tone="positive" /> : null}
        {error ? (
          <InlineMessage text={getErrorMessage(error)} tone="danger" />
        ) : null}

        <View style={styles.verifyPanel}>
          <Text style={styles.panelTitle}>完成 Email 驗證</Text>
          <Text style={styles.panelText}>
            從驗證信連結帶入的代碼可在這裡完成驗證。
          </Text>
          <TextInput
            autoCapitalize="none"
            onChangeText={setVerificationToken}
            placeholder="驗證代碼"
            placeholderTextColor={colors.sage}
            style={styles.input}
            value={verificationToken}
          />
          <Button
            disabled={!verificationToken.trim()}
            label="驗證 Email"
            loading={verify.isPending}
            onPress={() => verify.mutate()}
            variant="secondary"
          />
          <Button
            disabled={!email.trim()}
            label="重寄驗證信"
            loading={resend.isPending}
            onPress={() => resend.mutate()}
            variant="quiet"
          />
        </View>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  content: { gap: 12, padding: spacing.md },
  intro: { color: colors.muted, fontSize: 13, lineHeight: 20 },
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
  verifyPanel: {
    backgroundColor: colors.paper,
    borderRadius: radii.lg,
    gap: 10,
    marginTop: spacing.md,
    padding: spacing.md,
  },
  panelTitle: { color: colors.forest, fontSize: 18, fontWeight: "900" },
  panelText: { color: colors.muted, fontSize: 12, lineHeight: 18 },
});
