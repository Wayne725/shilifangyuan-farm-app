import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { apiFetch } from "../lib/api";
import { activityAccessDenied } from "../lib/activities";
import { taipeiDateTimeInput, taipeiInputToIso } from "../lib/meal-time";
import type { Activity } from "../lib/types";

const dates = [["starts_at", "開始時間"], ["ends_at", "結束時間"], ["registration_deadline", "報名截止"]] as const;

export function ActivityDetailsEditor({ activity, onDone, onDenied }: {
  activity: Activity; onDone: () => void; onDenied: () => void;
}) {
  const [open, setOpen] = useState(false);
  return <section>
    <button className="button button-quiet" type="button" aria-expanded={open} onClick={() => setOpen(!open)}>
      {open ? "收起活動資料" : "編輯活動"}
    </button>
    {open && <ActivityDetailsForm activity={activity} onDenied={onDenied} onDone={() => { setOpen(false); onDone(); }} />}
  </section>;
}

function ActivityDetailsForm({ activity, onDone, onDenied }: {
  activity: Activity; onDone: () => void; onDenied: () => void;
}) {
  const [original] = useState(activity);
  const [draft, setDraft] = useState(() => ({
    title: activity.title, description: activity.description, location: activity.location, capacity: String(activity.capacity),
    starts_at: taipeiDateTimeInput(activity.starts_at), ends_at: taipeiDateTimeInput(activity.ends_at),
    registration_deadline: taipeiDateTimeInput(activity.registration_deadline),
  }));
  const changedTime = dates.some(([key]) => draft[key] !== taipeiDateTimeInput(original[key]));
  const save = useMutation({
    mutationFn: () => apiFetch(`/v1/admin/activities/${activity.id}`, {
      method: "PATCH", body: JSON.stringify({
        ...draft, capacity: Number(draft.capacity), expected_updated_at: original.updated_at,
        ...Object.fromEntries(dates.map(([key]) => [
          key, draft[key] === taipeiDateTimeInput(original[key]) ? original[key] : taipeiInputToIso(draft[key]),
        ])),
      }),
    }),
    onSuccess: onDone,
    onError: error => { if (activityAccessDenied(error)) onDenied(); },
  });
  return <form className="social-form" aria-label="編輯活動" onSubmit={event => {
    event.preventDefault();
    if (!save.isPending) save.mutate();
  }}>
    <div className="field-grid two-columns">
      <label className="field"><span>活動標題</span><input required maxLength={160} value={draft.title} onChange={event => setDraft({ ...draft, title: event.target.value })} /></label>
      <label className="field"><span>地點</span><input required maxLength={240} value={draft.location} onChange={event => setDraft({ ...draft, location: event.target.value })} /></label>
      {dates.map(([key, label]) => <label className="field" key={key}><span>{label}（台灣時間）</span>
        <input type="datetime-local" required value={draft[key]} onChange={event => setDraft({ ...draft, [key]: event.target.value })} /></label>)}
      <label className="field"><span>名額</span><input type="number" required min={Math.max(activity.registration_count, 1)} max={100000} value={draft.capacity} onChange={event => setDraft({ ...draft, capacity: event.target.value })} /></label>
    </div>
    <label className="field"><span>活動說明</span><textarea maxLength={10000} value={draft.description} onChange={event => setDraft({ ...draft, description: event.target.value })} /></label>
    {(activity.registration_count > 0 || activity.waitlist_count > 0) && (changedTime || original.location !== draft.location.trim()) &&
      <p role="status">變更時間或地點後，系統會通知報名者。</p>}
    {save.error && <p className="form-error" role="alert">{save.error.message}</p>}
    <button className="button button-system" disabled={save.isPending}>儲存活動</button>
  </form>;
}
