import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { router, useLocalSearchParams } from "expo-router";
import { useEffect, useState } from "react";
import { StyleSheet, Text, View } from "react-native";

import {
  Button,
  EmptyState,
  InfoRow,
  InlineMessage,
  LoadingState,
  PageHeader,
  ProgressBar,
  QuantityControl,
  Screen,
  StatusPill,
} from "../../src/components/ui";
import { dateTime, proposalLabels } from "../../src/lib/format";
import { api, getErrorMessage } from "../../src/services/api";
import { useAuth } from "../../src/store/AuthContext";
import { colors, radii, spacing } from "../../src/theme";

export default function ProposalDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { isAuthenticated } = useAuth();
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["proposal", id],
    queryFn: () => api.proposal(id),
  });
  const [quantity, setQuantity] = useState(1);
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (query.data?.my_vote_quantity) setQuantity(query.data.my_vote_quantity);
  }, [query.data?.my_vote_quantity]);

  const vote = useMutation({
    mutationFn: () => api.voteProposal(id, quantity),
    onSuccess: async () => {
      setMessage("已更新你的預估數量");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["proposal", id] }),
        queryClient.invalidateQueries({ queryKey: ["proposals"] }),
      ]);
    },
  });
  const withdraw = useMutation({
    mutationFn: () => api.withdrawVote(id),
    onSuccess: async () => {
      setMessage("已撤回這次投票");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["proposal", id] }),
        queryClient.invalidateQueries({ queryKey: ["proposals"] }),
      ]);
    },
  });

  if (query.isLoading) return <LoadingState label="載入投票進度" />;
  if (!query.data) {
    return (
      <Screen>
        <PageHeader onBack={() => router.back()} title="投票詳情" />
        <EmptyState
          description="這筆提案可能已被關閉。"
          title="找不到提案"
        />
      </Screen>
    );
  }

  const proposal = query.data;
  const progress = Math.min(
    100,
    (proposal.vote_count / proposal.threshold) * 100,
  );
  const canVote = proposal.status === "voting";

  return (
    <Screen>
      <PageHeader onBack={() => router.back()} title="投票詳情" />
      <View style={styles.hero}>
        <StatusPill label={proposalLabels[proposal.status]} tone="warning" />
        <Text style={styles.title}>{proposal.target_name}</Text>
        <Text style={styles.description}>
          用一票表達需求。投票只供合作社評估，不代表已下單或付款。
        </Text>
        <View style={styles.countRow}>
          <View>
            <Text style={styles.count}>{proposal.vote_count}</Text>
            <Text style={styles.countLabel}>目前支持人數</Text>
          </View>
          <View style={styles.countRule} />
          <View>
            <Text style={styles.count}>{proposal.threshold}</Text>
            <Text style={styles.countLabel}>開團評估門檻</Text>
          </View>
        </View>
        <ProgressBar value={progress} />
      </View>

      <View style={styles.content}>
        <View style={styles.infoPanel}>
          <InfoRow
            icon="calendar-outline"
            label="投票截止"
            value={
              proposal.deadline ? dateTime(proposal.deadline) : "審核後設定"
            }
          />
          <View style={styles.rule} />
          <InfoRow
            icon="layers-outline"
            label="預估總需求"
            value={`${proposal.estimated_quantity} 組`}
          />
        </View>

        {message ? <InlineMessage text={message} tone="positive" /> : null}
        {vote.error || withdraw.error ? (
          <InlineMessage
            text={getErrorMessage(vote.error ?? withdraw.error)}
            tone="danger"
          />
        ) : null}

        {canVote ? (
          <View style={styles.votePanel}>
            <View>
              <Text style={styles.voteTitle}>我預估會買</Text>
              <Text style={styles.voteHint}>截止前可隨時修改或撤回</Text>
            </View>
            <QuantityControl max={20} onChange={setQuantity} value={quantity} />
          </View>
        ) : null}

        {canVote ? (
          isAuthenticated ? (
            <View style={styles.actions}>
              <Button
                label={proposal.my_vote_quantity ? "更新投票" : "投下支持票"}
                loading={vote.isPending}
                onPress={() => vote.mutate()}
              />
              {proposal.my_vote_quantity ? (
                <Button
                  label="撤回投票"
                  loading={withdraw.isPending}
                  onPress={() => withdraw.mutate()}
                  variant="quiet"
                />
              ) : null}
            </View>
          ) : (
            <Button
              label="登入後投票"
              onPress={() => router.push("/login")}
            />
          )
        ) : (
          <InlineMessage text="這次投票已結束，票數與預估數量不再變動。" />
        )}
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  hero: {
    backgroundColor: colors.forest,
    marginHorizontal: spacing.md,
    padding: spacing.lg,
    borderRadius: radii.lg,
  },
  title: {
    color: colors.white,
    fontSize: 29,
    fontWeight: "900",
    marginTop: spacing.md,
  },
  description: {
    color: "#D6E0D9",
    fontSize: 12,
    lineHeight: 19,
    marginTop: 8,
  },
  countRow: {
    flexDirection: "row",
    gap: spacing.lg,
    marginBottom: 12,
    marginTop: spacing.lg,
  },
  count: { color: "#E9C29D", fontSize: 31, fontWeight: "900" },
  countLabel: { color: "#C8D5CC", fontSize: 12, marginTop: 2 },
  countRule: { backgroundColor: "#4D6D62", width: 1 },
  content: { gap: 14, padding: spacing.md },
  infoPanel: {
    backgroundColor: colors.paper,
    borderRadius: radii.md,
    gap: 12,
    padding: spacing.md,
  },
  rule: { backgroundColor: colors.line, height: 1 },
  votePanel: {
    alignItems: "center",
    backgroundColor: colors.orangeSoft,
    borderRadius: radii.md,
    flexDirection: "row",
    justifyContent: "space-between",
    padding: spacing.md,
  },
  voteTitle: { color: colors.forest, fontSize: 15, fontWeight: "900" },
  voteHint: { color: colors.muted, fontSize: 12, marginTop: 4 },
  actions: { gap: 9 },
});
