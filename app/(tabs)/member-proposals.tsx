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
  MemberProposal,
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

const voteLabels: Record<MemberVoteChoice, string> = {
  yes: "贊成",
  no: "反對",
  abstain: "棄權",
};

function ProposalCard({ proposal }: { proposal: MemberProposal }) {
  const queryClient = useQueryClient();
  const [commentDraft, setCommentDraft] = useState("");
  const commentsOpen = ["discussion", "voting"].includes(proposal.status);
  const votesVisible = ["voting", "passed", "rejected", "closed"].includes(
    proposal.status,
  );
  const commentsQuery = useQuery({
    queryKey: ["member-proposal-comments", proposal.id],
    queryFn: () => api.memberProposalComments(proposal.id),
    enabled: commentsOpen,
  });
  const votesQuery = useQuery({
    queryKey: ["member-proposal-votes", proposal.id],
    queryFn: () => api.memberProposalVotes(proposal.id),
    enabled: votesVisible,
  });
  const vote = useMutation({
    mutationFn: (choice: MemberVoteChoice) =>
      api.voteMemberProposal(proposal.id, choice),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["member-proposals"] }),
        queryClient.invalidateQueries({
          queryKey: ["member-proposal-votes", proposal.id],
        }),
      ]);
    },
  });
  const comment = useMutation({
    mutationFn: (body: string) =>
      api.addMemberProposalComment(proposal.id, body),
    onSuccess: async () => {
      setCommentDraft("");
      await queryClient.invalidateQueries({
        queryKey: ["member-proposal-comments", proposal.id],
      });
    },
  });
  const voters =
    proposal.yes_count + proposal.no_count + proposal.abstain_count;
  const actionError = vote.error ?? comment.error;

  return (
    <View style={styles.card}>
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
        <Text style={styles.author}>提案人 {proposal.created_by_name}</Text>
      </View>
      <Text style={styles.title}>{proposal.title}</Text>
      <Text style={styles.summary}>{proposal.summary}</Text>
      {proposal.status === "discussion" && proposal.discussion_ends_at ? (
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
      {votesVisible ? (
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
              <Text style={styles.tallyValue}>{proposal.abstain_count}</Text>
              <Text style={styles.tallyLabel}>棄權</Text>
            </View>
          </View>
          <Text style={styles.voterMeta}>
            共 {voters} 人投票，最低投票數 {proposal.minimum_voters} 人
            {proposal.status === "voting" && proposal.voting_ends_at
              ? `，${dateTime(proposal.voting_ends_at)} 截止`
              : ""}
          </Text>
          {proposal.status === "voting" ? (
            <View style={styles.voteRow}>
              {votes.map((choice) => {
                const selected = proposal.my_vote === choice.value;
                const submitting =
                  vote.isPending && vote.variables === choice.value;
                return (
                  <Button
                    compact
                    disabled={vote.isPending || selected}
                    key={choice.value}
                    label={selected ? `已選${choice.label}` : choice.label}
                    loading={submitting}
                    onPress={() => vote.mutate(choice.value)}
                    variant={selected ? "primary" : "quiet"}
                  />
                );
              })}
            </View>
          ) : null}
        </>
      ) : null}
      {votesVisible ? (
        <View style={styles.governanceSection}>
          <Text style={styles.commentHeading}>公開記名票</Text>
          {votesQuery.isLoading ? (
            <View style={styles.sectionState}>
              <Text style={styles.sectionStateText}>載入投票名單中…</Text>
            </View>
          ) : votesQuery.isError ? (
            <View style={styles.sectionState}>
              <Text style={styles.sectionErrorText}>無法載入投票名單</Text>
              <Button
                compact
                label="重試"
                onPress={() => votesQuery.refetch()}
                variant="quiet"
              />
            </View>
          ) : votesQuery.data?.length ? (
            <View style={styles.namedVoteList}>
              {votesQuery.data.map((item) => (
                <View key={item.user_id} style={styles.namedVoteRow}>
                  <View style={styles.namedVoteIdentity}>
                    <View style={styles.avatar}>
                      <Text style={styles.avatarText}>
                        {item.display_name.trim().slice(0, 1) || "社"}
                      </Text>
                    </View>
                    <View style={styles.namedVoteCopy}>
                      <Text style={styles.namedVoteName}>
                        {item.display_name}
                      </Text>
                      <Text style={styles.namedVoteTime}>
                        {dateTime(item.updated_at)}
                      </Text>
                    </View>
                  </View>
                  <StatusPill
                    label={voteLabels[item.choice]}
                    tone={
                      item.choice === "yes"
                        ? "positive"
                        : item.choice === "no"
                          ? "danger"
                          : "neutral"
                    }
                  />
                </View>
              ))}
            </View>
          ) : (
            <View style={styles.sectionState}>
              <Text style={styles.sectionStateText}>目前還沒有人投票</Text>
            </View>
          )}
        </View>
      ) : null}
      {commentsOpen ? (
        <View style={styles.comments}>
          <Text style={styles.commentHeading}>社員討論</Text>
          {commentsQuery.isLoading ? (
            <View style={styles.sectionState}>
              <Text style={styles.sectionStateText}>載入討論中…</Text>
            </View>
          ) : commentsQuery.isError ? (
            <View style={styles.sectionState}>
              <Text style={styles.sectionErrorText}>無法載入社員討論</Text>
              <Button
                compact
                label="重試"
                onPress={() => commentsQuery.refetch()}
                variant="quiet"
              />
            </View>
          ) : commentsQuery.data?.length ? (
            commentsQuery.data.map((item) => (
              <View key={item.id} style={styles.comment}>
                <View style={styles.commentMeta}>
                  <Text style={styles.commentAuthor}>{item.author_name}</Text>
                  <Text style={styles.commentTime}>
                    {dateTime(item.created_at)}
                  </Text>
                </View>
                <Text style={styles.commentBody}>{item.body}</Text>
              </View>
            ))
          ) : (
            <View style={styles.sectionState}>
              <Text style={styles.sectionStateText}>
                尚無留言，歡迎提出第一個具體意見。
              </Text>
            </View>
          )}
          <TextInput
            accessibilityLabel={`${proposal.title}的留言內容`}
            editable={!comment.isPending}
            multiline
            onChangeText={setCommentDraft}
            placeholder="留下具體意見"
            placeholderTextColor={colors.sage}
            style={styles.commentInput}
            textAlignVertical="top"
            value={commentDraft}
          />
          <Button
            compact
            disabled={!commentDraft.trim() || comment.isPending}
            label="送出留言"
            loading={comment.isPending}
            onPress={() => comment.mutate(commentDraft.trim())}
            variant="secondary"
          />
        </View>
      ) : null}
      {actionError ? (
        <View style={styles.actionError}>
          <InlineMessage text={getErrorMessage(actionError)} tone="danger" />
        </View>
      ) : null}
    </View>
  );
}

export default function MemberProposalsScreen() {
  const { isAuthenticated } = useAuth();
  const queryClient = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [title, setTitle] = useState("");
  const [summary, setSummary] = useState("");
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
  if (membership.isError) {
    return (
      <Screen>
        <PageHeader title="社員提案" />
        <EmptyState
          action="重新載入"
          description="目前無法確認社員資格。"
          onAction={() => membership.refetch()}
          title="社員資料載入失敗"
        />
      </Screen>
    );
  }
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

  const error = create.error;
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
      ) : query.isError ? (
        <EmptyState
          action="重新載入"
          description="目前無法取得社員提案。"
          onAction={() => query.refetch()}
          title="提案載入失敗"
        />
      ) : query.data?.length ? (
        <View style={styles.list}>
          {query.data.map((proposal) => (
            <ProposalCard key={proposal.id} proposal={proposal} />
          ))}
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
  governanceSection: {
    borderTopColor: colors.line,
    borderTopWidth: 1,
    gap: 8,
    marginTop: 14,
    paddingTop: 12,
  },
  namedVoteList: { gap: 7 },
  namedVoteRow: {
    alignItems: "center",
    backgroundColor: colors.cream,
    borderRadius: radii.md,
    flexDirection: "row",
    justifyContent: "space-between",
    minHeight: 56,
    paddingHorizontal: 10,
    paddingVertical: 7,
  },
  namedVoteIdentity: {
    alignItems: "center",
    flex: 1,
    flexDirection: "row",
    gap: 9,
  },
  namedVoteCopy: { flex: 1 },
  namedVoteName: { color: colors.forest, fontSize: 13, fontWeight: "800" },
  namedVoteTime: { color: colors.muted, fontSize: 12, marginTop: 2 },
  avatar: {
    alignItems: "center",
    backgroundColor: colors.sageLight,
    borderRadius: 18,
    height: 36,
    justifyContent: "center",
    width: 36,
  },
  avatarText: { color: colors.forest, fontSize: 14, fontWeight: "900" },
  sectionState: {
    alignItems: "center",
    backgroundColor: colors.cream,
    borderRadius: radii.md,
    gap: 8,
    justifyContent: "center",
    minHeight: 52,
    padding: 8,
  },
  sectionStateText: { color: colors.muted, fontSize: 12, lineHeight: 18 },
  sectionErrorText: { color: colors.danger, fontSize: 12 },
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
  commentMeta: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
  },
  commentAuthor: { color: colors.forest, fontSize: 12, fontWeight: "800" },
  commentTime: { color: colors.muted, fontSize: 12 },
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
    minHeight: 52,
    paddingHorizontal: 12,
    paddingVertical: 12,
  },
  actionError: { marginTop: 12 },
});
