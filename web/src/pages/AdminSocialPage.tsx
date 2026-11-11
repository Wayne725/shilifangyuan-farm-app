import * as Tabs from "@radix-ui/react-tabs";
import { CalendarBlank, CheckCircle, IdentificationCard, NotePencil, UsersThree } from "@phosphor-icons/react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useState } from "react";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";

import { DataState, LoadingLines } from "../components/Shared";
import { SocialActionButton } from "../components/SocialActionButton";
import { AdminActivityCard, ActivityRefundInbox } from "../components/AdminActivityPayments";
import { AdminAdmissionInbox } from "../components/AdminAdmissionInbox";
import { MembershipEducationSettings } from "../components/MembershipEducationSettings";
import { activityAccessDenied, activityTime, retryActivityRead } from "../lib/activities";
import { AdminNav } from "../components/AdminNav";
import { MeetingFilters, MeetingSetup } from "../components/MeetingTools";
import { filterMeetings, meetingAccessDenied, meetingCategoryName, meetingCategoryOptions, retryMeetingRead } from "../lib/meetings";
import { RosterCsvImport } from "../components/RosterCsvImport";
import { useStorefrontStatus } from "../components/StorefrontStatus";
import { useAuth } from "../context/AuthContext";
import { ApiError, apiFetch, formatDateTime } from "../lib/api";
import { proposalStatusLabel } from "../lib/labels";
import { documentAvailable, documentExpired, documentRetentionLabel } from "../lib/membership-documents";
import { useDeadlineClock } from "../lib/use-deadline-clock";
import type { Activity, Meeting, MembershipApplication, Proposal } from "../lib/types";
import "./AdminSocialPage.css";
import { OptionalMark } from "../components/OptionalMark";

interface ShareholdingRecord {
  id: string;
  share_capital_amount: number;
  share_count: number;
  updated_at: string;
}

interface AdminMembership extends ShareholdingRecord {
  user_id: string;
  display_name?: string;
  status: string;
  member_number?: string | null;
  trainee_number?: string | null;
}


interface MemberRosterEntry extends ShareholdingRecord {
  member_number: string;
  legal_name: string;
  email_masked: string;
  phone_masked: string;
  is_active: boolean;
  claimed: boolean;
  claimed_at?: string | null;
}

const socialTabs = [
  { value: "reviews", label: "審核工作" },
  { value: "activities", label: "活動管理" },
  { value: "proposals", label: "社員提案" },
  { value: "meetings", label: "會議管理" },
  { value: "members", label: "會籍管理" },
  { value: "roster", label: "社員名冊" },
  { value: "education", label: "入社教育" },
] as const;

type WorkScope = "current" | "history" | "all";
const workScopeOptions = [
  { value: "current", label: "進行中" },
  { value: "history", label: "歷史紀錄" },
  { value: "all", label: "全部" },
] as const;
const activityScopeOptions = [...workScopeOptions, { value: "active", label: "已發布進行中" }] as const;
const proposalScopeOptions = [...workScopeOptions, { value: "voting", label: "表決中" }] as const;
const filterWork = <T,>(items: T[] | undefined, scope: WorkScope, current: (item: T) => boolean) =>
  (items || []).filter(item => scope === "all" || (scope === "current" ? current(item) : !current(item)));

