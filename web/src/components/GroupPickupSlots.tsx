import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { apiFetch } from "../lib/api";
import { campaignAccessDenied } from "../lib/group-campaigns";
import type { GroupCampaign, GroupPickupSlot, GroupPickupSlotSummary } from "../lib/types";
import { PickupLocationPicker, pickupLocationSummary, usePickupLocations } from "./PickupLocationPicker";

const weekdays = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"];

export const defaultPickupSlots: GroupPickupSlot[] = [{ weekday: 4, starts_at: "11:30", ends_at: "13:00" }];

export function pickupSlotsError(slots: GroupPickupSlot[]) {
  if (!slots.length) return "請至少設定一個取貨時段";
  if (slots.some((slot) => !slot.starts_at || !slot.ends_at || slot.ends_at <= slot.starts_at)) return "取貨時段的結束時間必須晚於開始時間";
  const keys = slots.map((slot) => `${slot.weekday}-${slot.starts_at}-${slot.ends_at}`);
  if (new Set(keys).size !== keys.length) return "取貨時段不可重複";
  return "";
}

export function pickupSlotsPayload(slots: GroupPickupSlot[]) {
  return slots.map(({ id, weekday, starts_at, ends_at }) => ({ ...(id ? { id } : {}), weekday, starts_at, ends_at }));
}

export function PickupSlotEditor({ value, onChange, disabled }: { value: GroupPickupSlot[]; onChange: (slots: GroupPickupSlot[]) => void; disabled?: boolean }) {
  const update = (index: number, changes: Partial<GroupPickupSlot>) => onChange(value.map((slot, position) => position === index ? { ...slot, ...changes } : slot));
  const error = pickupSlotsError(value);
  return <fieldset className="pickup-slot-editor" disabled={disabled}>
    <legend>取貨時段（買家點選其中一個）</legend>
    {value.map((slot, index) => <div className="field-grid three-columns" key={slot.id || `new-${index}`}>
      <label className="field"><span>取貨日 {index + 1}</span><select value={slot.weekday} onChange={(event) => update(index, { weekday: Number(event.target.value) })}>{weekdays.map((label, day) => <option key={label} value={day}>每{label}</option>)}</select></label>
      <label className="field"><span>開始時間 {index + 1}</span><input type="time" required value={slot.starts_at} onChange={(event) => update(index, { starts_at: event.target.value })} /></label>
      <label className="field"><span>結束時間 {index + 1}</span><input type="time" required value={slot.ends_at} onChange={(event) => update(index, { ends_at: event.target.value })} /></label>
      {value.length > 1 && <button className="button button-quiet" type="button" onClick={() => onChange(value.filter((_, position) => position !== index))}>移除取貨日 {index + 1}</button>}
    </div>)}
    <button className="button button-quiet" type="button" disabled={value.length >= 21} onClick={() => {
      const last = value[value.length - 1] || defaultPickupSlots[0];
      onChange([...value, { weekday: (last.weekday + 1) % 7, starts_at: last.starts_at, ends_at: last.ends_at }]);
    }}>新增取貨時段</button>
    {error && <p className="form-error" role="alert">{error}</p>}
  </fieldset>;
}

