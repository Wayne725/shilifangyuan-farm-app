import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, apiFetch } from "../lib/api";
import { campaignAccessDenied, campaignConfirmBlockReason, campaignDateInput, campaignDateTime, campaignScheduleError } from "../lib/group-campaigns";
import { taipeiInputToIso } from "../lib/meal-time";
import { useDeadlineClock } from "../lib/use-deadline-clock";
import type { GroupCampaign } from "../lib/types";
import "./CampaignReviewPanel.css";
import { OptionalMark } from "./OptionalMark";

type Props = { campaign: GroupCampaign; unit?: string; onDone: () => void; onAccessDenied: () => void };

export function CampaignReviewPanel({ campaign, unit = "份", onDone, onAccessDenied }: Props) {
  const now = useDeadlineClock(campaign.deadline);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [deadlineOpen, setDeadlineOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [result, setResult] = useState("");
  const otherDecision = useMutation({
    mutationFn: (decision: "reject" | "cancel") => apiFetch<GroupCampaign>(`/v1/group-campaigns/${campaign.id}/admin/${decision}`, { method: "POST", body: JSON.stringify({ reason: reason.trim() }) }),
    onSuccess: () => { setReason(""); setResult("審核結果已儲存"); onDone(); },
    onError: error => { if (campaignAccessDenied(error)) onAccessDenied(); },
  });
  const block = campaignConfirmBlockReason(campaign, now);
  const reviewing = ["recruiting", "pending_confirmation"].includes(campaign.decision_status);
  return <section className="campaign-review-panel" aria-label="共同購買成團審核">
    <dl className="campaign-quantity-summary">
      <div><dt>管理員處理提醒日（台灣）</dt><dd>{campaignDateTime(campaign.confirmation_deadline)}</dd></div>
      {reviewing && <div><dt>距離成團門檻</dt><dd>{Math.max(0, campaign.min_paid_quantity - campaign.paid_quantity)} {unit}</dd></div>}
      {campaign.final_pickup_at && <div><dt>最終取貨時間</dt><dd>{campaignDateTime(campaign.final_pickup_at)}</dd></div>}
    </dl>
    {campaign.review_overdue && reviewing && <p className="form-error" role="status">已到處理提醒日，仍待管理員處理</p>}
    {campaign.confirmed_paid_quantity != null && <p>確認紀錄：已付款 {campaign.confirmed_paid_quantity} {unit}／原門檻 {campaign.confirmed_min_paid_quantity} {unit} · 第 {campaign.confirmed_threshold_version} 版</p>}
    {campaign.confirmation_exception_reason && <p>例外成團原因：{campaign.confirmation_exception_reason}</p>}
    {reviewing && <>
      {!reviewOpen && <button className="button button-system" type="button" disabled={Boolean(block) || otherDecision.isPending || deadlineOpen} onClick={() => { setResult(""); setReviewOpen(true); }}>審核成團</button>}
      {block && <p className="form-error" role="status">{block}</p>}
      {reviewOpen && <ConfirmationForm campaign={campaign} unit={unit} onAccessDenied={onAccessDenied} onCancel={() => setReviewOpen(false)} onDone={() => { setReviewOpen(false); setResult("共同購買已確認成團"); onDone(); }} />}
    </>}
    {campaign.can_edit_review_deadline && !reviewOpen && <ReviewDeadlineForm disabled={otherDecision.isPending} onEditing={setDeadlineOpen} campaign={campaign} onDone={onDone} onAccessDenied={onAccessDenied} />}
    {["recruiting", "pending_confirmation", "confirmed"].includes(campaign.decision_status) && <div className="campaign-rejection">
      <label className="field"><span>取消／拒絕原因<OptionalMark /></span><input value={reason} maxLength={500} disabled={otherDecision.isPending} onChange={event => setReason(event.target.value)} /></label>
      <button className="button button-quiet" type="button" disabled={otherDecision.isPending || reviewOpen || deadlineOpen} onClick={() => otherDecision.mutate(campaign.decision_status === "pending_confirmation" ? "reject" : "cancel")}>{campaign.decision_status === "pending_confirmation" ? "不成團" : "取消共同購買"}</button>
    </div>}
    {otherDecision.isError && <p className="form-error" role="alert">{otherDecision.error.message}</p>}
    {result && <p className="form-success" role="status">{result}</p>}
  </section>;
}

function ConfirmationForm({ campaign, unit = "份", onDone, onAccessDenied, onCancel }: Props & { onCancel: () => void }) {
  const client = useQueryClient();
  const [snapshot, setSnapshot] = useState(campaign);
  const [pickup, setPickup] = useState(campaignDateInput(campaign.final_pickup_at || campaign.estimated_pickup_start));
  const [allowBelow, setAllowBelow] = useState(false);
  const [reason, setReason] = useState("");
  const [conflict, setConflict] = useState(false);
  const [rechecked, setRechecked] = useState(false);
  const [reloaded, setReloaded] = useState(false);
  const now = useDeadlineClock(campaign.deadline);
  const stale = campaign.threshold_version !== snapshot.threshold_version;
  const below = snapshot.paid_quantity < snapshot.min_paid_quantity;
  const block = campaignConfirmBlockReason(campaign, now) || campaignConfirmBlockReason(snapshot, now);
  const pickupValid = pickup && new Date(`${pickup}:00+08:00`).getTime() > now;
  const valid = !block && !stale && (!conflict || reloaded && rechecked) && pickupValid && (!below || allowBelow);
  const save = useMutation({
    mutationFn: () => apiFetch<GroupCampaign>(`/v1/group-campaigns/${campaign.id}/admin/confirm`, { method: "POST", body: JSON.stringify({ final_pickup_at: taipeiInputToIso(pickup), expected_threshold_version: snapshot.threshold_version, allow_below_threshold: below && allowBelow, ...(below ? { reason: reason.trim() } : {}) }) }),
    onSuccess: onDone,
    onError: error => { if (campaignAccessDenied(error)) onAccessDenied(); else if (error instanceof ApiError && error.status === 409) { setConflict(true); setReloaded(false); setRechecked(false); void client.invalidateQueries({ queryKey: ["group-campaigns"] }); } },
  });
  const reload = useMutation({
    mutationFn: () => apiFetch<GroupCampaign>(`/v1/group-campaigns/${campaign.id}`),
    onSuccess: latest => { setSnapshot(latest); setConflict(true); setReloaded(true); setRechecked(false); setAllowBelow(false); void client.invalidateQueries({ queryKey: ["group-campaigns"] }); },
    onError: error => { if (campaignAccessDenied(error)) onAccessDenied(); },
  });
  const busy = save.isPending || reload.isPending;
  return <form className="social-form campaign-decision-form" aria-label="確認共同購買成團" onSubmit={event => { event.preventDefault(); if (valid && !busy) save.mutate(); }}>
    <h3>確認成團</h3>
    <p>已付款 {snapshot.paid_quantity} {unit}／原門檻 {snapshot.min_paid_quantity} {unit} · 第 {snapshot.threshold_version} 版</p>
    <label className="field"><span>最終取貨時間（台灣）</span><input required type="datetime-local" value={pickup} disabled={busy} onChange={event => setPickup(event.target.value)} /></label>
    {below && <>
      <label className="meal-choice"><input type="checkbox" checked={allowBelow} disabled={busy} onChange={event => setAllowBelow(event.target.checked)} /><span>未達原門檻，仍確認成團</span></label>
      <label className="field"><span>例外成團原因<OptionalMark /></span><textarea maxLength={500} value={reason} disabled={busy} onChange={event => setReason(event.target.value)} /></label>
    </>}
    {!pickupValid && <p className="form-error">最終取貨時間須晚於現在</p>}
    {(conflict || stale) && <div className="campaign-review-conflict"><p className="form-error" role="alert">共同購買資料已變更，輸入已保留。請重新核對付款數量與門檻。</p><button className="button button-quiet" type="button" disabled={busy} onClick={() => reload.mutate()}>重新核對成團資料</button>{reloaded && !stale && <label className="meal-choice"><input type="checkbox" checked={rechecked} disabled={busy} onChange={event => setRechecked(event.target.checked)} /><span>已核對最新付款數量與原門檻</span></label>}</div>}
    {save.isError && <p className="form-error" role="alert">{save.error.message}</p>}
    {reload.isError && <p className="form-error" role="alert">{reload.error.message}</p>}
    <div className="form-actions"><button className="button button-quiet" type="button" disabled={busy} onClick={onCancel}>取消審核</button><button className="button button-primary" type="submit" disabled={!valid || busy}>{save.isPending ? "確認中…" : below ? "例外確認成團" : "確認成團"}</button></div>
  </form>;
}

function ReviewDeadlineForm({ campaign, onDone, onAccessDenied, onEditing, disabled }: Props & { onEditing: (editing: boolean) => void; disabled: boolean }) {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [snapshot, setSnapshot] = useState(campaign);
  const [deadline, setDeadline] = useState(campaignDateInput(campaign.confirmation_deadline));
  const [pickupStart, setPickupStart] = useState(campaignDateInput(campaign.estimated_pickup_start));
  const [pickupEnd, setPickupEnd] = useState(campaignDateInput(campaign.estimated_pickup_end));
  const [reason, setReason] = useState("");
  const [conflict, setConflict] = useState(false);
  const [latest, setLatest] = useState<GroupCampaign | null>(null);
  const [rechecked, setRechecked] = useState(false);
  const [result, setResult] = useState("");
  const reference = latest || snapshot;
  const error = campaignScheduleError(campaignDateInput(reference.deadline), deadline, pickupStart, pickupEnd, Date.now(), true);
  const stale = campaign.updated_at !== reference.updated_at;
  const changed = deadline !== campaignDateInput(reference.confirmation_deadline) || pickupStart !== campaignDateInput(reference.estimated_pickup_start) || pickupEnd !== campaignDateInput(reference.estimated_pickup_end);
  const ready = Boolean(reference.updated_at) && !error && changed && !stale && (!conflict || latest && rechecked);
  const save = useMutation({
    mutationFn: () => apiFetch<GroupCampaign>(`/v1/group-campaigns/${campaign.id}/admin/review-deadline`, { method: "PATCH", body: JSON.stringify({ confirmation_deadline: taipeiInputToIso(deadline), expected_confirmation_deadline: reference.confirmation_deadline || null, expected_updated_at: reference.updated_at, reason: reason.trim(), ...(pickupStart !== campaignDateInput(reference.estimated_pickup_start) ? { estimated_pickup_start: taipeiInputToIso(pickupStart) } : {}), ...(pickupEnd !== campaignDateInput(reference.estimated_pickup_end) ? { estimated_pickup_end: taipeiInputToIso(pickupEnd) } : {}) }) }),
    onSuccess: () => { setOpen(false); onEditing(false); setResult("處理提醒日已更新"); onDone(); },
    onError: error => { if (campaignAccessDenied(error)) onAccessDenied(); else if (error instanceof ApiError && error.status === 409) { setConflict(true); setLatest(null); setRechecked(false); } },
  });
  const reload = useMutation({
    mutationFn: () => apiFetch<GroupCampaign>(`/v1/group-campaigns/${campaign.id}`),
    onSuccess: value => { setLatest(value); setRechecked(false); void client.invalidateQueries({ queryKey: ["group-campaigns"] }); },
    onError: error => { if (campaignAccessDenied(error)) onAccessDenied(); },
  });
  const busy = save.isPending || reload.isPending;
  return <div className="campaign-review-deadline">
    <button className="button button-quiet" type="button" disabled={busy || disabled} onClick={() => { onEditing(!open); setOpen(value => !value); setSnapshot(campaign); setDeadline(campaignDateInput(campaign.confirmation_deadline)); setPickupStart(campaignDateInput(campaign.estimated_pickup_start)); setPickupEnd(campaignDateInput(campaign.estimated_pickup_end)); setReason(""); setConflict(false); setLatest(null); setRechecked(false); setResult(""); save.reset(); reload.reset(); }}>調整處理提醒日</button>
    {result && <p className="form-success" role="status">{result}</p>}
    {open && <form className="social-form" aria-label="調整共同購買處理提醒日" onSubmit={event => { event.preventDefault(); if (ready && !busy) save.mutate(); }}>
      <p>付款截止：{campaignDateTime(reference.deadline)} · 預計取貨開始：{campaignDateTime(reference.estimated_pickup_start)}</p>
      <div className="field-grid two-columns"><label className="field"><span>管理員處理提醒日（台灣）</span><input required type="datetime-local" value={deadline} disabled={busy} onChange={event => setDeadline(event.target.value)} /></label><label className="field"><span>預計取貨開始（台灣）</span><input required type="datetime-local" value={pickupStart} disabled={busy} onChange={event => setPickupStart(event.target.value)} /></label><label className="field"><span>預計取貨結束（台灣）</span><input required type="datetime-local" value={pickupEnd} disabled={busy} onChange={event => setPickupEnd(event.target.value)} /></label><label className="field"><span>調整原因<OptionalMark /></span><input maxLength={500} value={reason} disabled={busy} onChange={event => setReason(event.target.value)} /></label></div>
      {(conflict || stale) && <div className="campaign-review-conflict"><p className="form-error" role="alert">處理提醒日已變更，輸入已保留。請重新核對。</p><button className="button button-quiet" type="button" disabled={busy} onClick={() => { setConflict(true); reload.mutate(); }}>重新讀取處理提醒日</button>{latest && !stale && <><p>目前處理提醒日：{campaignDateTime(latest.confirmation_deadline)}</p><label className="meal-choice"><input type="checkbox" checked={rechecked} disabled={busy} onChange={event => setRechecked(event.target.checked)} /><span>已核對最新提醒日，確認儲存目前輸入</span></label></>}</div>}
      {(error || save.isError || reload.isError) && <p className="form-error" role="alert">{error || save.error?.message || reload.error?.message}</p>}
      <div className="form-actions"><button className="button button-quiet" type="button" disabled={busy} onClick={() => { setOpen(false); onEditing(false); }}>取消調整</button><button className="button button-primary" disabled={!ready || busy}>{save.isPending ? "儲存中…" : "儲存處理提醒日"}</button></div>
    </form>}
  </div>;
}