export function AdminSocialPage() {
  const { user, openLogin } = useAuth();
  const isAdmin = user?.user_role === "admin";
  const { tab = "reviews", view, q } = useSearch({ from: "/admin/social" });
  const navigate = useNavigate({ from: "/admin/social" });
  const refundView = view === "refunds";
  const activeActivityView = view === "active";
  const votingView = view === "voting";
  const now = useDeadlineClock();
  const platform = useStorefrontStatus();
  const [activityAccessRevoked, setActivityAccessRevoked] = useState(false);
  const [reviewScope, setReviewScope] = useState("pending");
  const [activityScope, setActivityScope] = useState<WorkScope>("current");
  const [proposalScope, setProposalScope] = useState<WorkScope>("current");
  const [memberSearch, setMemberSearch] = useState(q ?? "");
  useEffect(() => setMemberSearch(q ?? ""), [q]);
  const [memberStatus, setMemberStatus] = useState("");
  const [meetingCategory, setMeetingCategory] = useState("");
  const [meetingKeyword, setMeetingKeyword] = useState("");
  const [meetingAccessRevoked, setMeetingAccessRevoked] = useState(false);
  const queryClient = useQueryClient();
  const [activities, proposals, applications, meetings, memberships, roster] = useQueries({ queries: [
    { queryKey: ["admin-activities", user?.id], queryFn: () => apiFetch<Activity[]>("/v1/admin/activities"), enabled: isAdmin && tab === "activities" && !refundView && !activityAccessRevoked, retry: retryActivityRead },
    { queryKey: ["admin-member-proposals"], queryFn: () => apiFetch<Proposal[]>("/v1/admin/member-proposals"), enabled: isAdmin && (tab === "proposals" || tab === "meetings") },
    { queryKey: ["admin-membership-applications"], queryFn: () => apiFetch<MembershipApplication[]>("/v1/admin/membership-applications"), enabled: isAdmin && tab === "reviews" },
    { queryKey: ["admin-meetings"], queryFn: () => apiFetch<Meeting[]>("/v1/admin/meetings"), enabled: isAdmin && tab === "meetings", retry: retryMeetingRead },
    { queryKey: ["admin-members"], queryFn: () => apiFetch<AdminMembership[]>("/v1/admin/members"), enabled: isAdmin && ["reviews", "meetings", "members", "roster"].includes(tab) },
    { queryKey: ["admin-member-roster"], queryFn: () => apiFetch<MemberRosterEntry[]>("/v1/admin/member-roster"), enabled: isAdmin && (tab === "roster" || tab === "members") },
  ] });

  if (!isAdmin) {
    return <section className="admin-gate"><div className="admin-gate-mark"><UsersThree size={38} weight="light" /></div><p className="eyebrow">SOCIAL OPERATIONS / 社務管理</p><h1>社務管理只向管理者開放。</h1><p>請使用管理者帳號登入。</p>{!user ? <button className="button button-primary" type="button" onClick={openLogin}>管理者登入</button> : <Link className="button button-quiet" to="/social">返回社務系統</Link>}</section>;
  }

  const activityDenied = activityAccessRevoked || (!refundView && activityAccessDenied(activities.error));
  const pendingApplications = applications.data?.filter((item) => ["submitted", "needs_supplement"].includes(item.status)) || [];
  const traineeMemberships = memberships.data?.filter((item) => item.status === "trainee") || [];

  const publishedActive = (item: Activity) => item.status === "published" && item.is_public !== false && activityTime(item.ends_at) > now;
  const visibleActivities = activeActivityView ? (activities.data || []).filter(publishedActive) :
    filterWork(activities.data, activityScope, item => item.status === "pending_review" || publishedActive(item));
  const voting = (item: Proposal) => item.status === "voting" && activityTime(item.voting_ends_at) > now;
  const visibleProposals = votingView ? (proposals.data || []).filter(voting) : filterWork(proposals.data, proposalScope, item =>
    item.status === "pending_review" || voting(item) || (item.status === "discussion" && activityTime(item.voting_ends_at) > now));
  const visibleApplications = reviewScope === "pending" ? pendingApplications : (applications.data || []).filter(item =>
    reviewScope === "all" || !["submitted", "needs_supplement"].includes(item.status));
  const visibleTrainees = reviewScope === "done" ? [] : traineeMemberships;
  const admissionApprovalBlockedReason = platform.isPending
    ? "正在確認入社核准是否開放。"
    : platform.isError || !platform.data?.sales_scope
      ? "無法確認入社核准狀態，請稍後重新整理。"
      : platform.data.sales_scope !== "all"
        ? "完整入社流程尚未開放，暫不能核准或建立繳費通知；仍可要求補件或駁回。"
        : "";
  const visibleMembers = (memberships.data || []).map(item => ({...item, display_name: roster.data?.find(entry => entry.member_number === item.member_number)?.legal_name ?? item.display_name})).filter(item => (!memberStatus || item.status === memberStatus) && `${item.display_name || ""} ${item.member_number || ""} ${item.trainee_number || ""} ${item.id} ${item.user_id}`.toLowerCase().includes(memberSearch.trim().toLowerCase()));

  const visibleMeetings = filterMeetings(meetings.data || [], meetingCategory, meetingKeyword);
  const refreshMeetings = () => { void queryClient.invalidateQueries({ queryKey: ["admin-meetings"] }); void queryClient.invalidateQueries({ queryKey: ["meetings"] }); };
  const refreshActivities = () => {
    for (const key of ["admin-activities", "admin-activity-registrations", "admin-activity-refunds", "admin-activity-charge", "activity-finance", "activities", "activity-registrations", "activity-charges"]) {
      void queryClient.invalidateQueries({ queryKey: [key] });
    }
  };
  const revokeActivityAccess = () => setActivityAccessRevoked(true);

  return (
    <section className="admin-social-page">
      <header className="workspace-heading">
        <div><p className="eyebrow">SOCIAL OPERATIONS / 社務管理</p><h1>審核、排程與社員服務</h1></div>
      </header>
      <AdminNav />

      <Tabs.Root className="admin-social-tabs" value={tab} onValueChange={value => {
        const next = socialTabs.find(item => item.value === value);
        if (next) void navigate({ search: { tab: next.value }, resetScroll: false });
      }}>
        <Tabs.List className="tab-list" aria-label="社務管理分類">
          {socialTabs.map(item => <Tabs.Trigger key={item.value} value={item.value}>{item.label}</Tabs.Trigger>)}
        </Tabs.List>

        <Tabs.Content className="tab-content" value="reviews">
          <AdminAdmissionInbox />
          <label className="field social-review-scope"><span>審核範圍</span><select value={reviewScope} onChange={(event) => setReviewScope(event.target.value)}><option value="pending">待處理</option><option value="all">全部</option><option value="done">已處理</option></select></label>
          {(reviewScope !== "pending" || visibleApplications.length > 0) && <AdminReviewSection icon={IdentificationCard} title="舊版入社資料" count={visibleApplications.length} pending={applications.isPending} error={applications.error?.message} empty="沒有符合條件的舊版申請">
            {admissionApprovalBlockedReason && visibleApplications.some(application => application.status === "submitted") && <p className="form-error" role="status">{admissionApprovalBlockedReason}</p>}
            {visibleApplications.map((application) => <MembershipReviewCard key={application.id} application={application} approvalBlockedReason={admissionApprovalBlockedReason} onDone={() => queryClient.invalidateQueries({ queryKey: ["admin-membership-applications"] })} />)}
          </AdminReviewSection>}

          {reviewScope === "pending" && visibleApplications.length === 0 && applications.isError && <p role="alert">舊版申請無法讀取：{applications.error.message}</p>}
          {memberships.isError && <p role="alert" className="form-error">會籍待辦無法讀取：{memberships.error.message}</p>}
          {visibleTrainees.length > 0 && <AdminReviewSection icon={UsersThree} title="待轉正式社員" count={visibleTrainees.length} pending={memberships.isPending} empty="沒有待轉正式社員">{visibleTrainees.map(membership => <MembershipStatusCard key={membership.id} membership={membership} onDone={() => queryClient.invalidateQueries({queryKey: ["admin-members"]})} />)}</AdminReviewSection>}
        </Tabs.Content>

        <Tabs.Content className="tab-content" value="activities">
          {activityDenied ? <p role="alert">活動管理權限已失效，請重新登入。</p> : <>
            <div className="admin-quick-buttons">
              <Link className="button button-quiet" to="/admin/social" search={{ tab: "activities", view: refundView ? undefined : "refunds" }}>
                {refundView ? "活動清單" : "活動退款"}
              </Link>
            </div>
            {refundView ? <ActivityRefundInbox onDenied={revokeActivityAccess} /> : <>
              <WorkScopeSelect label="活動範圍" value={activeActivityView ? "active" : activityScope} options={activityScopeOptions} onChange={scope => {
                if (scope === "active") {
                  void navigate({ search: { tab: "activities", view: "active" }, resetScroll: false });
                } else {
                  setActivityScope(scope);
                  if (activeActivityView) void navigate({ search: { tab: "activities" }, resetScroll: false });
                }
              }} />
              <AdminReviewSection icon={CalendarBlank} title="活動管理" count={visibleActivities.length} pending={activities.isPending} error={activities.error?.message} empty="沒有符合條件的活動">
                {visibleActivities.map(activity => <AdminActivityCard key={`${user?.id}:${activity.id}`} activity={activity} onDone={refreshActivities} onDenied={revokeActivityAccess} />)}
              </AdminReviewSection>
            </>}
          </>}
        </Tabs.Content>

        <Tabs.Content className="tab-content" value="proposals">
          <WorkScopeSelect label="提案範圍" value={votingView ? "voting" : proposalScope} options={proposalScopeOptions} onChange={scope => {
            if (scope === "voting") {
              void navigate({ search: { tab: "proposals", view: "voting" }, resetScroll: false });
            } else {
              setProposalScope(scope);
              if (votingView) void navigate({ search: { tab: "proposals" }, resetScroll: false });
            }
          }} />
          <AdminReviewSection icon={NotePencil} title="社員提案" count={visibleProposals.length} pending={proposals.isPending} error={proposals.error?.message} empty="沒有符合條件的提案">
            {visibleProposals.map(proposal => <ProposalReviewCard key={proposal.id} proposal={proposal} onDone={() => queryClient.invalidateQueries({ queryKey: ["admin-member-proposals"] })} />)}
          </AdminReviewSection>
        </Tabs.Content>

        <Tabs.Content className="tab-content" value="members">
          <div className="social-member-filters"><label className="field"><span>姓名、社員編號或會員識別碼</span><input type="search" value={memberSearch} onChange={event => setMemberSearch(event.target.value)} /></label><label className="field"><span>會籍狀態</span><select value={memberStatus} onChange={event => setMemberStatus(event.target.value)}><option value="">全部狀態</option>{["trainee", "active", "suspended", "resigned", "terminated"].map(value => <option key={value} value={value}>{membershipStatusLabel(value)}</option>)}</select></label></div>
          <AdminReviewSection icon={UsersThree} title="會籍管理" count={visibleMembers.length} pending={memberships.isPending} error={memberships.error?.message} empty="目前沒有會籍">
            {visibleMembers.map((membership) => <MembershipStatusCard key={membership.id} membership={membership} onDone={() => queryClient.invalidateQueries({ queryKey: ["admin-members"] })} />)}
          </AdminReviewSection>
        </Tabs.Content>
        <Tabs.Content className="tab-content" value="meetings">
          {meetingAccessRevoked || meetingAccessDenied(meetings.error) ? <DataState kind="error" title="會議管理權限已失效" detail="請重新登入管理員帳號" /> : <>
          <MeetingSetup key={user?.id} onDone={refreshMeetings} onAccessDenied={() => setMeetingAccessRevoked(true)} />
          <MeetingFilters categories={meetingCategoryOptions(meetings.data || [])} categoryId={meetingCategory} keyword={meetingKeyword} onCategory={setMeetingCategory} onKeyword={setMeetingKeyword} />
          <AdminReviewSection icon={CalendarBlank} title="會議紀錄" count={visibleMeetings.length} pending={meetings.isPending} error={meetings.error?.message} empty={meetingCategory || meetingKeyword ? "沒有符合條件的會議" : "目前沒有會議"}>
            {!meetings.isError && visibleMeetings.map((meeting) => <MeetingAdminCard key={meeting.id} meeting={meeting} memberships={memberships.data || []} proposals={proposals.data || []} onDone={refreshMeetings} />)}
          </AdminReviewSection>
          </>}
        </Tabs.Content>

        <Tabs.Content className="tab-content" value="roster">
          <RosterCsvImport onDone={() => queryClient.invalidateQueries({ queryKey: ["admin-member-roster"] })} />
          <AdminReviewSection icon={IdentificationCard} title="既有社員名冊" count={roster.data?.length || 0} pending={roster.isPending} error={roster.error?.message} empty="尚未匯入既有社員">
            {roster.data?.map((entry) => <RosterEntryCard key={entry.id} entry={entry} />)}
          </AdminReviewSection>
        </Tabs.Content>
        <Tabs.Content className="tab-content" value="education">
          <MembershipEducationSettings />
        </Tabs.Content>
      </Tabs.Root>
    </section>
  );
}

