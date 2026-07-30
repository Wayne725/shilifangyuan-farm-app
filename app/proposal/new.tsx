import { useMutation, useQuery } from "@tanstack/react-query";
import { router } from "expo-router";
import { useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import {
  Button,
  EmptyState,
  InlineMessage,
  LoadingState,
  PageHeader,
  Screen,
  SegmentControl,
} from "../../src/components/ui";
import { api, getErrorMessage } from "../../src/services/api";
import { useAuth } from "../../src/store/AuthContext";
import { colors, radii, spacing } from "../../src/theme";

type TargetType = "product" | "bundle";

export default function NewProposalScreen() {
  const { isAuthenticated } = useAuth();
  const [targetType, setTargetType] = useState<TargetType>("product");
  const [targetId, setTargetId] = useState("");
  const productsQuery = useQuery({
    queryKey: ["products"],
    queryFn: api.products,
  });
  const bundlesQuery = useQuery({
    queryKey: ["bundles"],
    queryFn: api.bundles,
  });
  const mutation = useMutation({
    mutationFn: () => api.createProposal(targetType, targetId),
    onSuccess: (proposal) =>
      router.replace({
        pathname: "/proposal/[id]",
        params: { id: proposal.id },
      }),
  });

  const targets =
    targetType === "product"
      ? (productsQuery.data ?? []).filter((item) => item.is_active)
      : (bundlesQuery.data ?? []).filter((item) => item.is_active);

  if (!isAuthenticated) {
    return (
      <Screen>
        <PageHeader onBack={() => router.back()} title="發起團購投票" />
        <EmptyState
          action="登入帳號"
          description="登入後才能送出提案並追蹤審核結果。"
          icon="person-outline"
          onAction={() => router.replace("/login")}
          title="請先登入"
        />
      </Screen>
    );
  }

  return (
    <Screen>
      <PageHeader
        onBack={() => router.back()}
        subtitle="只能選擇合作社既有商品或套組，送出後由管理員審核。"
        title="發起團購投票"
      />
      <SegmentControl
        onChange={(next) => {
          setTargetType(next);
          setTargetId("");
        }}
        options={[
          { value: "product", label: "單一商品" },
          { value: "bundle", label: "固定套組" },
        ]}
        value={targetType}
      />

      {productsQuery.isLoading || bundlesQuery.isLoading ? (
        <LoadingState label="載入可提案項目" />
      ) : (
        <View style={styles.content}>
          <Text style={styles.sectionTitle}>選擇想共同購買的項目</Text>
          <View style={styles.options}>
            {targets.map((target) => {
              const selected = target.id === targetId;
              return (
                <Pressable
                  key={target.id}
                  onPress={() => setTargetId(target.id)}
                  style={[
                    styles.option,
                    selected && styles.optionSelected,
                  ]}
                >
                  <View
                    style={[
                      styles.radio,
                      selected && styles.radioSelected,
                    ]}
                  >
                    {selected ? <View style={styles.dot} /> : null}
                  </View>
                  <View style={styles.optionCopy}>
                    <Text style={styles.optionName}>{target.name}</Text>
                    <Text style={styles.optionDescription} numberOfLines={2}>
                      {target.description}
                    </Text>
                  </View>
                </Pressable>
              );
            })}
          </View>

          <InlineMessage text="審核通過後，預設開放 7 天並以 10 人為評估門檻；投票不會直接產生訂單。" />
          {mutation.error ? (
            <InlineMessage
              text={getErrorMessage(mutation.error)}
              tone="danger"
            />
          ) : null}
          <Button
            disabled={!targetId}
            label="送出審核"
            loading={mutation.isPending}
            onPress={() => mutation.mutate()}
          />
        </View>
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  content: { gap: 15, padding: spacing.md },
  sectionTitle: { color: colors.forest, fontSize: 17, fontWeight: "900" },
  options: { gap: 9 },
  option: {
    alignItems: "center",
    backgroundColor: colors.paper,
    borderColor: colors.line,
    borderRadius: radii.md,
    borderWidth: 1,
    flexDirection: "row",
    padding: 14,
  },
  optionSelected: {
    backgroundColor: colors.sageLight,
    borderColor: colors.sage,
  },
  radio: {
    alignItems: "center",
    borderColor: colors.sage,
    borderRadius: 10,
    borderWidth: 1.5,
    height: 20,
    justifyContent: "center",
    width: 20,
  },
  radioSelected: { borderColor: colors.forest },
  dot: {
    backgroundColor: colors.forest,
    borderRadius: 5,
    height: 10,
    width: 10,
  },
  optionCopy: { flex: 1, marginLeft: 12 },
  optionName: { color: colors.forest, fontSize: 14, fontWeight: "900" },
  optionDescription: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 15,
    marginTop: 3,
  },
});
