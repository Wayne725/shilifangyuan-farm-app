import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, apiFetch } from "../lib/api";
import { campaignAccessDenied, campaignDateInput, campaignDateTime, campaignPreorderScheduleError } from "../lib/group-campaigns";
import { taipeiInputToIso } from "../lib/meal-time";
import type { GroupCampaign } from "../lib/types";
import "./CampaignReviewPanel.css";

type Props = { campaign: GroupCampaign; unit?: string; onDone: () => void; onAccessDenied: () => void };

export function CampaignReviewPanel({ campaign, onDone, onAccessDenied }: Props) {
  const [editingPickup, setEditingPickup] = useState(false);
  const [reason, setReason] = useState("");
  const [result, setResult] = useState("");
  const active = ["recruiting", "pending_confirmation", "confirmed"].includes(campaign.decision_status);
  const cancel = useMutation({
    mutationFn: () => apiFetch<GroupCampaign>(`/v1/group-campaigns/${campaign.id}/admin/cancel`, { method: "POST", body: JSON.stringify({ reason: reason.trim() }) }),
    onSuccess: () => { setReason(""); setResult("共同購買已取消"); onDone(); },
    onError: error => { if (campaignAccessDenied(error)) onAccessDenied(); },
  });
  return <section className="campaign-review-panel" aria-label="共同購買取貨與取消">
    <dl className="campaign-quantity-summary">
      <div><dt>取貨開始（台灣）</dt><dd>{campaignDateTime(campaign.estimated_pickup_start)}</dd></div>
      <div><dt>取貨結束（台灣）</dt><dd>{campaignDateTime(campaign.estimated_pickup_end)}</dd></div>
      {campaign.final_pickup_at && <div><dt>原確認取貨時間</dt><dd>{campaignDateTime(campaign.final_pickup_at)}</dd></div>}
    </dl>
    {campaign.preorder_only && active && <PickupArrangement campaign={campaign} disabled={cancel.isPending} onEditing={setEditingPickup} onAccessDenied={onAccessDenied} onDone={onDone} />}
    {active && <div className="campaign-rejection">
      <label className="field"><span>取消原因</span><input value={reason} maxLength={500} disabled={cancel.isPending} onChange={event => setReason(event.target.value)} /></label>
      <button className="button button-quiet" type="button" disabled={cancel.isPending || editingPickup} onClick={() => { setResult(""); cancel.mutate(); }}>取消共同購買</button>
    </div>}
    {cancel.isError && <p className="form-error" role="alert">{cancel.error.message}</p>}
    {result && <p className="form-success" role="status">{result}</p>}
  </section>;
}

function PickupArrangement({ campaign, onDone, onAccessDenied, onEditing, disabled }: Props & { onEditing: (editing: boolean) => void; disabled: boolean }) {
  const client = useQueryClient();
  const [editor, setEditor] = useState<{ start: string; end: string; original: GroupCampaign } | null>(null);
  const [latest, setLatest] = useState<GroupCampaign | null>(null);
  const [conflict, setConflict] = useState(false);
  const [rechecked, setRechecked] = useState(false);
  const [saved, setSaved] = useState(false);
  const reference = latest || editor?.original || campaign;
  const stale = Boolean(editor) && campaign.updated_at !== reference.updated_at;
  const changes = editor ? {
    ...(editor.start !== campaignDateInput(reference.estimated_pickup_start) ? { estimated_pickup_start: taipeiInputToIso(editor.start) } : {}),
    ...(editor.end !== campaignDateInput(reference.estimated_pickup_end) ? { estimated_pickup_end: taipeiInputToIso(editor.end) } : {}),
  } : {};
  const error = editor ? campaignPreorderScheduleError(campaignDateInput(reference.deadline), editor.start, editor.end, Date.now(), true) : "";
  const save = useMutation({
    mutationFn: () => apiFetch<GroupCampaign>(`/v1/group-campaigns/${campaign.id}`, { method: "PATCH", body: JSON.stringify({ ...changes, expected_updated_at: reference.updated_at }) }),
    onSuccess: () => {
      setEditor(null);
      onEditing(false);
      setSaved(true);
      void client.invalidateQueries({ queryKey: ["group-campaign", campaign.id] });
      onDone();
    },
    onError: error => {
      if (campaignAccessDenied(error)) onAccessDenied();
      else if (error instanceof ApiError && error.status === 409) {
        setConflict(true);
        setLatest(null);
        setRechecked(false);
      }
    },
  });
  const reload = useMutation({
    mutationFn: () => apiFetch<GroupCampaign>(`/v1/group-campaigns/${campaign.id}`),
    onSuccess: value => {
      setLatest(value);
      setConflict(true);
      setRechecked(false);
      save.reset();
      void client.invalidateQueries({ queryKey: ["group-campaigns"] });
    },
    onError: error => { if (campaignAccessDenied(error)) onAccessDenied(); },
  });
  const busy = save.isPending || reload.isPending;
  const ready = Boolean(editor && reference.updated_at) && !error && Object.keys(changes).length > 0 && !stale && (!conflict || Boolean(latest && rechecked)) && !busy;
  return <div className="campaign-review-deadline">
    {!editor && <button className="button button-quiet" type="button" disabled={disabled} onClick={() => {
      setEditor({ start: campaignDateInput(campaign.estimated_pickup_start), end: campaignDateInput(campaign.estimated_pickup_end), original: campaign });
      setLatest(null);
      setConflict(false);
      setRechecked(false);
      setSaved(false);
      save.reset();
      reload.reset();
      onEditing(true);
    }}>調整取貨安排</button>}
    {saved && <p className="form-success" role="status">取貨安排已更新</p>}
    {editor && <form className="social-form" aria-label="調整共同購買取貨安排" onSubmit={event => { event.preventDefault(); if (ready) save.mutate(); }}>
      <div className="field-grid two-columns">
        <label className="field"><span>取貨開始（台灣）</span><input required type="datetime-local" value={editor.start} disabled={busy} onChange={event => setEditor({ ...editor, start: event.target.value })} /></label>
        <label className="field"><span>取貨結束（台灣）</span><input required type="datetime-local" value={editor.end} disabled={busy} onChange={event => setEditor({ ...editor, end: event.target.value })} /></label>
      </div>
      {(conflict || stale) && <div className="campaign-review-conflict">
        <p className="form-error" role="alert">取貨安排已變更，輸入已保留。請重新核對。</p>
        <button className="button button-quiet" type="button" disabled={busy} onClick={() => reload.mutate()}>重新讀取取貨安排</button>
        {latest && !stale && <>
          <p>目前取貨開始：{campaignDateTime(latest.estimated_pickup_start)} · 取貨結束：{campaignDateTime(latest.estimated_pickup_end)}</p>
          <label className="meal-choice"><input type="checkbox" checked={rechecked} disabled={busy} onChange={event => setRechecked(event.target.checked)} /><span>已核對最新取貨安排，確認儲存目前輸入</span></label>
        </>}
      </div>}
      {(error || save.isError || reload.isError) && <p className="form-error" role="alert">{error || save.error?.message || reload.error?.message}</p>}
      <div className="form-actions"><button className="button button-quiet" type="button" disabled={busy} onClick={() => { setEditor(null); onEditing(false); }}>取消調整</button><button className="button button-primary" type="submit" disabled={!ready}>{save.isPending ? "儲存中…" : "儲存取貨安排"}</button></div>
    </form>}
  </div>;
}
