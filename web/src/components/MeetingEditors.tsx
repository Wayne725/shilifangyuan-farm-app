import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { apiFetch, formatDateTime } from "../lib/api";
import { meetingAccessDenied } from "../lib/meetings";
import { taipeiDateTimeInput, taipeiInputToIso } from "../lib/meal-time";
import type { Meeting, MeetingCategory } from "../lib/types";

type MeetingActions = { onDone: () => void; onDenied: () => void };

export function MeetingDetailsEditor({ meeting, onDone, onDenied }: MeetingActions & { meeting: Meeting }) {
  const [open, setOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const remove = useMutation({
    mutationFn: () => apiFetch(`/v1/admin/meetings/${meeting.id}`, { method: "DELETE" }),
    onSuccess: onDone,
    onError: error => { if (meetingAccessDenied(error)) onDenied(); },
  });
  return <section>
    <div className="form-actions">
      <button className="button button-quiet" type="button" onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? "收起會議資料" : "編輯會議"}
      </button>
      {meeting.can_delete && !confirmDelete && <button className="button button-quiet" type="button" onClick={() => setConfirmDelete(true)}>刪除會議</button>}
    </div>
    {confirmDelete && <div className="form-actions">
      <button className="button button-quiet" type="button" disabled={remove.isPending} onClick={() => setConfirmDelete(false)}>取消</button>
      <button className="button button-danger" type="button" disabled={remove.isPending} onClick={() => remove.mutate()}>確認刪除會議</button>
    </div>}
    {remove.error && <p className="form-error" role="alert">{remove.error.message}</p>}
    {open && <MeetingDetailsForm meeting={meeting} onDenied={onDenied} onDone={() => { setOpen(false); onDone(); }} />}
  </section>;
}