function WorkScopeSelect<Scope extends string>({ label, value, options, onChange }: { label: string; value: Scope; options: readonly { value: Scope; label: string }[]; onChange: (value: Scope) => void }) {
  return <label className="field social-review-scope"><span>{label}</span><select value={value} onChange={event => {
    const option = options.find(item => item.value === event.target.value);
    if (option) onChange(option.value);
  }}>
    {options.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
  </select></label>;
}

function AdminReviewSection({ icon: Icon, title, count, pending, error, empty, children }: { icon: typeof CalendarBlank; title: string; count: number; pending: boolean; error?: string; empty: string; children: React.ReactNode }) {
  return <section className="admin-review-section"><div className="section-title-row"><div><h2>{title}</h2></div><span><Icon size={20} />{count}</span></div>{pending && <LoadingLines count={2} />}{error && <DataState kind="error" title={`${title}無法讀取`} detail={error} />}{!pending && !error && count === 0 && <DataState title={empty} detail="" />}<div className="admin-review-list">{children}</div></section>;
}

function RosterEntryCard({ entry }: { entry: MemberRosterEntry }) {
  return (
    <article className="admin-review-card roster-entry-card">
      <div className="review-card-copy">
        <span className="status-chip">{entry.claimed ? "已認領" : entry.is_active ? "可認領" : "已停用"}</span>
        <h3>{entry.member_number} · {entry.legal_name}</h3>
        <p>{entry.email_masked} · {entry.phone_masked || "未提供電話"}</p>
        <small>股金 {entry.share_capital_amount} 元 · {entry.share_count} 股</small>
      </div>
      <div className="review-card-actions"><strong>{entry.claimed ? "已連結社員帳號" : "等待社員完成名冊核對"}</strong>{entry.claimed_at && <small>{formatDateTime(entry.claimed_at)}</small>}<ShareholdingEditor record={entry} source="roster" /></div>
    </article>
  );
}

function ShareholdingEditor({ record, source }: { record: ShareholdingRecord; source: "roster" | "membership" }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [amount, setAmount] = useState(String(record.share_capital_amount));
  const [count, setCount] = useState(String(record.share_count));
  const [reason, setReason] = useState("");
  const [expectedUpdatedAt, setExpectedUpdatedAt] = useState(record.updated_at);
  const [conflict, setConflict] = useState(false);
  const [latest, setLatest] = useState<ShareholdingRecord | null>(null);
  const [confirmedLatest, setConfirmedLatest] = useState(false);
  const [saved, setSaved] = useState(false);
  const endpoint = source === "roster" ? `/v1/admin/member-roster/${record.id}/shares` : `/v1/admin/members/${record.id}/shares`;
  const validInteger = (value: string) => value.trim() !== "" && Number.isInteger(Number(value)) && Number(value) >= 0 && Number(value) <= 2147483647;
  const valid = validInteger(amount) && validInteger(count) && reason.trim().length <= 1000;
  const versionReady = conflict ? Boolean(latest?.updated_at && confirmedLatest) : Boolean(expectedUpdatedAt);
  const save = useMutation({
    mutationFn: () => {
      if (!valid || !versionReady) throw new Error("請填寫非負整數，並核對最新股籍版本");
      return apiFetch<ShareholdingRecord>(endpoint, {
        method: "PATCH",
        body: JSON.stringify({
          share_capital_amount: Number(amount),
          share_count: Number(count),
          reason: reason.trim(),
          expected_updated_at: conflict ? latest?.updated_at : expectedUpdatedAt,
        }),
      });
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["admin-member-roster"] }),
        queryClient.invalidateQueries({ queryKey: ["admin-members"] }),
      ]);
      setOpen(false);
      setSaved(true);
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) {
        setConflict(true);
        setLatest(null);
        setConfirmedLatest(false);
      }
    },
  });
  const reload = useMutation({
    mutationFn: async () => {
      const [roster, members] = await Promise.all([
        queryClient.fetchQuery({ queryKey: ["admin-member-roster"], queryFn: () => apiFetch<MemberRosterEntry[]>("/v1/admin/member-roster"), staleTime: 0 }),
        queryClient.fetchQuery({ queryKey: ["admin-members"], queryFn: () => apiFetch<AdminMembership[]>("/v1/admin/members"), staleTime: 0 }),
      ]);
      const refreshed = (source === "roster" ? roster : members).find((item) => item.id === record.id);
      if (!refreshed?.updated_at) throw new Error("找不到可核對的最新股籍版本，請稍後重讀或聯絡系統管理者");
      return refreshed;
    },
    onSuccess: (refreshed) => { setLatest(refreshed); setConfirmedLatest(false); },
  });
  const begin = () => {
    setAmount(String(record.share_capital_amount)); setCount(String(record.share_count));
    setReason(""); setExpectedUpdatedAt(record.updated_at); setConflict(false); setLatest(null);
    setConfirmedLatest(false); setSaved(false); save.reset(); reload.reset(); setOpen(true);
  };
  return <div>
    {!open && <button type="button" disabled={!record.updated_at} onClick={begin}>編輯股金／股數</button>}
    {!record.updated_at && <p className="field-help">暫無股籍版本資訊，請重新讀取名冊後再編輯。</p>}
    {saved && <p className="form-success" role="status">股籍已更新；未收款或退款。</p>}
    {open && <form aria-label="修改股金與股數" onSubmit={(event) => { event.preventDefault(); if (valid && versionReady) save.mutate(); }}>
      <p role="note">僅修正股籍，不會收款或退款。已認領社員的名冊與會籍會同步更新，社員端不顯示股金與股數。</p>
      <label className="field"><span>股金（元）</span><input required type="number" min={0} max={2147483647} step={1} disabled={save.isPending} value={amount} onChange={(event) => setAmount(event.target.value)} /></label>
      <label className="field"><span>股數</span><input required type="number" min={0} max={2147483647} step={1} disabled={save.isPending} value={count} onChange={(event) => setCount(event.target.value)} /></label>
      <label className="field"><span>修改原因<OptionalMark /></span><textarea aria-label="修改原因" maxLength={1000} rows={3} disabled={save.isPending} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
      {(!validInteger(amount) || !validInteger(count)) && <p className="form-error">股金與股數須為 0 至 2147483647 的整數。</p>}
      {save.isError && <p className="form-error" role="alert">{save.error.message}</p>}
      {conflict && <div>
        <p className="form-error">股籍資料已變更或連結需核對。你的輸入已保留，請先重新讀取，確認最新資料後再決定是否送出。</p>
        <button type="button" disabled={reload.isPending || save.isPending} onClick={() => { setLatest(null); setConfirmedLatest(false); reload.mutate(); }}>{reload.isPending ? "重新讀取中…" : "重新讀取最新股籍"}</button>
        {latest && <><p>最新股金 {latest.share_capital_amount} 元 · {latest.share_count} 股</p><label className="meal-choice"><input type="checkbox" checked={confirmedLatest} disabled={save.isPending} onChange={(event) => setConfirmedLatest(event.target.checked)} /><span>我已核對最新股籍，確認以目前輸入修正</span></label></>}
        {reload.isError && <p className="form-error" role="alert">{reload.error.message}</p>}
      </div>}
      <div className="form-actions"><button type="button" disabled={save.isPending || reload.isPending} onClick={() => setOpen(false)}>取消編輯</button><button type="submit" disabled={!valid || !versionReady || save.isPending || reload.isPending}>{save.isPending ? "儲存中…" : "儲存股籍修正"}</button></div>
    </form>}
  </div>;
}

