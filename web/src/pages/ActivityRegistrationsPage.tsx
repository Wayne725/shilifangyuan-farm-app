import { useEffect, useRef, useState } from 'react';
import { Link, useSearch } from '@tanstack/react-router';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useAuth } from '../context/AuthContext';
import { apiFetch } from '../lib/api';
import { refreshPaymentAttempt } from '../lib/commerce';
import { activityAccessDenied, activityDate, activityPaymentLabel, filterActivityRegistrations, retryActivityRead, type ActivityCharge, type ActivityRegistration } from '../lib/activities';
import { ActivityChargeDetails, ActivityRegistrationActions } from '../components/ActivityRegistrationActions';
import { DataState, LoadingLines } from '../components/Shared';
import './ActivitiesPayment.css';

export function ActivityRegistrationsPage() {
  const {user,isAuthReady,openLogin}=useAuth();
  if(!isAuthReady) return <section className="social-page-shell"><LoadingLines count={3}/><p>正在恢復登入狀態與付款結果…</p></section>;
  if(!user) return <section className="social-page-shell"><h1>我的活動報名</h1><button className="button button-primary" onClick={openLogin}>登入查看活動報名</button></section>;
  return <RegistrationWorkspace key={user.id} userId={user.id}/>;
}
function RegistrationWorkspace({userId}:{userId:string}) {
  const search=useSearch({from:'/activities/registrations'}), client=useQueryClient();
  const [denied,setDenied]=useState(false),[q,setQ]=useState(''),[status,setStatus]=useState('');
  const started=useRef(Date.now());
  const registrations=useQuery({queryKey:['activity-registrations',userId],queryFn:()=>apiFetch<ActivityRegistration[]>('/v1/activity-registrations'),enabled:!denied,retry:retryActivityRead,refetchInterval:query=>activityAccessDenied(query.state.error)?false:30000});
  const charges=useQuery({queryKey:['activity-charges',userId],queryFn:()=>apiFetch<ActivityCharge[]>('/v1/activity-charges'),enabled:!denied,retry:retryActivityRead,refetchInterval:query=>activityAccessDenied(query.state.error)?false:30000});
  const confirmation=useQuery({queryKey:['activity-payment-refresh',userId,search.attempt_id],queryFn:()=>refreshPaymentAttempt(search.attempt_id!),enabled:!denied&&Boolean(search.attempt_id&&search.payment==='confirming'),retry:retryActivityRead,refetchInterval:query=>{if(activityAccessDenied(query.state.error)||Date.now()-started.current>=120000)return false;const state=query.state.data?.status;return state&&!['pending','confirming'].includes(state)?false:Date.now()-started.current<30000?5000:15000;},refetchIntervalInBackground:false});
  const refresh=()=>{void client.invalidateQueries({queryKey:['activity-registrations']});void client.invalidateQueries({queryKey:['activity-charges']});void client.invalidateQueries({queryKey:['activities']});};
  useEffect(()=>{started.current=Date.now();},[search.attempt_id]);
  useEffect(()=>{if(confirmation.data) refresh();},[confirmation.data]); // eslint-disable-line react-hooks/exhaustive-deps
  const revoked=denied||[registrations.error,charges.error,confirmation.error].some(activityAccessDenied);
  useEffect(()=>{if(revoked)setDenied(true);},[revoked]);
  if(revoked)return <section className="social-page-shell"><DataState kind="error" title="活動報名權限已失效" detail="請重新登入後再試。"/></section>;
  const rows=filterActivityRegistrations(registrations.data||[],q,status);
  const currentCharge=charges.data?.find(item=>item.id===search.activity_charge_id);
  const knownIds=new Set((registrations.data||[]).map(item=>item.charge?.id));
  const history=(charges.data||[]).filter(item=>!knownIds.has(item.id));
  return <section className="social-page-shell"><header className="social-page-heading"><div><p className="eyebrow">MY ACTIVITIES / 活動報名</p><h1>我的活動報名</h1></div><Link to="/activities" className="button button-quiet">瀏覽永續活動</Link></header>
    {search.activity_charge_id&&<section className="activity-payment-result" aria-label="活動付款結果"><h2>活動付款結果</h2>{currentCharge?<><p>{currentCharge.activity_title} · {currentCharge.number}</p><p role="status">{activityPaymentLabel(currentCharge.payment_status)}</p></>:charges.isPending?<LoadingLines count={2}/>:<p>找不到這筆活動繳款，請核對下方報名紀錄。</p>}{confirmation.isFetching&&<p role="status">正在核對金流結果…</p>}{confirmation.isError&&<p className="form-error" role="alert">{confirmation.error.message}</p>}<button className="button button-quiet" disabled={confirmation.isFetching||charges.isFetching} onClick={()=>{refresh();if(search.attempt_id)void confirmation.refetch();}}>更新付款結果</button></section>}
    <div className="activity-filter"><label className="field"><span>查詢活動或繳款編號</span><input value={q} onChange={event=>setQ(event.target.value)}/></label><label className="field"><span>報名狀態</span><select value={status} onChange={event=>setStatus(event.target.value)}><option value="">全部狀態</option><option value="pending_payment">待付款</option><option value="registered">報名成功</option><option value="waitlisted">候補</option><option value="cancelled">已取消</option><option value="attended">已出席</option><option value="no_show">未出席</option></select></label><button className="button button-quiet" onClick={()=>{setQ('');setStatus('');}}>清除查詢</button></div>
    {registrations.isPending?<LoadingLines count={3}/>:registrations.isError?<DataState kind="error" title="報名紀錄無法讀取" detail={registrations.error.message}/>:<div className="activity-history-list">{rows.length===0&&<DataState title="沒有符合的報名紀錄" detail=""/>}{rows.map(item=><article key={item.id}><h2>{item.activity_title}</h2>{item.starts_at&&<p>{activityDate(item.starts_at)} · {item.location}</p>}<p>{item.contact_name} · {item.contact_email}</p><ActivityRegistrationActions registration={item} onDone={refresh} onDenied={()=>setDenied(true)}/></article>)}</div>}
    {charges.isError&&<DataState kind="error" title="繳款紀錄無法讀取" detail={charges.error.message}/>}
    {!charges.isError&&history.length>0&&<details className="activity-history-list"><summary>歷次繳款紀錄（{history.length} 筆）</summary>{history.map(charge=><article key={charge.id}><h2>{charge.activity_title} · 第 {charge.registration_revision} 次報名</h2><ActivityChargeDetails charge={charge} onDone={refresh} onDenied={()=>setDenied(true)}/></article>)}</details>}
  </section>;
}