export function CampaignPickupSlots({ campaign, onDone, onAccessDenied }: { campaign: GroupCampaign; onDone: () => void; onAccessDenied: () => void }) {
  const [draft, setDraft] = useState<GroupPickupSlot[] | null>(null);
  const [summaryOpen, setSummaryOpen] = useState(false);
  const summary = useQuery({
    queryKey: ["group-pickup-slot-summary", campaign.id, campaign.paid_quantity],
    queryFn: () => apiFetch<GroupPickupSlotSummary[]>(`/v1/group-campaigns/${campaign.id}/admin/pickup-slot-summary`),
    enabled: summaryOpen,
  });
  const save = useMutation({
    mutationFn: (slots: GroupPickupSlot[]) => apiFetch<GroupCampaign>(`/v1/group-campaigns/${campaign.id}`, {
      method: "PATCH", body: JSON.stringify({ pickup_slots: pickupSlotsPayload(slots) }),
    }),
    onSuccess: () => { setDraft(null); if (summaryOpen) void summary.refetch(); onDone(); },
    onError: (error) => { if (campaignAccessDenied(error)) onAccessDenied(); },
  });
  const slots = campaign.pickup_slots || [];
  return <section className="campaign-pickup-slots" aria-label={`${campaign.title} 取貨時段`}>
    <h4>取貨時段</h4>
    {draft ? <form onSubmit={(event) => { event.preventDefault(); if (!pickupSlotsError(draft)) save.mutate(draft); }}>
      <PickupSlotEditor value={draft} onChange={setDraft} disabled={save.isPending} />
      <p className="field-help">已成立的訂單保留下單時選的時段，不會跟著改。</p>
      {save.isError && <p className="form-error" role="alert">{save.error.message}</p>}
      <div className="form-actions"><button className="button button-quiet" type="button" disabled={save.isPending} onClick={() => setDraft(null)}>取消</button><button className="button button-primary" disabled={save.isPending || Boolean(pickupSlotsError(draft))}>儲存取貨時段</button></div>
    </form> : <>
      <p>{slots.length ? slots.map((slot) => slot.label).join("、") : "尚未設定取貨時段，買家暫時無法訂購"}</p>
      <button className="button button-quiet" type="button" onClick={() => { save.reset(); setDraft(slots.length ? slots.map((slot) => ({ ...slot })) : defaultPickupSlots.map((slot) => ({ ...slot }))); }}>{slots.length ? "編輯取貨時段" : "設定取貨時段"}</button>
    </>}
    {save.isSuccess && !draft && <p className="form-success" role="status">取貨時段已儲存</p>}
    <details className="admin-extra-actions" onToggle={(event) => setSummaryOpen(event.currentTarget.open)}><summary>依取貨時段查看已付款訂單</summary>
      {summary.isPending ? <p>讀取中…</p> : summary.isError ? <p className="form-error">{summary.error.message}</p> : <div className="admin-records-table" tabIndex={0} role="region" aria-label={`${campaign.title} 取貨時段彙整`}>
        <table><thead><tr><th>取貨時段</th><th>訂單數</th><th>數量</th><th>訂購人</th></tr></thead>
          <tbody>{summary.data.map((row) => <tr key={row.label}><td>{row.label}</td><td>{row.order_count}</td><td>{row.quantity}</td><td>{row.orders.length ? row.orders.map((order) => `${order.buyer_name || order.contact_email} ×${order.quantity}（${order.order_number}）`).join("、") : "—"}</td></tr>)}</tbody>
        </table>
      </div>}
    </details>
  </section>;
}

export function CampaignPickupLocations({ campaign, onDone, onAccessDenied }: { campaign: GroupCampaign; onDone: () => void; onAccessDenied: () => void }) {
  const [draft, setDraft] = useState<string[] | null>(null);
  const current = campaign.pickup_location_ids ?? [];
  const locations = usePickupLocations(current.length > 0, true);
  const save = useMutation({
    mutationFn: (ids: string[]) => apiFetch<GroupCampaign>(`/v1/group-campaigns/${campaign.id}`, {
      method: "PATCH", body: JSON.stringify({ pickup_location_ids: ids }),
    }),
    onSuccess: () => { setDraft(null); onDone(); },
    onError: (error) => { if (campaignAccessDenied(error)) onAccessDenied(); },
  });
  return <section className="campaign-pickup-locations" aria-label={`${campaign.title} 取貨地點`}>
    <h4>取貨地點</h4>
    {draft ? <form onSubmit={(event) => { event.preventDefault(); save.mutate(draft); }}>
      <PickupLocationPicker retail value={draft} onChange={setDraft} disabled={save.isPending} />
      <p className="field-help">已成立的訂單保留原本選的取貨地點，不會跟著改。</p>
      {save.isError && <p className="form-error" role="alert">{save.error.message}</p>}
      <div className="form-actions"><button className="button button-quiet" type="button" disabled={save.isPending} onClick={() => setDraft(null)}>取消</button><button className="button button-primary" disabled={save.isPending}>儲存取貨地點</button></div>
    </form> : <>
      <p>{pickupLocationSummary(current, locations.data)}</p>
      <button className="button button-quiet" type="button" onClick={() => { save.reset(); setDraft([...current]); }}>編輯取貨地點</button>
    </>}
    {save.isSuccess && !draft && <p className="form-success" role="status">取貨地點已儲存</p>}
  </section>;
}
