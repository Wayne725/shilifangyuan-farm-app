import { CalendarCheck, UsersThree } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "@tanstack/react-router";

import { MeetingFilters } from "../components/MeetingTools";
import { filterMeetings, meetingAccessDenied, meetingCategoryName, meetingCategoryOptions, retryMeetingRead } from "../lib/meetings";
import { SocialNav } from "../components/SocialNav";
import { MembershipReadError } from "../components/MembershipReadError";
import { DataState, LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import { apiFetch, formatDateTime } from "../lib/api";
import type { Meeting, MeetingCategory, MembershipSummary } from "../lib/types";

export function MeetingsPage() {
  const { user, openLogin } = useAuth();
  const [category, setCategory] = useState("");
  const [keyword, setKeyword] = useState("");
  const membership = useQuery({
    queryKey: ["membership-me", user?.id],
    queryFn: () => apiFetch<MembershipSummary>("/v1/members/me"),
    enabled: Boolean(user),
  });
  const canAccess = membership.data?.membership_type === "member" && membership.data?.membership?.status === "active";
  const meetings = useQuery({
    queryKey: ["meetings", user?.id],
    queryFn: () => apiFetch<Meeting[]>("/v1/meetings"),
    enabled: canAccess,
    retry: retryMeetingRead,
  });

  const categories = useQuery({
    queryKey: ["meeting-categories", user?.id],
    queryFn: () => apiFetch<MeetingCategory[]>("/v1/meeting-categories"),
    enabled: canAccess,
    retry: retryMeetingRead,
  });
  const denied = [meetings.error, categories.error].some(meetingAccessDenied);
  const visibleMeetings = filterMeetings(meetings.data || [], category, keyword);

  return (
    <section className="social-page-shell">
      <header className="social-page-heading">
        <div><p className="eyebrow">MEETINGS / 各項會議</p><h1>各項會議</h1></div>
      </header>
      <SocialNav />
      {!user ? (
        <div className="social-access-card">
          <CalendarCheck size={38} weight="light" />
          <h2>登入後查看各項會議</h2>
          <button className="button button-primary" type="button" onClick={openLogin}>登入帳號</button>
        </div>
      ) : membership.isPending ? <LoadingLines count={3} /> : membership.isError ? (
        <MembershipReadError error={membership.error} onRetry={() => void membership.refetch()} />
      ) : !canAccess || denied ? (
        <div className="social-access-card">
          <UsersThree size={38} weight="light" />
          <h2>此功能開放給正式社員</h2>
          <Link className="button button-system" to="/membership">查看入社進度</Link>
        </div>
      ) : (
        <section className="meeting-panel">
          <div className="section-title-row"><div><p className="eyebrow">MEETINGS / 各項會議</p><h2>會議與紀錄</h2></div><CalendarCheck size={27} weight="light" /></div>
          <MeetingFilters categories={meetingCategoryOptions(meetings.data || [], categories.data || [])} categoryId={category} keyword={keyword} onCategory={setCategory} onKeyword={setKeyword} />
          {categories.isError && <DataState kind="error" title="會議類別暫時無法讀取" detail={categories.error.message} />}
          {meetings.isPending && <LoadingLines count={3} />}
          {meetings.isError && <DataState kind="error" title="會議暫時無法讀取" detail={meetings.error.message} />}
          {!meetings.isPending && !meetings.isError && visibleMeetings.length === 0 && <DataState title={category || keyword ? "沒有符合條件的會議" : "目前沒有會議紀錄"} detail="" />}
          {meetings.data && !meetings.isError && <p role="status">共 {visibleMeetings.length} 場會議</p>}
          <div className="meeting-list">
            {!meetings.isError && visibleMeetings.map((meeting) => (
              <article key={meeting.id}>
                <span>{meetingCategoryName(meeting)}</span>
                <h3>{meeting.title}</h3>
                <time>{formatDateTime(meeting.starts_at)} · {meeting.location}</time>
                {meeting.agenda.length > 0 && <ul>{meeting.agenda.map((item, index) => {
                  const title = item.title || item.label || item.subject;
                  return <li key={index}>{typeof title === "string" ? title : `議程 ${index + 1}`}</li>;
                })}</ul>}
                {meeting.resolutions.length > 0 && <div className="meeting-resolutions">{meeting.resolutions.map((resolution) => <p key={resolution.id}><strong>{resolution.title}{resolution.revoked_at && "（已撤銷）"}</strong><span>{resolution.resolution_text}</span></p>)}</div>}
                <div className="meeting-attendance"><strong>{Math.round(meeting.attendance_rate * 100)}%</strong><small>{meeting.attended_count}/{meeting.eligible_member_count} 人出席</small></div>
              </article>
            ))}
          </div>
        </section>
      )}
    </section>
  );
}
