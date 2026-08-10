import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { router } from "expo-router";
import { useState } from "react";
import { StyleSheet, Text, TextInput, View } from "react-native";

import {
  Button,
  EmptyState,
  InlineMessage,
  LoadingState,
  PageHeader,
  Screen,
  StatusPill,
} from "../../src/components/ui";
import { hasFormalMemberAccess } from "../../src/lib/membership";
import { api, getErrorMessage } from "../../src/services/api";
import { useAuth } from "../../src/store/AuthContext";
import { colors, radii, spacing } from "../../src/theme";

export default function SocialWishesScreen() {
  const { isAuthenticated, user } = useAuth();
  const queryClient = useQueryClient();
  const allowed = hasFormalMemberAccess(
    user?.membership_type ?? "nonmember",
  );
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const wishes = useQuery({
    queryKey: ["wishes"],
    queryFn: api.wishes,
    enabled: allowed,
  });
  const create = useMutation({
    mutationFn: () =>
      api.createWish({ name: name.trim(), description: description.trim() }),
    onSuccess: async () => {
      setName("");
      setDescription("");
      await queryClient.invalidateQueries({ queryKey: ["wishes"] });
    },
  });
  const support = useMutation({
    mutationFn: api.supportWish,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["wishes"] }),
  });

  if (!isAuthenticated || !user) {
    return (
      <Screen>
        <PageHeader onBack={() => router.back()} title="願望" />
        <EmptyState
          action="登入帳號"
          description="登入正式社員帳號後提出願望。"
          onAction={() => router.push("/login")}
          title="請先登入"
        />
      </Screen>
    );
  }
  if (!allowed) {
    return (
      <Screen>
        <PageHeader onBack={() => router.back()} title="願望" />
        <EmptyState
          action="查看社員資料"
          description="轉為正式社員後才可提出願望或幫忙集氣。"
          icon="lock-closed-outline"
          onAction={() => router.push("/(tabs)/members")}
          title="正式社員限定"
        />
      </Screen>
    );
  }
  if (wishes.isLoading) return <LoadingState label="載入願望" />;

  const error = wishes.error ?? create.error ?? support.error;
  return (
    <Screen>
      <PageHeader
        onBack={() => router.back()}
        subtitle="提出系統尚未販售的品項，讓合作社看見共同需求。"
        title="願望"
      />
      <View style={styles.content}>
        {error ? (
          <InlineMessage text={getErrorMessage(error)} tone="danger" />
        ) : null}
        <View style={styles.form}>
          <Text style={styles.heading}>提出新願望</Text>
          <TextInput
            onChangeText={setName}
            placeholder="想引進的新商品"
            placeholderTextColor={colors.sage}
            style={styles.input}
            value={name}
          />
          <TextInput
            multiline
            onChangeText={setDescription}
            placeholder="為什麼需要它、參考來源或期待價格"
            placeholderTextColor={colors.sage}
            style={[styles.input, styles.multiline]}
            value={description}
          />
          <Button
            disabled={!name.trim() || !description.trim()}
            label="提交願望"
            loading={create.isPending}
            onPress={() => create.mutate()}
          />
        </View>
        {(wishes.data ?? []).map((wish) => (
          <View key={wish.id} style={styles.card}>
            <View style={styles.row}>
              <Text style={styles.title}>{wish.name}</Text>
              <StatusPill
                label={wish.status}
                tone={wish.status === "launched" ? "positive" : "neutral"}
              />
            </View>
            <Text style={styles.body}>{wish.description}</Text>
            <Button
              compact
              disabled={wish.supported_by_me}
              label={
                wish.supported_by_me
                  ? `已集氣 ${wish.support_count}`
                  : `幫忙集氣 ${wish.support_count}`
              }
              loading={support.isPending && support.variables === wish.id}
              onPress={() => support.mutate(wish.id)}
              variant="quiet"
            />
          </View>
        ))}
        {!wishes.data?.length ? (
          <EmptyState
            description="提出第一個想共同引進的品項。"
            title="目前沒有願望"
          />
        ) : null}
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  content: { gap: 12, padding: spacing.md },
  form: {
    backgroundColor: colors.paper,
    borderRadius: radii.lg,
    gap: 11,
    padding: spacing.md,
  },
  heading: { color: colors.forest, fontSize: 19, fontWeight: "900" },
  input: {
    backgroundColor: colors.cream,
    borderColor: colors.line,
    borderRadius: radii.md,
    borderWidth: 1,
    color: colors.charcoal,
    minHeight: 48,
    padding: 12,
  },
  multiline: { minHeight: 90, textAlignVertical: "top" },
  card: {
    backgroundColor: colors.paper,
    borderRadius: radii.md,
    gap: 8,
    padding: 13,
  },
  row: { alignItems: "center", flexDirection: "row", justifyContent: "space-between" },
  title: { color: colors.charcoal, flex: 1, fontSize: 15, fontWeight: "900" },
  body: { color: colors.muted, fontSize: 13, lineHeight: 20 },
});