function ProposalReviewCard({ proposal, onDone }: { proposal: Proposal; onDone: () => void }) {
  const [minimumVoters, setMinimumVoters] = useState(10);
  const [discussionEndsAt, setDiscussionEndsAt] = useState("");
  const [votingEndsAt, setVotingEndsAt] = useState("");
  const action = useMutation({
    mutationFn: ({decision, reason}: {decision: "approve" | "reject" | "close"; reason: string}) => apiFetch(`/v1/admin/member-proposals/${proposal.id}/${decision}`, {
      method: "POST",
      body: JSON.stringify(decision === "approve" ? { minimum_voters: minimumVoters, discussion_ends_at: new Date(discussionEndsAt).toISOString(), voting_ends_at: new Date(votingEndsAt).toISOString() } : { reason }),
    }),
    onSuccess: onDone,
  });
  return <article className="admin-review-card"><div className="review-card-copy"><span className="status-chip">{proposalStatusLabel(proposal.status)}</span><h3>{proposal.title}</h3><p>提案人 {proposal.created_by_name} · {proposal.tally.total} 票</p><small>{proposal.body}</small></div><div className="review-card-actions schedule-fields">{proposal.status === "pending_review" && <><label className="field"><span>討論截止</span><input type="datetime-local" value={discussionEndsAt} onChange={(event) => setDiscussionEndsAt(event.target.value)} /></label><label className="field"><span>投票截止</span><input type="datetime-local" value={votingEndsAt} onChange={(event) => setVotingEndsAt(event.target.value)} /></label><label className="field"><span>最低投票數</span><input min={1} type="number" value={minimumVoters} onChange={(event) => setMinimumVoters(Number(event.target.value))} /></label></>}<div>{proposal.status === "pending_review" && <SocialActionButton disabled={!discussionEndsAt || !votingEndsAt || action.isPending} action="approve" showReason={false} onConfirm={reason => action.mutate({decision: "approve", reason})}><CheckCircle size={16} />核准排程</SocialActionButton>}{proposal.status === "pending_review" && <SocialActionButton disabled={action.isPending} action="reject" showReason={true} onConfirm={reason => action.mutate({decision: "reject", reason})}>駁回</SocialActionButton>}{["passed", "rejected"].includes(proposal.status) && <SocialActionButton disabled={action.isPending} action="close" showReason={true} onConfirm={reason => action.mutate({decision: "close", reason})}>完成結案</SocialActionButton>}<Link to="/proposals/$proposalId" params={{ proposalId: proposal.id }}>查看提案</Link></div>{action.isSuccess && <p role="status">處理完成</p>}{action.isError && <p className="form-error" role="alert">{action.error.message}</p>}</div></article>;
}

