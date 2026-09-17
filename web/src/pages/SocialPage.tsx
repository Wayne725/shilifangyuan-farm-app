import {
  ArrowRight,
  CalendarBlank,
  CalendarCheck,
  IdentificationCard,
  NotePencil,
  UsersThree,
} from "@phosphor-icons/react";
import { useQueries, useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { SocialNav } from "../components/SocialNav";
import { DataState, LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import { apiFetch, formatDate } from "../lib/api";
import { proposalStatusLabel } from "../lib/labels";
import type { Activity, Meeting, MembershipSummary, Proposal } from "../lib/types";

export function SocialPage() {
  const { user, openLogin } = useAuth();
  const membership = useQuery({
    queryKey: ["membership-me", user?.id],
    queryFn: () => apiFetch<MembershipSummary>("/v1/members/me"),
    enabled: Boolean(user),
  });
  const isMember = membership.data?.membership_type === "member";
  const [activities, proposals, meetings] = useQueries({
    queries: [
      {
        queryKey: ["activities"],
        queryFn: () => apiFetch<Activity[]>("/v1/activities"),
        enabled: Boolean(isMember),
      },
      {
        queryKey: ["member-proposals"],
        queryFn: () => apiFetch<Proposal[]>("/v1/member-proposals"),
        enabled: Boolean(isMember),
      },
      {
        queryKey: ["meetings"],
        queryFn: () => apiFetch<Meeting[]>("/v1/meetings"),
        enabled: Boolean(isMember),
      },
    ],
  });

  return (
    <>
      <section className="page-intro social-intro compact-intro">
        <div>
          <p className="eyebrow">CO-OP COMMONS / 社務系統</p>
          <h1>一起參與，<br /><em>一起決定。</em></h1>
        </div>
        <div className="intro-aside">
          <span>活動、願望、社員名錄與合作社議事。</span>
        </div>
      </section>
      <SocialNav />

      {!user ? (
        <section className="social-access-card">
          <UsersThree size={38} weight="light" />
          <h2>登入後進入社務系統</h2>
          <p>一般買家可查看入社進度；正式社員可參與活動、願望、名錄與議事。</p>
          <button className="button button-primary" type="button" onClick={openLogin}>登入帳號</button>
        </section>
      ) : membership.isPending ? (
        <section className="content-section"><LoadingLines count={3} /></section>
      ) : !isMember ? (
        <section className="social-access-card membership-access">
          <IdentificationCard size={38} weight="light" />
          <p className="eyebrow">MEMBERSHIP</p>
          <h2>{membership.data?.membership_type === "trainee" ? "實習社員資格已啟用" : "開始入社程序"}</h2>
          <p>
            {membership.data?.membership_type === "trainee"
              ? "完成線下流程並由管理員轉正後，即可使用完整社務功能。"
              : "填寫入社資料、送出申請並完成款項後，合作社會接續審核。"}
          </p>
          <Link className="button button-system" to="/membership">查看入社資料</Link>
        </section>
      ) : (
        <>
          <section className="social-identity-strip">
            <div className="member-monogram">{user.display_name.slice(0, 1)}</div>
            <div><small>正式社員</small><strong>{user.display_name}</strong></div>
            <dl>
              <div><dt>社員編號</dt><dd>{membership.data?.membership?.member_number}</dd></div>
              <div><dt>公開名錄</dt><dd>{membership.data?.directory?.is_public ? "已公開" : "未公開"}</dd></div>
            </dl>
          </section>

          <section className="social-dashboard content-section">
            <div className="social-feature-grid">
              <Link className="social-feature primary" to="/activities">
                <CalendarBlank size={28} weight="light" />
                <span>社員活動</span>
                <strong>{activities.data?.length ?? 0}</strong>
                <small>場近期活動</small>
                <ArrowRight size={20} />
              </Link>
              <Link className="social-feature" to="/governance">
                <NotePencil size={28} weight="light" />
                <span>社員提案</span>
                <strong>{proposals.data?.length ?? 0}</strong>
                <small>件討論與表決</small>
                <ArrowRight size={20} />
              </Link>
              <Link className="social-feature" to="/directory">
                <IdentificationCard size={28} weight="light" />
                <span>社員名錄</span>
                <strong>{membership.data?.directory?.is_public ? "ON" : "OFF"}</strong>
                <small>你的公開狀態</small>
                <ArrowRight size={20} />
              </Link>
              <Link className="social-feature" to="/meetings">
                <CalendarCheck size={28} weight="light" />
                <span>社員會議</span>
                <strong>{meetings.data?.length ?? 0}</strong>
                <small>場會議與紀錄</small>
                <ArrowRight size={20} />
              </Link>
            </div>

            <div className="social-feed-grid">
              <section className="social-feed">
                <header><div><p className="eyebrow">UPCOMING</p><h2>近期活動</h2></div><Link className="text-link" to="/activities">查看全部</Link></header>
                {activities.isPending && <LoadingLines count={2} />}
                {activities.data?.slice(0, 3).map((activity) => (
                  <article key={activity.id}>
                    <time>{formatDate(activity.starts_at)}</time>
                    <div><strong>{activity.title}</strong><small>{activity.location} · {activity.registration_count}/{activity.capacity} 人</small></div>
                    <span>{activity.my_registration ? "已報名" : "可報名"}</span>
                  </article>
                ))}
                {activities.data?.length === 0 && <DataState title="目前沒有近期活動" detail="社員可發起新的活動送交管理員審核。" />}
              </section>

              <section className="social-feed governance-feed">
                <header><div><p className="eyebrow">PROPOSALS</p><h2>提案進度</h2></div><Link className="text-link" to="/governance">查看提案</Link></header>
                {proposals.data?.slice(0, 3).map((proposal) => (
                  <Link key={proposal.id} to="/proposals/$proposalId" params={{ proposalId: proposal.id }}>
                    <span>{proposalStatusLabel(proposal.status)}</span>
                    <div><strong>{proposal.title}</strong><small>提案人 {proposal.created_by_name}</small></div>
                    <b>{proposal.tally.total}</b>
                  </Link>
                ))}
              </section>
              <section className="social-feed">
                <header><div><p className="eyebrow">MEETINGS</p><h2>社員會議</h2></div><Link className="text-link" to="/meetings">查看會議</Link></header>
                {meetings.isPending && <LoadingLines count={2} />}
                {meetings.isError && <DataState kind="error" title="會議暫時無法讀取" detail={meetings.error.message} />}
                {meetings.data?.length === 0 && <DataState title="目前沒有會議紀錄" detail="合作社公告後會顯示在這裡。" />}
                {meetings.data?.slice(0, 3).map((meeting) => (
                  <article key={meeting.id}>
                    <time>{formatDate(meeting.starts_at)}</time>
                    <div><strong>{meeting.title}</strong><small>{meeting.location}</small></div>
                  </article>
                ))}
              </section>
            </div>
          </section>
        </>
      )}
    </>
  );
}
