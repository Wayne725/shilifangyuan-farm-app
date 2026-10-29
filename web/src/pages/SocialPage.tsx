import {
  ArrowRight,
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
import { meetingAccessDenied, retryMeetingRead } from "../lib/meetings";
import { proposalStatusLabel } from "../lib/labels";
import type { Meeting, MembershipSummary, Proposal } from "../lib/types";

export function SocialPage() {
  const { user, openLogin } = useAuth();
  const membership = useQuery({
    queryKey: ["membership-me", user?.id],
    queryFn: () => apiFetch<MembershipSummary>("/v1/members/me"),
    enabled: Boolean(user),
  });
  const isMember = membership.data?.membership_type === "member";
  const canReadMeetings = isMember && membership.data?.membership?.status === "active";
  const [proposals, meetings] = useQueries({
    queries: [
      {
        queryKey: ["member-proposals"],
        queryFn: () => apiFetch<Proposal[]>("/v1/member-proposals"),
        enabled: Boolean(isMember),
      },
      {
        queryKey: ["meetings", user?.id],
        queryFn: () => apiFetch<Meeting[]>("/v1/meetings"),
        enabled: canReadMeetings,
        retry: retryMeetingRead,
      },
    ],
  });

  const visibleMeetings = canReadMeetings && !meetings.isError ? meetings.data : undefined;

  return (
    <>
      <section className="page-intro social-intro compact-intro">
        <div>
          <p className="eyebrow">CO-OP COMMONS / 社務系統</p>
          <h1>一起參與，<br /><em>一起決定。</em></h1>
        </div>
        <div className="intro-aside">
          <span>願望、社員名錄與合作社議事。</span>
        </div>
      </section>
      {isMember && <SocialNav />}

      {!user ? (
        <section className="social-access-card">
          <UsersThree size={38} weight="light" />
          <h2>登入後進入社務系統</h2>
          <p>一般買家可查看入社進度；正式社員可參與願望、名錄與議事。</p>
          <button className="button button-primary" type="button" onClick={openLogin}>登入帳號</button>
        </section>
      ) : membership.isPending ? (
        <section className="content-section"><LoadingLines count={3} /></section>
      ) : !isMember ? (
        <section className="social-access-card membership-access">
          <IdentificationCard size={38} weight="light" />
          <p className="eyebrow">MEMBERSHIP / 社員資格</p>
          <h2>{membership.data?.membership_type === "trainee" ? "實習社員資格已啟用" : "開始入社程序"}</h2>
          <p>
            {membership.data?.membership_type === "trainee"
              ? "完成線下流程並由管理員轉正後，即可使用完整社務功能。"
              : "填妥基本資料後，可送出申請並查看處理進度。"}
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
              <Link className="social-feature primary" to="/governance">
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
                <span>各項會議</span>
                <strong>{visibleMeetings?.length ?? "—"}</strong>
                <small>場會議與紀錄</small>
                <ArrowRight size={20} />
              </Link>
            </div>

            <div className="social-feed-grid">
              <section className="social-feed governance-feed">
                <header><div><p className="eyebrow">PROPOSALS / 社員提案</p><h2>提案進度</h2></div><Link className="text-link" to="/governance">查看提案</Link></header>
                {proposals.data?.slice(0, 3).map((proposal) => (
                  <Link key={proposal.id} to="/proposals/$proposalId" params={{ proposalId: proposal.id }}>
                    <span>{proposalStatusLabel(proposal.status)}</span>
                    <div><strong>{proposal.title}</strong><small>提案人 {proposal.created_by_name}</small></div>
                    <b>{proposal.tally.total}</b>
                  </Link>
                ))}
              </section>
              <section className="social-feed">
                <header><div><p className="eyebrow">MEETINGS / 各項會議</p><h2>各項會議</h2></div><Link className="text-link" to="/meetings">查看會議</Link></header>
                {canReadMeetings && meetings.isPending && <LoadingLines count={2} />}
                {!canReadMeetings || meetingAccessDenied(meetings.error) ? <DataState title="各項會議限有效正式社員查閱" detail="" /> : meetings.isError && <DataState kind="error" title="會議暫時無法讀取" detail={meetings.error.message} />}
                {visibleMeetings?.length === 0 && <DataState title="目前沒有會議紀錄" detail="合作社公告後會顯示在這裡。" />}
                {visibleMeetings?.slice(0, 3).map((meeting) => (
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