function MembershipReviewCard({ application, approvalBlockedReason, onDone }: { application: MembershipApplication; approvalBlockedReason: string; onDone: () => void }) {
  const action = useMutation({ mutationFn: ({decision, reason}: {decision: "approve" | "request-supplement" | "reject"; reason: string}) => {
    if (decision === "approve" && approvalBlockedReason) throw new Error(approvalBlockedReason);
    return apiFetch(`/v1/admin/membership-applications/${application.id}/${decision}`, { method: "POST", body: JSON.stringify({ reason: reason || null }) });
  }, onSuccess: onDone });
  const openDocument = useMutation({
    mutationFn: (documentId: string) => {
      const document = application.documents.find(item => item.id === documentId);
      if (document?.is_demo_placeholder === true) throw new Error(documentRetentionLabel(document));
      return apiFetch<{ download_url: string }>(`/v1/admin/membership-applications/${application.id}/documents/${documentId}/download-url`);
    },
    onSuccess: ({ download_url }) => window.open(download_url, "_blank", "noopener,noreferrer"),
  });
  const reviewable = ["submitted", "needs_supplement"].includes(application.status);
  const availableDocuments = application.documents.filter(documentAvailable);
  const expiredDocuments = application.documents.some(documentExpired);
  return (
    <article className="admin-review-card">
      <div className="review-card-copy">
        <span className="status-chip">{applicationAdminStatus(application.status)}</span>
        <h3>{application.profile?.legal_name || "未填姓名"}</h3>
        <p>{application.profile?.phone}</p>
        <small>{application.profile?.address}</small>
        {application.documents.length > 0 && <p role="note">歷史證件 {availableDocuments.length} 份可調閱；目前申請不需證件。{expiredDocuments && "到期檔案停止調閱，依既有留存政策處理。"}</p>}
        <div className="document-review-links">
          {application.documents.map((document) => (
            <div key={document.id}>
              <button type="button" disabled={openDocument.isPending || !documentAvailable(document)} onClick={() => openDocument.mutate(document.id)}>{documentTypeLabel(document.document_type)}</button>
              <small>{documentRetentionLabel(document)}</small>
              {document.deletion_error && document.deletion_retry_at && <small>下次清除重試：{formatDateTime(document.deletion_retry_at)}</small>}
            </div>
          ))}
        </div>
      </div>
      <div className="review-card-actions">

        <div>
          {application.status === "submitted" && <SocialActionButton disabled={action.isPending || Boolean(approvalBlockedReason)} action="approve" showReason={false} onConfirm={reason => action.mutate({decision: "approve", reason})}><CheckCircle size={16} />{approvalBlockedReason ? "暫不可核准" : "核准"}</SocialActionButton>}
          {application.status === "submitted" && <SocialActionButton disabled={action.isPending} action="request-supplement" showReason={true} onConfirm={reason => action.mutate({decision: "request-supplement", reason})}>要求補件</SocialActionButton>}
          {reviewable && <SocialActionButton disabled={action.isPending} action="reject" showReason={true} onConfirm={reason => action.mutate({decision: "reject", reason})}>駁回</SocialActionButton>}
        </div>
        {action.isSuccess && <p role="status">處理完成</p>}{(action.isError || openDocument.isError) && <p className="form-error">{action.error?.message || openDocument.error?.message}</p>}
      </div>
    </article>
  );
}

