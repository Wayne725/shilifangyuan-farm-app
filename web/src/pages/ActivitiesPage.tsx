import { CalendarBlank, MapPin, Plus, UsersThree } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Link } from "@tanstack/react-router";

import { SocialNav } from "../components/SocialNav";
import { DataState, LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import { apiFetch, formatDateTime } from "../lib/api";
import type { Activity, MembershipSummary } from "../lib/types";

const emptyForm = {
  title: "",
  description: "",
  location: "",
  starts_at: "",
  ends_at: "",
  registration_deadline: "",
  capacity: 20,
  waitlist_enabled: true,
};

export function ActivitiesPage() {
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
  const activities = useQuery({
    queryKey: ["activities"],
    queryFn: () => apiFetch<Activity[]>("/v1/activities"),
    enabled: canAccess,
  });
  const registration = useMutation({
    mutationFn: ({ id, cancel }: { id: string; cancel: boolean }) =>
      apiFetch(`/v1/activities/${id}/${cancel ? "cancel-registration" : "register"}`, { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["activities"] }),
  });
  const createActivity = useMutation({
    mutationFn: () => apiFetch<Activity>("/v1/activities", {
      method: "POST",
      body: JSON.stringify({
        ...form,
        starts_at: new Date(form.starts_at).toISOString(),
        ends_at: new Date(form.ends_at).toISOString(),
        registration_deadline: new Date(form.registration_deadline).toISOString(),
      }),
    }),
    onSuccess: () => {
      setForm(emptyForm);
      setShowForm(false);
      queryClient.invalidateQueries({ queryKey: ["activities"] });
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    createActivity.mutate();
  }

  return (
    <section className="social-page-shell">
      <header className="social-page-heading">
        <div><p className="eyebrow">MEMBER ACTIVITIES</p><h1>社員活動</h1></div>
        {canAccess && <button className="button button-system" type="button" onClick={() => setShowForm((value) => !value)}><Plus size={18} />發起活動</button>}
      </header>
      <SocialNav />

      {!user ? (
        <AccessCard title="登入後查看社員活動" action="登入帳號" onAction={openLogin} />
      ) : membership.isPending ? <LoadingLines count={3} /> : !canAccess ? (
        <div className="social-access-card"><UsersThree size={36} weight="light" /><h2>此功能開放給正式社員</h2><Link className="button button-system" to="/membership">查看入社進度</Link></div>
      ) : (
        <>
          {showForm && (
            <form className="social-form" onSubmit={submit}>
              <div className="form-heading"><div><p className="eyebrow">NEW ACTIVITY</p><h2>發起社員活動</h2></div><span>送出後由管理員審核</span></div>
              <div className="field-grid two-columns">
                <label className="field wide"><span>活動名稱</span><input required maxLength={160} value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} /></label>
                <label className="field"><span>地點</span><input required maxLength={240} value={form.location} onChange={(event) => setForm({ ...form, location: event.target.value })} /></label>
                <label className="field"><span>名額</span><input required min={1} max={100000} type="number" value={form.capacity} onChange={(event) => setForm({ ...form, capacity: Number(event.target.value) })} /></label>
                <label className="field"><span>開始時間</span><input required type="datetime-local" value={form.starts_at} onChange={(event) => setForm({ ...form, starts_at: event.target.value })} /></label>
                <label className="field"><span>結束時間</span><input required type="datetime-local" value={form.ends_at} onChange={(event) => setForm({ ...form, ends_at: event.target.value })} /></label>
                <label className="field"><span>報名截止</span><input required type="datetime-local" value={form.registration_deadline} onChange={(event) => setForm({ ...form, registration_deadline: event.target.value })} /></label>
                <label className="check-field"><input type="checkbox" checked={form.waitlist_enabled} onChange={(event) => setForm({ ...form, waitlist_enabled: event.target.checked })} /><span>額滿後開放候補</span></label>
                <label className="field wide"><span>活動內容</span><textarea rows={5} maxLength={10000} value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} /></label>
              </div>
              {createActivity.isError && <p className="form-error">{createActivity.error.message}</p>}
              {createActivity.isSuccess && <p className="form-success">活動已送交審核。</p>}
              <div className="form-actions"><button className="button button-quiet" type="button" onClick={() => setShowForm(false)}>取消</button><button className="button button-primary" disabled={createActivity.isPending}>送出審核</button></div>
            </form>
          )}

          {activities.isPending && <LoadingLines count={4} />}
          {activities.isError && <DataState kind="error" title="活動暫時無法讀取" detail={activities.error.message} />}
          {activities.data?.length === 0 && <DataState title="目前沒有已發布活動" detail="社員發起的活動通過審核後會顯示在這裡。" />}
          <div className="activity-list">
            {activities.data?.map((activity) => {
              const activeRegistration = activity.my_registration && activity.my_registration.status !== "cancelled";
              return (
                <article key={activity.id}>
                  <div className="activity-calendar"><CalendarBlank size={21} weight="light" /><time>{formatDateTime(activity.starts_at)}</time></div>
                  <div className="activity-main"><span className="status-chip">{activityStatusLabel(activity.status)}</span><h2>{activity.title}</h2><p>{activity.description || "活動內容將由發起人補充。"}</p><div><span><MapPin size={15} />{activity.location}</span><span><UsersThree size={15} />{activity.registration_count}/{activity.capacity} 人{activity.waitlist_count > 0 ? ` · 候補 ${activity.waitlist_count}` : ""}</span></div></div>
                  <div className="activity-cta"><small>報名至 {formatDateTime(activity.registration_deadline)}</small><button className={`button ${activeRegistration ? "button-quiet" : "button-system"}`} type="button" disabled={registration.isPending} onClick={() => registration.mutate({ id: activity.id, cancel: Boolean(activeRegistration) })}>{activeRegistration ? activity.my_registration?.status === "waitlisted" ? "取消候補" : "取消報名" : "立即報名"}</button></div>
                </article>
              );
            })}
          </div>
          {registration.isError && <p className="form-error">{registration.error.message}</p>}
        </>
      )}
    </section>
  );
}

function AccessCard({ title, action, onAction }: { title: string; action: string; onAction: () => void }) {
  return <div className="social-access-card"><UsersThree size={36} weight="light" /><h2>{title}</h2><button className="button button-primary" type="button" onClick={onAction}>{action}</button></div>;
}

function activityStatusLabel(status: string) {
  return { published: "報名中", pending_review: "待審核", completed: "已完成", cancelled: "已取消" }[status] || status;
}
