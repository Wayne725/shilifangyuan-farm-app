import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { useDeadlineClock } from "../lib/use-deadline-clock";
import { campaignAccessDenied, campaignQuantityBlockReason } from "../lib/group-campaigns";
import { apiFetch } from "../lib/api";
import type { GroupCampaign } from "../lib/types";

export function CampaignQuantityEditor({ campaign, unit = "組", onDone, onAccessDenied }: { campaign: GroupCampaign; unit?: string; onDone: () => void; onAccessDenied?: () => void }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(() => quantities(campaign));
  const [original, setOriginal] = useState(() => quantities(campaign));
  const [saved, setSaved] = useState(false);
  const now = useDeadlineClock(campaign.deadline);
  const blockReason = campaignQuantityBlockReason(campaign, now);
  const closed = Boolean(blockReason);
  const error = Object.values(draft).some((value) => !Number.isInteger(value) || value < 1) ? "數量必須是大於 0 的整數"
    : draft.min_paid_quantity > draft.supply_cap ? "成團所需總數量不可高於本團供應上限" : "";
  const changes = Object.fromEntries(Object.entries(draft).filter(([key, value]) => value !== original[key as keyof typeof draft]));
  const update = useMutation({
    mutationFn: () => apiFetch<GroupCampaign>(`/v1/group-campaigns/${campaign.id}`, { method: "PATCH", body: JSON.stringify(changes) }),
    onSuccess: () => { setOpen(false); setSaved(true); onDone(); },
    onError: error => { if (campaignAccessDenied(error)) onAccessDenied?.(); },
  });
  return <section className="admin-create-block campaign-quantity-settings" aria-label="共同購買數量設定">
    <dl className="campaign-quantity-summary">
      <div><dt>每人限購</dt><dd>{campaign.per_user_cap} {unit}</dd></div>
      <div><dt>成團所需總數量</dt><dd>{campaign.min_paid_quantity} {unit}</dd></div>
      <div><dt>本團供應上限</dt><dd>{campaign.supply_cap} {unit}</dd></div>
    </dl>
    {closed ? <p role="status"><span className="status-chip">{blockReason}</span></p> :
      <button className="button button-system" type="button" disabled={update.isPending} onClick={() => { setDraft(quantities(campaign)); setOriginal(quantities(campaign)); setOpen((value) => !value); setSaved(false); update.reset(); }}>編輯數量設定</button>}
    {saved && <p className="form-success" role="status">共同購買數量設定已儲存</p>}
    {open && !closed && <form className="social-form admin-master-form" aria-label="編輯共同購買數量" onSubmit={(event) => { event.preventDefault(); if (!error && Object.keys(changes).length) update.mutate(); }}>
      <div className="form-heading"><div><p className="eyebrow">CAMPAIGN QUANTITIES / 數量設定</p><h3>編輯共同購買數量</h3></div></div>
      <div className="field-grid three-columns">
        {([
          ["per_user_cap", "每人限購數量"],
          ["min_paid_quantity", "成團所需總數量"],
          ["supply_cap", "本團供應上限"],
        ] as const).map(([key, label]) => <label className="field" key={key}><span>{label}（{unit}）</span><input required min={1} step={1} type="number" disabled={update.isPending} value={draft[key]} onChange={(event) => setDraft({ ...draft, [key]: Number(event.target.value) })} /></label>)}
      </div>
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="form-actions"><button className="button button-quiet" type="button" disabled={update.isPending} onClick={() => { setOpen(false); update.reset(); }}>取消</button><button className="button button-primary" disabled={Boolean(error) || !Object.keys(changes).length || update.isPending}>儲存數量設定</button></div>
    </form>}
    {update.isError && <p className="form-error" role="alert">{update.error.message}</p>}
  </section>;
}

function quantities(campaign: GroupCampaign) {
  return { per_user_cap: campaign.per_user_cap, min_paid_quantity: campaign.min_paid_quantity, supply_cap: campaign.supply_cap };
}