function MembershipStatusCard({ membership, onDone }: { membership: AdminMembership; onDone: () => void }) {
  const action = useMutation({
    mutationFn: ({nextAction, reason}: {nextAction: "activate" | "suspend" | "resign" | "terminate" | "share-capital-return"; reason: string}) => apiFetch(`/v1/admin/members/${membership.id}/${nextAction}`, { method: "POST", body: JSON.stringify({ reason }) }),
    onSuccess: onDone,
  });
  return <article className="admin-review-card"><div className="review-card-copy"><span className="status-chip">{membershipStatusLabel(membership.status)}</span><h3>{membership.display_name && `${membership.display_name} · `}{membership.member_number || membership.trainee_number || "待編號"}</h3><p>股金 {membership.share_capital_amount} 元 · {membership.share_count} 股</p><details><summary>會籍詳細資料</summary><small>會籍 ID {membership.id}</small></details></div><div className="review-card-actions"><div>{membership.status === "trainee" && <SocialActionButton disabled={action.isPending} action="activate" showReason={true} onConfirm={reason => action.mutate({nextAction: "activate", reason})}><CheckCircle size={16} />轉為正式社員</SocialActionButton>}{membership.status === "active" && <SocialActionButton disabled={action.isPending} action="suspend" showReason={true} onConfirm={reason => action.mutate({nextAction: "suspend", reason})}>暫停會籍</SocialActionButton>}{["active", "suspended"].includes(membership.status) && <SocialActionButton disabled={action.isPending} action="resign" showReason={true} onConfirm={reason => action.mutate({nextAction: "resign", reason})}>辦理退社</SocialActionButton>}{["trainee", "active", "suspended", "resigned"].includes(membership.status) && <SocialActionButton disabled={action.isPending} action="terminate" showReason={true} onConfirm={reason => action.mutate({nextAction: "terminate", reason})}>終止會籍</SocialActionButton>}{membership.status === "resigned" && <SocialActionButton disabled={action.isPending} action="share-capital-return" showReason={true} onConfirm={reason => action.mutate({nextAction: "share-capital-return", reason})}>返還股金</SocialActionButton>}</div>{action.isSuccess && <p role="status">處理完成</p>}{action.isError && <p className="form-error" role="alert">{action.error.message}</p>}<ShareholdingEditor record={membership} source="membership" /></div></article>;
}

