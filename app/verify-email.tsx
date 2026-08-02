import { useMutation } from "@tanstack/react-query";
import { router, useLocalSearchParams } from "expo-router";
import { useEffect, useRef, useState } from "react";
import { StyleSheet, Text, TextInput, View } from "react-native";

import {
  Button,
  InlineMessage,
  PageHeader,
  Screen,
} from "../src/components/ui";
import { api, getErrorMessage } from "../src/services/api";
import { colors, radii, spacing } from "../src/theme";

export default function VerifyEmailScreen() {
  const { token: linkToken } = useLocalSearchParams<{ token?: string }>();
  const [token, setToken] = useState(linkToken ?? "");
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState("");
  const autoSubmitted = useRef(false);

  const verify = useMutation({
    mutationFn: (value: string) => api.verifyEmail(value.trim()),
    onSuccess: (result) => setMessage(result.message),
  });
  const resend = useMutation({
    mutationFn: () => api.resendVerification(email.trim()),
    onSuccess: (result) => setMessage(result.message),
  });

  useEffect(() => {
    if (linkToken && !autoSubmitted.current) {
      autoSubmitted.current = true;
      setToken(linkToken);
      verify.mutate(linkToken);
    }
  }, [linkToken]);

  const verified = verify.isSuccess;
  const error = verify.error ?? resend.error;

  return (
    <Screen>
      <PageHeader
        onBack={() => router.back()}
        subtitle="完成 Email 驗證後才能登入。"
        title="驗證 Email"
      />
      <View style={styles.content}>
        {verified ? (
          <>
            <InlineMessage text={message} tone="positive" />
            <Button label="前往登入" onPress={() => router.replace("/login")} />
          </>
        ) : (
          <>
            <View style={styles.field}>
              <Text style={styles.label}>驗證代碼</Text>
              <TextInput
                keyboardType="number-pad"
                maxLength={6}
                onChangeText={setToken}
                placeholder="輸入 6 位數驗證碼"
                placeholderTextColor={colors.sage}
                style={styles.input}
                value={token}
              />
            </View>
            <Button
              disabled={!/^\d{6}$/.test(token)}
              label="完成驗證"
              loading={verify.isPending}
              onPress={() => verify.mutate(token)}
            />
            <View style={styles.panel}>
              <Text style={styles.panelTitle}>沒有收到驗證信</Text>
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
                disabled={!email.includes("@")}
                label="重新寄送驗證信"
                loading={resend.isPending}
                onPress={() => resend.mutate()}
                variant="secondary"
              />
            </View>
            {message ? <InlineMessage text={message} tone="positive" /> : null}
          </>
        )}
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
  panel: {
    backgroundColor: colors.paper,
    borderRadius: radii.lg,
    gap: 11,
    marginTop: spacing.md,
    padding: spacing.md,
  },
  panelTitle: { color: colors.forest, fontSize: 18, fontWeight: "900" },
});
