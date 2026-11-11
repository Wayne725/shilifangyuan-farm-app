import { HandHeart } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { useAuth } from "../context/AuthContext";
import { apiFetch } from "../lib/api";
import type { Wish } from "../lib/types";
import { OptionalMark } from "./OptionalMark";
import { DataState, LoadingLines } from "./Shared";

export function AdminWishesPanel() {
  const { user } = useAuth();
  const client = useQueryClient();
  const isAdmin = user?.user_role === "admin";
  const wishes = useQuery({ queryKey: ["wishes"], queryFn: () => apiFetch<Wish[]>("/v1/wishes"), enabled: isAdmin });
  if (!isAdmin) return null;

  return <section className="admin-review-section">
    <div className="section-title-row"><div><h2>社員願望</h2></div><span><HandHeart size={20} />{wishes.data?.length || 0}</span></div>
    {wishes.isPending ? <LoadingLines count={2} /> : wishes.isError ? (
      <DataState kind="error" title="社員願望無法讀取" detail={wishes.error.message} />
    ) : wishes.data.length === 0 ? <DataState title="目前沒有社員願望" detail="" /> : (
      <div className="admin-review-list">{wishes.data.map(wish => <WishAdminCard key={wish.id} wish={wish} onDone={() => client.invalidateQueries({ queryKey: ["wishes"] })} />)}</div>
    )}
  </section>;
}

function WishAdminCard({ wish, onDone }: { wish: Wish; onDone: () => void }) {
  const [status, setStatus] = useState(wish.status);
  const [note, setNote] = useState(wish.admin_note || "");
  const [productId, setProductId] = useState(wish.launched_product_id || "");
  const [campaignId, setCampaignId] = useState(wish.launched_campaign_id || "");
  const save = useMutation({
    mutationFn: () => apiFetch(`/v1/admin/wishes/${wish.id}/status`, {
      method: "PUT",
      body: JSON.stringify({ status, admin_note: note, launched_product_id: productId || null, launched_campaign_id: campaignId || null, proposer_points: 100 }),
    }),
    onSuccess: onDone,
  });

  return <article className="admin-review-card">
    <div className="review-card-copy"><span className="status-chip">{wish.support_count} 人支持</span><h3>{wish.name}</h3><p>{wish.description}</p></div>
    <div className="review-card-actions schedule-fields">
      <label className="field"><span>處理狀態</span><select value={status} onChange={event => setStatus(event.target.value)}>
        <option value="submitted">新提出</option><option value="gathering">募集支持</option><option value="sourcing">尋找供應</option><option value="launched">已成案</option><option value="declined">不採用</option>
      </select></label>
      <label className="field"><span>社員說明<OptionalMark /></span><input value={note} onChange={event => setNote(event.target.value)} /></label>
      {status === "launched" && <>
        <label className="field"><span>商品 ID</span><input value={productId} onChange={event => setProductId(event.target.value)} /></label>
        <label className="field"><span>共同購買 ID</span><input value={campaignId} onChange={event => setCampaignId(event.target.value)} /></label>
      </>}
      <button type="button" disabled={save.isPending || (status === "launched" && !productId && !campaignId)} onClick={() => save.mutate()}>儲存狀態</button>
      {save.isError && <p className="form-error">{save.error.message}</p>}
    </div>
  </article>;
}