function MeetingAdminCard({ meeting, memberships, proposals, onDone }: { meeting: Meeting; memberships: AdminMembership[]; proposals: Proposal[]; onDone: () => void }) {
  const [manage, setManage] = useState(false);
  const [memberId, setMemberId] = useState("");
  const [resolution, setResolution] = useState({ title: "", resolution_text: "", member_proposal_id: "" });
  const attendance = useMutation({
    mutationFn: (attended: boolean) => apiFetch(`/v1/admin/meetings/${meeting.id}/attendance`, { method: "PUT", body: JSON.stringify({ member_id: memberId, attended }) }),
    onSuccess: onDone,
  });
  const createResolution = useMutation({
    mutationFn: () => apiFetch(`/v1/admin/meetings/${meeting.id}/resolutions`, { method: "POST", body: JSON.stringify({ ...resolution, member_proposal_id: resolution.member_proposal_id || null }) }),
    onSuccess: () => {
      setResolution({ title: "", resolution_text: "", member_proposal_id: "" });
      onDone();
    },
  });
  const activeMemberships = memberships.filter((membership) => membership.status === "active");
  return (
    <article className="admin-review-card admin-review-card-expandable meeting-admin-card">
      <div className="review-card-copy"><span className="status-chip">{meetingCategoryName(meeting)}</span><h3>{meeting.title}</h3><p>{formatDateTime(meeting.starts_at)} · {meeting.location}</p><small>{meeting.resolutions.length} 項決議</small></div>
      <div className="meeting-admin-summary"><strong>{Math.round(meeting.attendance_rate * 100)}%</strong><small>{meeting.attended_count}/{meeting.eligible_member_count} 人</small><button type="button" onClick={() => setManage((value) => !value)}>{manage ? "收起管理" : "出席與決議"}</button></div>
      {manage && (
        <div className="meeting-admin-tools">
          <section>
            <h4>出席登記</h4>
            <label className="field"><span>正式社員</span><select value={memberId} onChange={(event) => setMemberId(event.target.value)}><option value="">選擇社員</option>{activeMemberships.map((membership) => <option key={membership.id} value={membership.user_id}>{membership.member_number || membership.user_id}</option>)}</select></label>
            <div><button type="button" disabled={!memberId || attendance.isPending} onClick={() => attendance.mutate(true)}>登記出席</button><button type="button" disabled={!memberId || attendance.isPending} onClick={() => attendance.mutate(false)}>取消出席</button></div>
          </section>
          <section>
            <h4>新增決議</h4>
            <label className="field"><span>決議主旨</span><input value={resolution.title} onChange={(event) => setResolution({ ...resolution, title: event.target.value })} /></label>
            <label className="field"><span>關聯提案</span><select value={resolution.member_proposal_id} onChange={(event) => setResolution({ ...resolution, member_proposal_id: event.target.value })}><option value="">不關聯提案</option>{proposals.map((proposal) => <option key={proposal.id} value={proposal.id}>{proposal.title}</option>)}</select></label>
            <label className="field"><span>決議內容</span><textarea rows={4} value={resolution.resolution_text} onChange={(event) => setResolution({ ...resolution, resolution_text: event.target.value })} /></label>
            <button type="button" disabled={!resolution.title.trim() || !resolution.resolution_text.trim() || createResolution.isPending} onClick={() => createResolution.mutate()}>儲存決議</button>
          </section>
          {(attendance.isError || createResolution.isError) && <p className="form-error">{attendance.error?.message || createResolution.error?.message}</p>}
          {meeting.resolutions.length > 0 && <div className="meeting-resolution-admin-list">{meeting.resolutions.map((item) => <p key={item.id}><strong>{item.title}</strong><span>{item.resolution_text}</span></p>)}</div>}
        </div>
      )}
    </article>
  );
}

function activityAdminStatus(status: string) {
  return { pending_review: "待審核", published: "已發布", rejected: "已駁回", cancelled: "已取消", completed: "已完成", draft: "草稿" }[status] || status;
}

function registrationStatusLabel(status: string) {
  return { registered: "已報名", waitlisted: "候補", cancelled: "已取消", attended: "已出席", no_show: "未出席" }[status] || status;
}

function applicationAdminStatus(status: string) {
  return { draft: "填寫中", submitted: "待審核", needs_supplement: "待補件", approved: "已核准", rejected: "已駁回", withdrawn: "已撤回" }[status] || status;
}

function documentTypeLabel(type: string) {
  return { id_front: "身分證正面", id_back: "身分證反面", secondary: "第二證件" }[type] || type;
}

function membershipStatusLabel(status: string) {
  return { pending_payment: "待繳款", trainee: "實習社員", active: "正式社員", suspended: "暫停", resigned: "已退社", terminated: "已終止" }[status] || status;
}
