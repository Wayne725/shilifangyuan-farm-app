import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { ApiError, apiFetch, formatMoney } from '../lib/api';
import { activityAccessDenied, activityDate, activityMembershipLabel, activityPaymentLabel, activityRefundLabel, activityRegistrationLabel, canPayActivity, createActivityPayment, type ActivityCharge, type ActivityRegistration } from '../lib/activities';
import { openActivityHandoff } from '../lib/checkout-navigation';
import { useDeadlineClock } from '../lib/use-deadline-clock';

export function ActivityChargeDetails({charge, onDone, onDenied}: {charge: ActivityCharge; onDone: () => void; onDenied: () => void}) {
  const now=useDeadlineClock(charge.expires_at);
  const pay=useMutation({mutationFn: () => createActivityPayment(charge.id), onSuccess: result => { if(!result.payment_url) throw new Error("付款頁尚未建立"); return openActivityHandoff(result.payment_url,charge.id,result.attempt_id || result.id); }, onError: error => { if(activityAccessDenied(error)) onDenied(); else if(error instanceof ApiError && error.status===409) onDone(); }});
  return <div className="activity-charge"><dl className="activity-detail-grid"><div><dt>繳款編號</dt><dd>{charge.number}</dd></div><div><dt>報名時身分</dt><dd>{activityMembershipLabel(charge.membership_type)}</dd></div><div><dt>報名費</dt><dd>{formatMoney(charge.amount)}</dd></div><div><dt>付款狀態</dt><dd>{activityPaymentLabel(charge.payment_status)}</dd></div><div><dt>付款期限（台灣）</dt><dd>{activityDate(charge.expires_at)}</dd></div>{charge.paid_at && <div><dt>收款時間</dt><dd>{activityDate(charge.paid_at)}</dd></div>}</dl>
    {charge.refund_requests?.map(request=><p key={request.id} role="status">{activityRefundLabel(request.status)} · {formatMoney(request.amount)}{request.review_reason && ` · ${request.review_reason}`}</p>)}
    {canPayActivity(charge,now) && <button className="button button-primary" disabled={pay.isPending} onClick={()=>pay.mutate()}>{pay.isPending?'準備付款頁…':`前往付款 ${formatMoney(charge.amount)}`}</button>}
    {pay.isError && <p className="form-error" role="alert">{pay.error.message}</p>}
  </div>;
}
export function ActivityRegistrationActions({registration, onDone, onDenied}: {registration:ActivityRegistration;onDone:()=>void;onDenied:()=>void}) {
  const [open,setOpen]=useState(false),[reason,setReason]=useState(''),[conflict,setConflict]=useState(false);
  const pending=registration.refund_request?.status==='pending_review';
  const paid=Boolean(registration.available_actions?.includes('request_refund'));
  const active=Boolean(registration.available_actions?.some(action=>['cancel','request_refund'].includes(action)));
  const cancel=useMutation({mutationFn:()=>apiFetch<ActivityRegistration>(`/v1/activities/${registration.activity_id}/cancel-registration`,{method:'POST',body:JSON.stringify({expected_revision:registration.revision,reason:reason.trim() || '本人申請取消活動報名'})}),onSuccess:()=>{setOpen(false);setReason('');onDone();},onError:error=>{if(activityAccessDenied(error))onDenied();else if(error instanceof ApiError&&error.status===409){setConflict(true);onDone();}}});
  return <div className="activity-registration"><p className="status-chip">{activityRegistrationLabel(registration.status)}{registration.status==='waitlisted' && ` · 順序 ${registration.queue_position}`}</p>
    {registration.charge && <ActivityChargeDetails charge={registration.charge} onDone={onDone} onDenied={onDenied}/>}
    {registration.available_actions?.includes("update_contact") && <ActivityContactForm registration={registration} onDone={onDone} onDenied={onDenied}/>}
    {pending && <p role="status">{registration.refund_request?.kind === "late_or_duplicate_payment" ? "晚到或重複付款退款待管理員審核" : "取消及退款申請待管理員審核"}</p>}
    {active && <button className="button button-quiet" disabled={cancel.isPending} onClick={()=>setOpen(!open)}>{paid?'申請取消及退款':registration.status==='waitlisted'?'取消候補':'取消報名'}</button>}
    {open && active && <form className="activity-action-form" aria-label="取消活動報名" onSubmit={event=>{event.preventDefault();cancel.mutate();}}>{paid && <p>申請退款 {formatMoney(registration.charge!.amount)}</p>}<label className="field"><span>取消原因</span><textarea required={paid} maxLength={2000} value={reason} onChange={event=>setReason(event.target.value)} /></label>
      {conflict && <div><p className="form-error">報名資料已更新，請核對目前狀態後重試。</p><button type="button" className="button button-quiet" onClick={()=>{setConflict(false);cancel.reset();}}>已核對目前報名資料</button></div>}
      <div className="form-actions"><button className="button button-quiet" type="button" onClick={()=>setOpen(false)} disabled={cancel.isPending}>返回</button><button className="button button-primary" disabled={conflict||cancel.isPending||(paid&&!reason.trim())}>{cancel.isPending?'送出中…':paid?'送出退款申請':'確認取消'}</button></div></form>}
    {cancel.isError && <p className="form-error" role="alert">{cancel.error.message}</p>}
  </div>;
}

function ActivityContactForm({registration,onDone,onDenied}:{registration:ActivityRegistration;onDone:()=>void;onDenied:()=>void}) {
  const [open,setOpen]=useState(false),[name,setName]=useState(registration.contact_name||""),[email,setEmail]=useState(registration.contact_email||"");
  const save=useMutation({mutationFn:()=>apiFetch(`/v1/activities/${registration.activity_id}/register`,{method:"POST",body:JSON.stringify({contact_name:name.trim(),contact_email:email.trim()})}),onSuccess:()=>{setOpen(false);onDone();},onError:error=>{if(activityAccessDenied(error))onDenied();}});
  return <><button className="button button-quiet" onClick={()=>setOpen(!open)}>更新聯絡資料</button>{open&&<form className="activity-action-form" aria-label="更新候補聯絡資料" onSubmit={event=>{event.preventDefault();save.mutate();}}><label className="field"><span>聯絡姓名</span><input required maxLength={160} value={name} onChange={event=>setName(event.target.value)}/></label><label className="field"><span>通知 Email</span><input required type="email" value={email} onChange={event=>setEmail(event.target.value)}/></label>{save.isError&&<p className="form-error">{save.error.message}</p>}<button className="button button-primary" disabled={save.isPending}>儲存聯絡資料</button></form>}</>;
}
