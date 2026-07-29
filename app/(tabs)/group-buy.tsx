import { Ionicons } from "@expo/vector-icons";
import { useQuery } from "@tanstack/react-query";
import { router } from "expo-router";
import { useState } from "react";
import {
  Image,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";

import {
  Button,
  EmptyState,
  LoadingState,
  PageHeader,
  ProgressBar,
  Screen,
  SegmentControl,
  StatusPill,
} from "../../src/components/ui";
import {
  campaignLabels,
  dateTime,
  money,
  proposalLabels,
} from "../../src/lib/format";
import { imageFor } from "../../src/lib/images";
import { api } from "../../src/services/api";
import { useAuth } from "../../src/store/AuthContext";
import { colors, radii, shadows, spacing } from "../../src/theme";
import type { GroupCampaign, VoteProposal } from "../../src/types";

type HubTab = "campaigns" | "votes" | "mine";

const tabs: { value: HubTab; label: string }[] = [
  { value: "campaigns", label: "正式團購" },
  { value: "votes", label: "投票" },
  { value: "mine", label: "我的" },
];

function CampaignCard({ campaign }: { campaign: GroupCampaign }) {
  const progress = Math.min(
    100,
    (campaign.paid_quantity / campaign.min_paid_quantity) * 100,
  );
  return (
    <Pressable
      onPress={() =>
        router.push({
          pathname: "/campaign/[id]",
          params: { id: campaign.id },
        })
      }
      style={({ pressed }) => [
        styles.campaignCard,
        pressed && styles.pressed,
      ]}
    >
      <Image
        source={imageFor(campaign.image_key, campaign.image_url)}
        style={styles.campaignImage}
      />
      <View style={styles.campaignCopy}>
        <View style={styles.cardTop}>
          <StatusPill
            label={campaignLabels[campaign.decision_status]}
            tone={
              campaign.decision_status === "confirmed"
                ? "positive"
                : campaign.decision_status === "pending_confirmation"
                  ? "warning"
                  : "neutral"
            }
          />
          <Text style={styles.deadline}>{dateTime(campaign.deadline)} 截止</Text>
        </View>
        <Text style={styles.campaignTitle}>{campaign.title}</Text>
        <Text numberOfLines={2} style={styles.cardDescription}>
          {campaign.description}
        </Text>
        <View style={styles.priceLine}>
          <Text style={styles.price}>{money(campaign.member_price)}</Text>
          <Text style={styles.priceMeta}>
            社員價・非社員 {money(campaign.nonmember_price)}
          </Text>
        </View>
        <View style={styles.progressMeta}>
          <Text style={styles.progressStrong}>
            已付款 {campaign.paid_quantity} 組
          </Text>
          <Text style={styles.progressText}>
            門檻 {campaign.min_paid_quantity} 組
          </Text>
        </View>
        <ProgressBar value={progress} />
      </View>
    </Pressable>
  );
}

function ProposalCard({ proposal }: { proposal: VoteProposal }) {
  const progress = Math.min(
    100,
    (proposal.vote_count / proposal.threshold) * 100,
  );
  return (
    <Pressable
      onPress={() =>
        router.push({
          pathname: "/proposal/[id]",
          params: { id: proposal.id },
        })
      }
      style={({ pressed }) => [
        styles.proposalCard,
        pressed && styles.pressed,
      ]}
    >
      <View style={styles.proposalIcon}>
        <Ionicons
          color={colors.forest}
          name={proposal.target_type === "bundle" ? "cube-outline" : "leaf-outline"}
          size={23}
        />
      </View>
      <View style={styles.proposalCopy}>
        <View style={styles.cardTop}>
          <StatusPill
            label={proposalLabels[proposal.status]}
            tone={proposal.status === "pending_review" ? "warning" : "neutral"}
          />
          <Text style={styles.deadline}>
            {proposal.deadline
              ? `${dateTime(proposal.deadline)} 截止`
              : "審核後設定"}
          </Text>
        </View>
        <Text style={styles.proposalTitle}>{proposal.target_name}</Text>
        <View style={styles.progressMeta}>
          <Text style={styles.progressStrong}>
            {proposal.vote_count} 人支持
          </Text>
          <Text style={styles.progressText}>目標 {proposal.threshold} 人</Text>
        </View>
        <ProgressBar value={progress} />
        <Text style={styles.estimate}>
          預估需求 {proposal.estimated_quantity} 組
          {proposal.my_vote_quantity
            ? `・我預估 ${proposal.my_vote_quantity} 組`
            : ""}
        </Text>
      </View>
    </Pressable>
  );
}

export default function GroupBuyScreen() {
  const [tab, setTab] = useState<HubTab>("campaigns");
  const { isAuthenticated } = useAuth();
  const campaignsQuery = useQuery({
    queryKey: ["campaigns"],
    queryFn: api.campaigns,
  });
  const proposalsQuery = useQuery({
    queryKey: ["proposals"],
    queryFn: api.proposals,
  });
  const ordersQuery = useQuery({
    queryKey: ["orders"],
    queryFn: api.orders,
    enabled: isAuthenticated,
  });

  const myProposals = (proposalsQuery.data ?? []).filter(
    (item) => item.my_vote_quantity,
  );
  const myCampaignIds = new Set(
    (ordersQuery.data ?? [])
      .filter((order) => order.order_kind === "group")
      .map((order) => order.group_campaign_id),
  );
  const myCampaigns = (campaignsQuery.data ?? []).filter((campaign) =>
    myCampaignIds.has(campaign.id),
  );
  const loading = campaignsQuery.isLoading || proposalsQuery.isLoading;

  return (
    <Screen>
      <PageHeader
        eyebrow="CO-BUYING"
        right={
          <Button
            compact
            icon="add"
            label="發起投票"
            onPress={() =>
              isAuthenticated
                ? router.push("/proposal/new")
                : router.push("/login")
            }
            variant="secondary"
          />
        }
        subtitle="先用投票確認需求，再由合作社審核並開放付款。"
        title="共同購買"
      />
      <SegmentControl onChange={setTab} options={tabs} value={tab} />

      {loading ? <LoadingState label="整理共同購買進度" /> : null}

      {!loading && tab === "campaigns" ? (
        <View style={styles.list}>
          {(campaignsQuery.data ?? []).map((campaign) => (
            <CampaignCard campaign={campaign} key={campaign.id} />
          ))}
        </View>
      ) : null}

      {!loading && tab === "votes" ? (
        <View style={styles.list}>
          {(proposalsQuery.data ?? []).map((proposal) => (
            <ProposalCard key={proposal.id} proposal={proposal} />
          ))}
        </View>
      ) : null}

      {!loading && tab === "mine" ? (
        isAuthenticated ? (
          <View style={styles.list}>
            {myCampaigns.length ? (
              <>
                <Text style={styles.groupLabel}>我加入的團購</Text>
                {myCampaigns.map((campaign) => (
                  <CampaignCard campaign={campaign} key={campaign.id} />
                ))}
              </>
            ) : null}
            {myProposals.length ? (
              <>
                <Text style={styles.groupLabel}>我參與的投票</Text>
                {myProposals.map((proposal) => (
                  <ProposalCard key={proposal.id} proposal={proposal} />
                ))}
              </>
            ) : null}
            {!myCampaigns.length && !myProposals.length ? (
              <EmptyState
                action="看看進行中的投票"
                description="投下你想共同購買的商品，或加入已開放付款的團購。"
                icon="people-outline"
                onAction={() => setTab("votes")}
                title="還沒有參與紀錄"
              />
            ) : null}
          </View>
        ) : (
          <EmptyState
            action="登入帳號"
            description="登入後可集中查看投票、團購訂單與付款進度。"
            icon="person-outline"
            onAction={() => router.push("/login")}
            title="登入查看我的團購"
          />
        )
      ) : null}
    </Screen>
  );
}

const styles = StyleSheet.create({
  list: { gap: 14, padding: spacing.md },
  campaignCard: {
    backgroundColor: colors.paper,
    borderRadius: radii.lg,
    overflow: "hidden",
    ...shadows.card,
  },
  campaignImage: { aspectRatio: 2.05, width: "100%" },
  campaignCopy: { padding: spacing.md },
  cardTop: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
  },
  deadline: { color: colors.muted, fontSize: 9 },
  campaignTitle: {
    color: colors.forest,
    fontSize: 21,
    fontWeight: "900",
    marginTop: 11,
  },
  cardDescription: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 19,
    marginTop: 5,
  },
  priceLine: {
    alignItems: "baseline",
    flexDirection: "row",
    gap: 8,
    marginTop: 14,
  },
  price: { color: colors.orange, fontSize: 24, fontWeight: "900" },
  priceMeta: { color: colors.muted, fontSize: 10 },
  progressMeta: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginBottom: 7,
    marginTop: 14,
  },
  progressStrong: { color: colors.forest, fontSize: 11, fontWeight: "900" },
  progressText: { color: colors.muted, fontSize: 10 },
  proposalCard: {
    backgroundColor: colors.paper,
    borderRadius: radii.lg,
    flexDirection: "row",
    gap: 12,
    padding: spacing.md,
  },
  proposalIcon: {
    alignItems: "center",
    backgroundColor: colors.sageLight,
    borderRadius: 16,
    height: 48,
    justifyContent: "center",
    width: 48,
  },
  proposalCopy: { flex: 1 },
  proposalTitle: {
    color: colors.forest,
    fontSize: 18,
    fontWeight: "900",
    marginTop: 9,
  },
  estimate: { color: colors.muted, fontSize: 10, marginTop: 9 },
  groupLabel: {
    color: colors.forest,
    fontSize: 17,
    fontWeight: "900",
    marginBottom: -4,
    marginTop: 8,
  },
  pressed: { opacity: 0.76, transform: [{ scale: 0.99 }] },
});
