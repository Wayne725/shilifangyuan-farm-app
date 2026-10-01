import { CalendarCheck, UsersThree } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { SocialNav } from "../components/SocialNav";
import { DataState, LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import { apiFetch, formatDateTime } from "../lib/api";
import type { Meeting, MembershipSummary } from "../lib/types";

export function MeetingsPage() {
  const { user, openLogin } = useAuth();
  const membership = useQuery({
    queryKey: ["membership-me", user?.id],
    queryFn: () => apiFetch<MembershipSummary>("/v1/members/me"),
    enabled: Boolean(user),
  });
  const canAccess = membership.data?.membership_type === "member";
  const meetings = useQuery({
    queryKey: ["meetings"],
    queryFn: () => apiFetch<Meeting[]>("/v1/meetings"),
    enabled: canAccess,
  });

  return (
    <section className="social-page-shell">
      <header className="social-page-heading">
        <div><p className="eyebrow">MEMBER MEETINGS</p><h1>社員會議</h1></div>
      </header>
      <SocialNav />
      {!user ? (
        <div className="social-access-card">
          <CalendarCheck size={38} weight="light" />
          <h2>登入後查看社員會議</h2>
          <button className="button button-primary" type="button" onClick={openLogin}>登入帳號</button>
        </div>
      ) : membership.isPending ? <LoadingLines count={3} /> : membership.isError ? (
        <DataState kind="error" title="社員資格暫時無法讀取" detail={membership.error.message} />
      ) : !canAccess ? (
        <div className="social-access-card">
          <UsersThree size={38} weight="light" />
          <h2>此功能開放給正式社員</h2>
          <Link className="button button-system" to="/membership">查看入社進度</Link>
        </div>
      ) : (
        <section className="meeting-panel">
          <div className="section-title-row"><div><p className="eyebrow">MEETINGS</p><h2>會議與紀錄</h2></div><CalendarCheck size={27} weight="light" /></div>
          {meetings.isPending && <LoadingLines count={3} />}
          {meetings.isError && <DataState kind="error" title="會議暫時無法讀取" detail={meetings.error.message} />}
          {meetings.data?.length === 0 && <DataState title="目前沒有會議紀錄" detail="合作社公告會議後會顯示在這裡。" />}
          <div className="meeting-list">
            {meetings.data?.map((meeting) => (
              <article key={meeting.id}>
                <span>{{ general_assembly: "社員大會", affairs: "社務會議" }[meeting.meeting_type] || meeting.meeting_type}</span>
                <h3>{meeting.title}</h3>
                <time>{formatDateTime(meeting.starts_at)} · {meeting.location}</time>
                {meeting.agenda.length > 0 && <ul>{meeting.agenda.map((item, index) => {
                  const title = item.title || item.label || item.subject;
                  return <li key={index}>{typeof title === "string" ? title : `議程 ${index + 1}`}</li>;
                })}</ul>}
                {meeting.resolutions.length > 0 && <div className="meeting-resolutions">{meeting.resolutions.map((resolution) => <p key={resolution.id}><strong>{resolution.title}</strong><span>{resolution.resolution_text}</span></p>)}</div>}
                <div className="meeting-attendance"><strong>{Math.round(meeting.attendance_rate * 100)}%</strong><small>{meeting.attended_count}/{meeting.eligible_member_count} 人出席</small></div>
              </article>
            ))}
          </div>
        </section>
      )}
    </section>
  );
}
