import { CalendarCheck, NotePencil, Plus, UsersThree } from "@phosphor-icons/react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Link } from "@tanstack/react-router";

import { SocialNav } from "../components/SocialNav";
import { DataState, LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import { apiFetch, formatDateTime } from "../lib/api";
import { proposalStatusLabel } from "../lib/labels";
import type { Meeting, MembershipSummary, Proposal } from "../lib/types";

const emptyForm = { title: "", body: "", proposal_type: "resolution" as Proposal["proposal_type"], options: "" };

export function GovernancePage() {
  const { user, openLogin } = useAuth();
  const queryClient = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState(emptyForm);
  const membership = useQuery({
    queryKey: ["membership-me", user?.id],
    queryFn: () => apiFetch<MembershipSummary>("/v1/members/me"),
    enabled: Boolean(user),
  });
  const canAccess = membership.data?.membership_type === "member";
  const [proposals, meetings] = useQueries({ queries: [
    { queryKey: ["member-proposals"], queryFn: () => apiFetch<Proposal[]>("/v1/member-proposals"), enabled: canAccess },
    { queryKey: ["meetings"], queryFn: () => apiFetch<Meeting[]>("/v1/meetings"), enabled: canAccess },
  ] });
  const createProposal = useMutation({
    mutationFn: async () => {
      const options = form.proposal_type === "multiple_choice"
        ? form.options.split("\n").map((label) => label.trim()).filter(Boolean).map((label) => ({ label }))
        : [];
      const proposal = await apiFetch<Proposal>("/v1/member-proposals", {
        method: "POST",
        body: JSON.stringify({ title: form.title, body: form.body, proposal_type: form.proposal_type, options }),
      });
      return apiFetch<Proposal>(`/v1/member-proposals/${proposal.id}/submit`, { method: "POST" });
    },
    onSuccess: () => {
      setForm(emptyForm);
      setShowForm(false);
      queryClient.invalidateQueries({ queryKey: ["member-proposals"] });
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    createProposal.mutate();
  }

  return (
    <section className="social-page-shell">
      <header className="social-page-heading">
        <div><p className="eyebrow">CO-OP GOVERNANCE</p><h1>提案議事</h1></div>
        {canAccess && <button className="button button-system" type="button" onClick={() => setShowForm((value) => !value)}><Plus size={18} />提出提案</button>}
      </header>
      <SocialNav />

      {!user ? (
        <div className="social-access-card"><NotePencil size={38} weight="light" /><h2>登入後進入社員議事</h2><button className="button button-primary" type="button" onClick={openLogin}>登入帳號</button></div>
      ) : membership.isPending ? <LoadingLines count={3} /> : !canAccess ? (
        <div className="social-access-card"><UsersThree size={38} weight="light" /><h2>此功能開放給正式社員</h2><Link className="button button-system" to="/membership">查看入社進度</Link></div>
      ) : (
        <>
          {showForm && (
            <form className="social-form" onSubmit={submit}>
              <div className="form-heading"><div><p className="eyebrow">NEW PROPOSAL</p><h2>提出社員提案</h2></div><span>送出後由管理員安排討論與表決</span></div>
              <div className="field-grid two-columns">
                <label className="field wide"><span>提案主旨</span><input required maxLength={160} value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} /></label>
                <label className="field"><span>表決方式</span><select value={form.proposal_type} onChange={(event) => setForm({ ...form, proposal_type: event.target.value as Proposal["proposal_type"] })}><option value="resolution">贊成／反對／棄權</option><option value="multiple_choice">多選項表決</option></select></label>
                {form.proposal_type === "multiple_choice" && <label className="field"><span>選項（每行一個）</span><textarea required rows={4} placeholder={'選項一\n選項二'} value={form.options} onChange={(event) => setForm({ ...form, options: event.target.value })} /></label>}
                <label className="field wide"><span>提案內容</span><textarea required rows={8} maxLength={20000} value={form.body} onChange={(event) => setForm({ ...form, body: event.target.value })} /></label>
              </div>
              {createProposal.isError && <p className="form-error">{createProposal.error.message}</p>}
              <div className="form-actions"><button className="button button-quiet" type="button" onClick={() => setShowForm(false)}>取消</button><button className="button button-primary" disabled={createProposal.isPending}>送出審核</button></div>
            </form>
          )}

          <div className="governance-layout">
            <section className="governance-proposals">
              <div className="section-title-row"><div><p className="eyebrow">PROPOSALS</p><h2>社員提案</h2></div><span>{proposals.data?.length ?? 0} 件</span></div>
              {proposals.isPending && <LoadingLines count={4} />}
              {proposals.isError && <DataState kind="error" title="提案暫時無法讀取" detail={proposals.error.message} />}
              {proposals.data?.length === 0 && <DataState title="目前沒有提案" detail="正式社員可提出合作社議題送交審核。" />}
              <div className="proposal-list">
                {proposals.data?.map((proposal) => (
                  <Link key={proposal.id} to="/proposals/$proposalId" params={{ proposalId: proposal.id }}>
                    <div><span className={`status-chip ${proposal.status}`}>{proposalStatusLabel(proposal.status)}</span><small>{proposal.proposal_type === "resolution" ? "決議表決" : "選項表決"}</small></div>
                    <h3>{proposal.title}</h3>
                    <p>{proposal.body}</p>
                    <footer><span>提案人 {proposal.created_by_name}</span><strong>{proposal.tally.total} 票</strong></footer>
                  </Link>
                ))}
              </div>
            </section>

            <aside className="meeting-panel">
              <div className="section-title-row"><div><p className="eyebrow">MEETINGS</p><h2>社員會議</h2></div><CalendarCheck size={27} weight="light" /></div>
              {meetings.isPending && <LoadingLines count={3} />}
              {meetings.data?.length === 0 && <DataState title="目前沒有會議紀錄" detail="管理員建立會議後會顯示在這裡。" />}
              <div className="meeting-list">
                {meetings.data?.map((meeting) => (
                  <article key={meeting.id}>
                    <span>{meetingTypeLabel(meeting.meeting_type)}</span>
                    <h3>{meeting.title}</h3>
                    <time>{formatDateTime(meeting.starts_at)} · {meeting.location}</time>
                    {meeting.agenda.length > 0 && <ul>{meeting.agenda.map((item, index) => <li key={index}>{agendaLabel(item, index)}</li>)}</ul>}
                    {meeting.resolutions.length > 0 && <div className="meeting-resolutions">{meeting.resolutions.map((resolution) => <p key={resolution.id}><strong>{resolution.title}</strong><span>{resolution.resolution_text}</span></p>)}</div>}
                    <div className="meeting-attendance"><strong>{Math.round(meeting.attendance_rate * 100)}%</strong><small>{meeting.attended_count}/{meeting.eligible_member_count} 人出席</small></div>
                  </article>
                ))}
              </div>
            </aside>
          </div>
        </>
      )}
    </section>
  );
}

function meetingTypeLabel(type: string) {
  return { general_assembly: "社員大會", affairs: "社務會議" }[type] || type;
}

function agendaLabel(item: Record<string, unknown>, index: number) {
  const title = item.title || item.label || item.subject;
  return typeof title === "string" ? title : `議程 ${index + 1}`;
}
