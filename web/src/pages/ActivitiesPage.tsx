import { CalendarBlank, MapPin, Plus, UsersThree } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useState } from "react";

import { Link } from "@tanstack/react-router";
import { ActivityRegistrationActions } from "../components/ActivityRegistrationActions";
import { activityAccessDenied, activityDate, activityTime, activityPrice, retryActivityRead, type ActivityRegistration } from "../lib/activities";
import { taipeiInputToIso } from "../lib/meal-time";
import { useDeadlineClock } from "../lib/use-deadline-clock";
import "./ActivitiesPayment.css";

import { DataState, LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import { apiFetch, formatMoney } from "../lib/api";
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
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [accessRevoked, setAccessRevoked] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState(emptyForm);
  const membership = useQuery({
    queryKey: ["membership-me", user?.id],
    queryFn: () => apiFetch<MembershipSummary>("/v1/members/me"),
    enabled: Boolean(user && !accessRevoked),
    retry: retryActivityRead,
  });
  const canCreate = membership.data?.membership_type === "member";
  const activities = useQuery({
    queryKey: ["activities", user?.id],
    queryFn: () => apiFetch<Activity[]>("/v1/activities"),
    enabled: !accessRevoked, retry: retryActivityRead,
    refetchInterval: query => activityAccessDenied(query.state.error) ? false : 30000,
  });
  const createActivity = useMutation({
    mutationFn: () => apiFetch<Activity>("/v1/activities", {
      method: "POST",
      body: JSON.stringify({
        ...form,
        starts_at: taipeiInputToIso(form.starts_at),
        ends_at: taipeiInputToIso(form.ends_at),
        registration_deadline: taipeiInputToIso(form.registration_deadline),
      }),
    }),
    onSuccess: () => {
      setForm(emptyForm);
      setShowForm(false);
      queryClient.invalidateQueries({ queryKey: ["activities"] });
    },
    onError: error => { if (activityAccessDenied(error)) setAccessRevoked(true); },
  });

  useEffect(() => {
    if (activityAccessDenied(activities.error) || activityAccessDenied(membership.error)) setAccessRevoked(true);
  }, [activities.error, membership.error]);

  function submit(event: FormEvent) {
    event.preventDefault();
    createActivity.mutate();
  }

  if (accessRevoked || activityAccessDenied(activities.error) || activityAccessDenied(membership.error)) return <section className="social-page-shell"><DataState kind="error" title="活動資料權限已失效" detail="請重新登入後再試。" /></section>;

  return (
    <section className="social-page-shell">
      <header className="social-page-heading">
        <div><p className="eyebrow">SUSTAINABILITY ACTIVITIES / 永續活動</p><h1>永續活動</h1></div>
        <Link className="button button-quiet" to="/activities/registrations">我的活動報名</Link>
        {canCreate && <button className="button button-system" type="button" onClick={() => setShowForm((value) => !value)}><Plus size={18} />發起活動</button>}
      </header>
      {canCreate && showForm && (
        <form className="social-form" onSubmit={submit}>
          <div className="form-heading"><div><p className="eyebrow">NEW ACTIVITY / 新增活動</p><h2>發起永續活動</h2></div></div>
          <div className="field-grid two-columns">
            <label className="field wide"><span>活動名稱</span><input required maxLength={160} value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} /></label>
            <label className="field"><span>地點</span><input required maxLength={240} value={form.location} onChange={(event) => setForm({ ...form, location: event.target.value })} /></label>
            <label className="field"><span>名額</span><input required min={1} max={100000} type="number" value={form.capacity} onChange={(event) => setForm({ ...form, capacity: Number(event.target.value) })} /></label>
            <label className="field"><span>開始時間（台灣）</span><input required type="datetime-local" value={form.starts_at} onChange={(event) => setForm({ ...form, starts_at: event.target.value })} /></label>
            <label className="field"><span>結束時間（台灣）</span><input required type="datetime-local" value={form.ends_at} onChange={(event) => setForm({ ...form, ends_at: event.target.value })} /></label>
            <label className="field"><span>報名截止（台灣）</span><input required type="datetime-local" value={form.registration_deadline} onChange={(event) => setForm({ ...form, registration_deadline: event.target.value })} /></label>
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
      {activities.data?.length === 0 && <DataState title="目前沒有已發布活動" detail="" />}
      <div className="activity-list activity-payment-list">
        {!activities.isError && activities.data?.map(activity => <ActivityCard key={`${user?.id || "guest"}:${activity.id}`} activity={activity} onDone={() => {void queryClient.invalidateQueries({queryKey:["activities"]});void queryClient.invalidateQueries({queryKey:["activity-registrations"]});}} onDenied={()=>setAccessRevoked(true)} />)}
      </div>

    </section>
  );
}

function activityStatusLabel(status: string) {
  return { published: "報名中", pending_review: "待審核", completed: "已完成", cancelled: "已取消" }[status] || status;
}

function ActivityCard({activity,onDone,onDenied}: {activity:Activity;onDone:()=>void;onDenied:()=>void}) {
  const {user,openLogin}=useAuth();
  const [open,setOpen]=useState(false),[name,setName]=useState(user?.display_name || ""),[email,setEmail]=useState(user?.notification_email || user?.email || "");
  const now=useDeadlineClock(activity.registration_deadline);
  const closed=now>=activityTime(activity.registration_deadline);
  const active=activity.my_registration && !['cancelled'].includes(activity.my_registration.status);
  const price=activityPrice(activity,user?.membership_type);
  const register=useMutation({mutationFn:()=>apiFetch<ActivityRegistration>(`/v1/activities/${activity.id}/register`,{method:'POST',body:JSON.stringify({contact_name:name.trim(),contact_email:email.trim()})}),onSuccess:()=>{setOpen(false);onDone();},onError:error=>{if(activityAccessDenied(error))onDenied();else onDone();}});
  return <article><div className="activity-calendar"><CalendarBlank size={21} weight="light"/><time>{activityDate(activity.starts_at)}</time></div><div className="activity-main"><span className="status-chip">{activityStatusLabel(activity.status)}</span><h2>{activity.title}</h2><p>{activity.description}</p><div><span><MapPin size={15}/>{activity.location}</span><span><UsersThree size={15}/>{activity.registration_count}/{activity.capacity} 人{activity.waitlist_count>0&&` · 候補 ${activity.waitlist_count}`}</span></div><p className="activity-price"><span>社員 {activity.member_price ? formatMoney(activity.member_price):'免費'}</span><span>非社員 {activity.non_member_price ? formatMoney(activity.non_member_price):'免費'}</span></p></div>
    <div className="activity-cta"><small>報名至 {activityDate(activity.registration_deadline)}</small>{active ? <ActivityRegistrationActions registration={activity.my_registration!} onDone={onDone} onDenied={onDenied}/> : <><button className="button button-system" disabled={closed||register.isPending} onClick={()=>user?setOpen(!open):openLogin()}>{closed?'報名已截止':!user?'登入報名':activity.registration_count>=activity.capacity?'加入候補':'立即報名'}</button>{open&&<form className="activity-action-form" aria-label={`報名${activity.title}`} onSubmit={event=>{event.preventDefault();register.mutate();}}><p>報名費 {formatMoney(price)}</p><label className="field"><span>聯絡姓名</span><input required maxLength={160} value={name} onChange={event=>setName(event.target.value)}/></label><label className="field"><span>通知 Email</span><input required type="email" maxLength={320} value={email} onChange={event=>setEmail(event.target.value)}/></label><div className="form-actions"><button className="button button-quiet" type="button" disabled={register.isPending} onClick={()=>setOpen(false)}>返回</button><button className="button button-primary" disabled={closed||register.isPending||!name.trim()||!email.trim()}>{register.isPending?'送出中…':'確認報名'}</button></div></form>}</>}{register.isError&&<p className="form-error" role="alert">{register.error.message}</p>}</div>
  </article>;
}
