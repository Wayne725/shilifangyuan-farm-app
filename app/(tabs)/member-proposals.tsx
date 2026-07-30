import { Ionicons } from "@expo/vector-icons";
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
import { dateTime } from "../../src/lib/format";
import { api, getErrorMessage } from "../../src/services/api";
import { useAuth } from "../../src/store/AuthContext";
import { colors, radii, spacing } from "../../src/theme";
import type {
  MemberProposalStatus,
  MemberVoteChoice,
} from "../../src/types";

const proposalLabels: Record<MemberProposalStatus, string> = {
  draft: "草稿",
  pending_review: "待審核",
  discussion: "討論中",
  voting: "表決中",
  passed: "通過",
  rejected: "未通過",
  withdrawn: "已撤回",
  closed: "已結案",
};

const votes: { value: MemberVoteChoice; label: string }[] = [
  { value: "yes", label: "贊成" },
  { value: "no", label: "反對" },
  { value: "abstain", label: "棄權" },
];

export default function MemberProposalsScreen() {
  const { isAuthenticated } = useAuth();
  const queryClient = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [title, setTitle] = useState("");
  const [summary, setSummary] = useState("");
  const [commentDrafts, setCommentDrafts] = useState<Record<string, string>>(
    {},
  );
  const membership = useQuery({
    queryKey: ["membership"],
    queryFn: api.membership,
    enabled: isAuthenticated,
  });
  const isMember = membership.data?.status === "active";
  const query = useQuery({
    queryKey: ["member-proposals"],
    queryFn: api.memberProposals,
    enabled: isMember,
  });
  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: ["member-proposals"] });
  const vote = useMutation({
    mutationFn: ({
      id,
      choice,
    }: {
      id: string;
      choice: MemberVoteChoice;
    }) => api.voteMemberProposal(id, choice),
    onSuccess: refresh,
  });
  const create = useMutation({
    mutationFn: () =>
      api.createMemberProposal({ title: title.trim(), summary: summary.trim() }),
    onSuccess: async () => {
      setTitle("");
      setSummary("");
      setShowForm(false);
      await refresh();
    },
  });
  const comment = useMutation({
    mutationFn: ({ id, body }: { id: string; body: string }) =>
      api.addMemberProposalComment(id, body),
    onSuccess: async (_, variables) => {
      setCommentDrafts((current) => ({ ...current, [variables.id]: "" }));
      await refresh();
    },
  });

  if (!isAuthenticated) {
    return (
      <Screen>
        <PageHeader title="社員提案" />
        <EmptyState
          action="登入帳號"
          description="社員治理提案只對有效社員開放。"
          onAction={() => router.push("/login")}
          title="請先登入"
        />
      </Screen>
    );
  }
  if (membership.isLoading) return <LoadingState label="確認社員資格" />;
  if (!isMember) {
    return (
      <Screen>
        <PageHeader title="社員提案" />
        <EmptyState
          action="查看入社程序"
          description="會籍啟用後即可提出提案、討論與記名表決。"
          onAction={() => router.push("/(tabs)/members")}
          title="社員限定"
        />
      </Screen>
    );
  }

  const error = vote.error ?? create.error ?? comment.error;
  return (
    <Screen>
      <PageHeader
        right={
          <Button
            compact
            icon={showForm ? "close" : "add"}
            label={showForm ? "收合" : "新增提案"}
            onPress={() => setShowForm((current) => !current)}
            variant="secondary"
          />
        }
        subtitle="治理提案與商品團購分開，採社員公開記名表決。"
        title="社員提案"
      />
      <View style={styles.content}>
        {error ? (
          <InlineMessage text={getErrorMessage(error)} tone="danger" />
        ) : null}
        {showForm ? (
          <View style={styles.form}>
            <Text style={styles.formTitle}>提出新提案</Text>
            <View style={styles.field}>
              <Text style={styles.label}>提案主旨</Text>
              <TextInput
                onChangeText={setTitle}
                placeholder="用一句話說明希望改變的事情"
                placeholderTextColor={colors.sage}
                style={styles.input}
                value={title}
              />
            </View>
            <View style={styles.field}>
              <Text style={styles.label}>提案摘要</Text>
              <TextInput
                multiline
                numberOfLines={4}
                onChangeText={setSummary}
                placeholder="說明原因、作法與希望社員討論的重點"
                placeholderTextColor={colors.sage}
                style={[styles.input, styles.textarea]}
                textAlignVertical="top"
                value={summary}
              />
            </View>
            <Button
              disabled={!title.trim() || !summary.trim()}
              label="送交審核"
              loading={create.isPending}
              onPress={() => create.mutate()}
            />
          </View>
        ) : null}
      </View>
      {query.isLoading ? (
        <LoadingState label="載入社員提案" />
      ) : query.data?.length ? (
        <View style={styles.list}>
          {query.data.map((proposal) => {
            const voters =
              proposal.yes_count + proposal.no_count + proposal.abstain_count;
            return (
              <View key={proposal.id} style={styles.card}>
                <View style={styles.cardTop}>
                  <StatusPill
                    label={proposalLabels[proposal.status]}
                    tone={
                      proposal.status === "passed"
                        ? "positive"
                        : proposal.status === "rejected"
                          ? "danger"
                          : proposal.status === "voting"
                            ? "warning"
                            : "neutral"
                    }
                  />
                  <Text style={styles.author}>
                    提案人 {proposal.created_by_name}
                  </Text>
                </View>
                <Text style={styles.title}>{proposal.title}</Text>
                <Text style={styles.summary}>{proposal.summary}</Text>
                {proposal.status === "discussion" &&
                proposal.discussion_ends_at ? (
                  <View style={styles.notice}>
                    <Ionicons
                      color={colors.forest}
                      name="chatbubble-ellipses-outline"
                      size={19}
                    />
                    <Text style={styles.noticeText}>
                      討論至 {dateTime(proposal.discussion_ends_at)}
                    </Text>
                  </View>
                ) : null}
                {proposal.status === "voting" ? (
                  <>
                    <View style={styles.tally}>
                      <View style={styles.tallyItem}>
                        <Text style={styles.tallyValue}>{proposal.yes_count}</Text>
                        <Text style={styles.tallyLabel}>贊成</Text>
                      </View>
                      <View style={styles.tallyItem}>
                        <Text style={styles.tallyValue}>{proposal.no_count}</Text>
                        <Text style={styles.tallyLabel}>反對</Text>
                      </View>
                      <View style={styles.tallyItem}>
                        <Text style={styles.tallyValue}>
                          {proposal.abstain_count}
                        </Text>
                        <Text style={styles.tallyLabel}>棄權</Text>
                      </View>
                    </View>
                    <Text style={styles.voterMeta}>
                      已有 {voters} 人投票，最低投票數 {proposal.minimum_voters} 人
                      {proposal.voting_ends_at
                        ? `，${dateTime(proposal.voting_ends_at)} 截止`
                        : ""}
                    </Text>
                    <View style={styles.voteRow}>
                      {votes.map((choice) => (
                        <Button
                          compact
                          key={choice.value}
                          label={
                            proposal.my_vote === choice.value
                              ? `已選${choice.label}`
                              : choice.label
                          }
                          loading={vote.isPending}
                          onPress={() =>
                            vote.mutate({ id: proposal.id, choice: choice.value })
                          }
                          variant={
                            proposal.my_vote === choice.value
                              ? "primary"
                              : "quiet"
                          }
                        />
                      ))}
                    </View>
                  </>
                ) : null}
                {["discussion", "voting"].includes(proposal.status) ? (
                  <View style={styles.comments}>
                    <Text style={styles.commentHeading}>社員討論</Text>
                    {(proposal.comments ?? []).slice(-2).map((item) => (
                      <View key={item.id} style={styles.comment}>
                        <Text style={styles.commentAuthor}>
                          {item.author_name}
                        </Text>
                        <Text style={styles.commentBody}>{item.body}</Text>
                      </View>
                    ))}
                    <TextInput
                      onChangeText={(body) =>
                        setCommentDrafts((current) => ({
                          ...current,
                          [proposal.id]: body,
                        }))
                      }
                      placeholder="留下具體意見"
                      placeholderTextColor={colors.sage}
                      style={styles.commentInput}
                      value={commentDrafts[proposal.id] ?? ""}
                    />
                    <Button
                      compact
                      disabled={!commentDrafts[proposal.id]?.trim()}
                      label="送出留言"
                      loading={comment.isPending}
                      onPress={() =>
                        comment.mutate({
                          id: proposal.id,
                          body: commentDrafts[proposal.id]!.trim(),
                        })
                      }
                      variant="secondary"
                    />
                  </View>
                ) : null}
              </View>
            );
          })}
        </View>
      ) : (
        <EmptyState
          description="送交第一筆社員提案，讓大家一起討論。"
          icon="chatbubbles-outline"
          title="目前沒有提案"
        />
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  content: { paddingHorizontal: spacing.md },
  form: {
    backgroundColor: colors.paper,
    borderRadius: radii.lg,
    gap: 12,
    marginBottom: spacing.md,
    padding: spacing.md,
  },
  formTitle: { color: colors.forest, fontSize: 18, fontWeight: "900" },
  field: { gap: 6 },
  label: { color: colors.forest, fontSize: 12, fontWeight: "800" },
  input: {
    backgroundColor: colors.cream,
    borderColor: colors.line,
    borderRadius: radii.md,
    borderWidth: 1,
    color: colors.charcoal,
    fontSize: 14,
    minHeight: 50,
    paddingHorizontal: 13,
  },
  textarea: { minHeight: 104, paddingTop: 12 },
  list: { gap: 13, padding: spacing.md },
  card: {
    backgroundColor: colors.paper,
    borderRadius: radii.lg,
    padding: spacing.md,
  },
  cardTop: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
  },
  author: { color: colors.muted, fontSize: 12 },
  title: {
    color: colors.forest,
    fontSize: 20,
    fontWeight: "900",
    marginTop: 12,
  },
  summary: {
    color: colors.charcoal,
    fontSize: 13,
    lineHeight: 21,
    marginTop: 6,
  },
  notice: {
    alignItems: "center",
    backgroundColor: colors.sageLight,
    borderRadius: radii.md,
    flexDirection: "row",
    gap: 8,
    marginTop: 13,
    padding: 11,
  },
  noticeText: { color: colors.forest, flex: 1, fontSize: 12 },
  tally: {
    flexDirection: "row",
    gap: 8,
    marginTop: 14,
  },
  tallyItem: {
    alignItems: "center",
    backgroundColor: colors.cream,
    borderRadius: radii.md,
    flex: 1,
    padding: 10,
  },
  tallyValue: { color: colors.forest, fontSize: 22, fontWeight: "900" },
  tallyLabel: { color: colors.muted, fontSize: 12, marginTop: 2 },
  voterMeta: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 18,
    marginTop: 10,
  },
  voteRow: { flexDirection: "row", gap: 8, marginTop: 12 },
  comments: {
    borderTopColor: colors.line,
    borderTopWidth: 1,
    gap: 8,
    marginTop: 14,
    paddingTop: 12,
  },
  commentHeading: { color: colors.forest, fontSize: 14, fontWeight: "900" },
  comment: {
    backgroundColor: colors.cream,
    borderRadius: radii.md,
    padding: 10,
  },
  commentAuthor: { color: colors.forest, fontSize: 12, fontWeight: "800" },
  commentBody: {
    color: colors.charcoal,
    fontSize: 12,
    lineHeight: 18,
    marginTop: 3,
  },
  commentInput: {
    borderColor: colors.line,
    borderRadius: radii.md,
    borderWidth: 1,
    color: colors.charcoal,
    fontSize: 13,
    minHeight: 46,
    paddingHorizontal: 12,
  },
});