function MeetingDetailsForm({ meeting, onDone, onDenied }: MeetingActions & { meeting: Meeting }) {
  const [original] = useState(meeting);
  const agendaText = original.agenda.map(item => typeof item.title === "string" ? item.title : "").join("\n");
  const [draft, setDraft] = useState(() => ({
    title: meeting.title, location: meeting.location, starts_at: taipeiDateTimeInput(meeting.starts_at),
    category_id: meeting.category_id, agenda: agendaText,
  }));
  const categories = useQuery({ queryKey: ["admin-meeting-categories"], retry: false,
    queryFn: ({ signal }) => apiFetch<MeetingCategory[]>("/v1/admin/meeting-categories", { signal }) });
  const save = useMutation({
    mutationFn: () => apiFetch(`/v1/admin/meetings/${meeting.id}`, { method: "PATCH", body: JSON.stringify({
      ...draft, title: draft.title.trim(), location: draft.location.trim(),
      starts_at: draft.starts_at === taipeiDateTimeInput(original.starts_at) ? original.starts_at : taipeiInputToIso(draft.starts_at),
      agenda: draft.agenda === agendaText ? original.agenda : draft.agenda.split("\n").map(line => line.trim()).filter(Boolean)
        .map(title => original.agenda.find(item => item.title === title) || { title }),
    }) }),
    onSuccess: onDone,
    onError: error => { if (meetingAccessDenied(error)) onDenied(); },
  });
  const options = (categories.data || []).filter(category => category.is_active || category.id === original.category_id);
  return <form className="social-form" aria-label="編輯會議" onSubmit={event => {
    event.preventDefault();
    if (!save.isPending) save.mutate();
  }}>
    <div className="field-grid two-columns">
      <label className="field"><span>會議名稱</span><input required maxLength={160} value={draft.title} onChange={event => setDraft({ ...draft, title: event.target.value })} /></label>
      <label className="field"><span>會議類別</span><select required value={draft.category_id} onChange={event => setDraft({ ...draft, category_id: event.target.value })}>
        {!options.some(item => item.id === original.category_id) && <option value={original.category_id}>{original.category_name}</option>}
        {options.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
      </select></label>
      <label className="field"><span>開始時間（台灣時間）</span><input required type="datetime-local" value={draft.starts_at} onChange={event => setDraft({ ...draft, starts_at: event.target.value })} /></label>
      <label className="field"><span>地點</span><input maxLength={240} value={draft.location} onChange={event => setDraft({ ...draft, location: event.target.value })} /></label>
    </div>
    <label className="field"><span>議程（每行一項）</span><textarea value={draft.agenda} onChange={event => setDraft({ ...draft, agenda: event.target.value })} /></label>
    {(save.error || categories.error) && <p className="form-error" role="alert">{save.error?.message || categories.error?.message}</p>}
    <button className="button button-system" disabled={save.isPending}>儲存會議</button>
  </form>;
}

type Resolution = Meeting["resolutions"][number];
type HistoryEntry = { id: string; actor: string; created_at: string; action: string;
  before?: { title?: string; resolution_text?: string } | null; after?: { title?: string; resolution_text?: string } | null };

export function ResolutionEditor({ meetingId, resolution, onDone, onDenied }: MeetingActions & { meetingId: string; resolution: Resolution }) {
  const [edit, setEdit] = useState(false);
  const [confirmRevoke, setConfirmRevoke] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [title, setTitle] = useState(resolution.title);
  const [body, setBody] = useState(resolution.resolution_text);
  const path = `/v1/admin/meetings/${meetingId}/resolutions/${resolution.id}`;
  const history = useQuery({ queryKey: ["meeting-resolution-history", resolution.id], enabled: historyOpen, retry: false,
    queryFn: ({ signal }) => apiFetch<HistoryEntry[]>(`${path}/history`, { signal }) });
  const update = useMutation({
    mutationFn: (revoke: boolean) => apiFetch(revoke ? `${path}/revoke` : path, revoke ? { method: "POST" }
      : { method: "PATCH", body: JSON.stringify({ title: title.trim(), resolution_text: body.trim() }) }),
    onSuccess: () => { setEdit(false); setConfirmRevoke(false); if (historyOpen) void history.refetch(); onDone(); },
    onError: error => { if (meetingAccessDenied(error)) onDenied(); },
  });
  return <article>
    <p><strong>{resolution.title}</strong>{resolution.revoked_at && <span>已撤銷</span>}<span>{resolution.resolution_text}</span></p>
    {!resolution.revoked_at && <div className="form-actions">
      <button type="button" className="button button-quiet" onClick={() => { setTitle(resolution.title); setBody(resolution.resolution_text); setEdit(!edit); }}>{edit ? "取消修改" : "編輯決議"}</button>
      {!confirmRevoke && <button type="button" className="button button-quiet" onClick={() => setConfirmRevoke(true)}>撤銷決議</button>}
    </div>}
    {edit && !resolution.revoked_at && <form className="social-form" aria-label="編輯決議" onSubmit={event => { event.preventDefault(); if (!update.isPending) update.mutate(false); }}>
      <label className="field"><span>決議主旨</span><input required maxLength={160} value={title} onChange={event => setTitle(event.target.value)} /></label>
      <label className="field"><span>決議內容</span><textarea required maxLength={20000} value={body} onChange={event => setBody(event.target.value)} /></label>
      <button className="button button-system" disabled={update.isPending}>儲存決議修改</button>
    </form>}
    {confirmRevoke && <div className="form-actions">
      <button type="button" className="button button-quiet" disabled={update.isPending} onClick={() => setConfirmRevoke(false)}>取消</button>
      <button type="button" className="button button-danger" disabled={update.isPending} onClick={() => update.mutate(true)}>確認撤銷決議</button>
    </div>}
    {update.error && <p className="form-error" role="alert">{update.error.message}</p>}
    <details onToggle={event => setHistoryOpen(event.currentTarget.open)}>
      <summary>修改紀錄</summary>
      {historyOpen && (history.isPending ? <p role="status">讀取紀錄…</p> : history.error ? <p className="form-error" role="alert">{history.error.message}</p>
        : <ul>{history.data?.map(item => <li key={item.id}>
          <p>{formatDateTime(item.created_at)} · {item.actor} · {item.action === "meeting_resolution.revoke" ? "撤銷" : item.action === "meeting_resolution.create" ? "新增" : "修改"}</p>
          {item.before && <p>修改前　{item.before.title} {item.before.resolution_text}</p>}
          {item.after && (item.after.title || item.after.resolution_text) && <p>修改後　{item.after.title} {item.after.resolution_text}</p>}
        </li>)}</ul>)}
    </details>
  </article>;
}
