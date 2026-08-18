import * as Tabs from "@radix-ui/react-tabs";
import { Buildings, CheckCircle, Package, Plus } from "@phosphor-icons/react";
import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { type FormEvent, useMemo, useState } from "react";

import { AdminNav } from "../components/AdminNav";
import { DataState, LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import { apiFetch, formatDateTime, formatMoney } from "../lib/api";
import type { GroupBundle, GroupCampaign, GroupVoteProposal, Product } from "../lib/types";

export function AdminGroupsPage() {
  const { user, openLogin } = useAuth();
  const isAdmin = user?.user_role === "admin";
  const queryClient = useQueryClient();
  const [products, bundles, proposals, campaigns] = useQueries({ queries: [
    { queryKey: ["admin-products"], queryFn: () => apiFetch<Product[]>("/v1/products"), enabled: isAdmin },
    { queryKey: ["group-bundles"], queryFn: () => apiFetch<GroupBundle[]>("/v1/group-bundles"), enabled: isAdmin },
    { queryKey: ["group-vote-proposals"], queryFn: () => apiFetch<GroupVoteProposal[]>("/v1/vote-proposals"), enabled: isAdmin },
    { queryKey: ["group-campaigns"], queryFn: () => apiFetch<GroupCampaign[]>("/v1/group-campaigns"), enabled: isAdmin },
  ] });

  if (!isAdmin) {
    return <section className="admin-gate"><div className="admin-gate-mark"><Buildings size={38} weight="light" /></div><p className="eyebrow">GROUP OPERATIONS</p><h1>團購管理只向管理者開放。</h1>{!user ? <button className="button button-primary" type="button" onClick={openLogin}>管理者登入</button> : <Link className="button button-quiet" to="/shop">返回生活消費</Link>}</section>;
  }

  const refreshProposals = () => queryClient.invalidateQueries({ queryKey: ["group-vote-proposals"] });
  const refreshCampaigns = () => queryClient.invalidateQueries({ queryKey: ["group-campaigns"] });
  const refreshBundles = () => queryClient.invalidateQueries({ queryKey: ["group-bundles"] });

  return (
    <section className="admin-module-page">
      <header className="workspace-heading"><div><p className="eyebrow">GROUP OPERATIONS / 團購管理</p><h1>需求投票與正式團購</h1></div><p>投票確認需求，管理端審核後建立可付款、可配送的正式團購。</p></header>
      <AdminNav />
      <Tabs.Root className="admin-social-tabs" defaultValue="proposals">
        <Tabs.List className="tab-list" aria-label="團購管理分類"><Tabs.Trigger value="proposals">提案審核</Tabs.Trigger><Tabs.Trigger value="campaigns">正式團購</Tabs.Trigger><Tabs.Trigger value="bundles">固定套組</Tabs.Trigger></Tabs.List>
        <Tabs.Content className="tab-content" value="proposals">
          <AdminGroupSection title="團購提案" count={proposals.data?.length || 0} pending={proposals.isPending} error={proposals.error?.message}>
            <div className="admin-review-list">{proposals.data?.map((proposal) => <GroupProposalReview key={proposal.id} proposal={proposal} products={products.data || []} bundles={bundles.data || []} onDone={() => { refreshProposals(); refreshCampaigns(); }} />)}</div>
          </AdminGroupSection>
        </Tabs.Content>
        <Tabs.Content className="tab-content" value="campaigns">
          <CampaignCreateForm products={products.data || []} bundles={bundles.data || []} onDone={refreshCampaigns} />
          <AdminGroupSection title="正式團購" count={campaigns.data?.length || 0} pending={campaigns.isPending} error={campaigns.error?.message}>
            <div className="admin-review-list">{campaigns.data?.map((campaign) => <CampaignAdminCard key={campaign.id} campaign={campaign} onDone={refreshCampaigns} />)}</div>
          </AdminGroupSection>
        </Tabs.Content>
        <Tabs.Content className="tab-content" value="bundles">
          <BundleCreateForm products={products.data || []} onDone={refreshBundles} />
          <AdminGroupSection title="固定套組" count={bundles.data?.length || 0} pending={bundles.isPending} error={bundles.error?.message}>
            <div className="admin-record-grid">{bundles.data?.map((bundle) => <article className="admin-record-card" key={bundle.id}><Package size={22} weight="light" /><span className="status-chip passed">啟用</span><h3>{bundle.name}</h3><p>{bundle.items.map((item) => `${item.product_name} × ${item.quantity}`).join("、")}</p><dl><div><dt>社員價</dt><dd>{formatMoney(bundle.member_price)}</dd></div><div><dt>一般價</dt><dd>{formatMoney(bundle.nonmember_price)}</dd></div></dl></article>)}</div>
          </AdminGroupSection>
        </Tabs.Content>
      </Tabs.Root>
    </section>
  );
}

function AdminGroupSection({ title, count, pending, error, children }: { title: string; count: number; pending: boolean; error?: string; children: React.ReactNode }) {
  return <section className="admin-review-section"><div className="section-title-row"><div><p className="eyebrow">GROUP WORKFLOW</p><h2>{title}</h2></div><span>{count}</span></div>{pending && <LoadingLines count={3} />}{error && <DataState kind="error" title={`${title}無法讀取`} detail={error} />}{!pending && !error && count === 0 && <DataState title={`目前沒有${title}`} detail="新的項目會出現在這裡。" />}{children}</section>;
}

function GroupProposalReview({ proposal, products, bundles, onDone }: { proposal: GroupVoteProposal; products: Product[]; bundles: GroupBundle[]; onDone: () => void }) {
  const [threshold, setThreshold] = useState(proposal.threshold || 10);
  const [deadline, setDeadline] = useState(localDateTime(7));
  const [reason, setReason] = useState("");
  const target = proposal.target_type === "product" ? products.find((item) => item.id === proposal.target_id) : bundles.find((item) => item.id === proposal.target_id);
  const action = useMutation({
    mutationFn: async (decision: "approve" | "reject" | "convert") => {
      if (decision === "approve") return apiFetch(`/v1/vote-proposals/${proposal.id}/admin/approve`, { method: "POST", body: JSON.stringify({ threshold, deadline: new Date(deadline).toISOString() }) });
      if (decision === "reject") return apiFetch(`/v1/vote-proposals/${proposal.id}/admin/reject`, { method: "POST", body: JSON.stringify({ reason }) });
      if (!target) throw new Error("找不到提案對應項目");
      return apiFetch(`/v1/vote-proposals/${proposal.id}/admin/convert`, { method: "POST", body: JSON.stringify(campaignPayloadFromTarget(proposal, target)) });
    },
    onSuccess: onDone,
  });
  return (
    <article className="admin-review-card">
      <div className="review-card-copy"><span className="status-chip">{groupProposalStatus(proposal.status)}</span><h3>{proposal.target_name}</h3><p>{proposal.vote_count}/{proposal.threshold} 人 · 預估 {proposal.estimated_quantity} 組</p><small>{proposal.deadline ? `${formatDateTime(proposal.deadline)} 截止` : "待審核設定期限"}</small></div>
      <div className="review-card-actions schedule-fields">
        {proposal.status === "pending_review" && <><label className="field"><span>門檻人數</span><input min={1} type="number" value={threshold} onChange={(event) => setThreshold(Number(event.target.value))} /></label><label className="field"><span>投票截止</span><input type="datetime-local" value={deadline} onChange={(event) => setDeadline(event.target.value)} /></label></>}
        <label className="field"><span>處理原因</span><input value={reason} onChange={(event) => setReason(event.target.value)} /></label>
        <div>{proposal.status === "pending_review" && <button type="button" disabled={!deadline || action.isPending} onClick={() => action.mutate("approve")}><CheckCircle size={16} />通過並開放投票</button>}{proposal.status === "pending_review" && <button type="button" disabled={!reason.trim() || action.isPending} onClick={() => action.mutate("reject")}>不通過</button>}{proposal.status === "conversion_pending" && <button type="button" disabled={!target || action.isPending} onClick={() => action.mutate("convert")}>建立正式團購</button>}</div>
        {action.isError && <p className="form-error">{action.error.message}</p>}
      </div>
    </article>
  );
}

function CampaignCreateForm({ products, bundles, onDone }: { products: Product[]; bundles: GroupBundle[]; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [targetType, setTargetType] = useState<"product" | "bundle">("product");
  const [targetId, setTargetId] = useState("");
  const [minQuantity, setMinQuantity] = useState(10);
  const [supplyCap, setSupplyCap] = useState(30);
  const [perUserCap, setPerUserCap] = useState(5);
  const [deadline, setDeadline] = useState(localDateTime(7));
  const [pickupStart, setPickupStart] = useState(localDateTime(10));
  const [pickupEnd, setPickupEnd] = useState(localDateTime(11));
  const targets = targetType === "product" ? products : bundles;
  const target = targets.find((item) => item.id === targetId);
  const create = useMutation({
    mutationFn: () => {
      if (!target) throw new Error("請選擇團購項目");
      const product = targetType === "product" ? target as Product : null;
      return apiFetch<GroupCampaign>("/v1/group-campaigns", { method: "POST", body: JSON.stringify({ target_type: targetType, target_id: target.id, title: `${target.name}共同購買`, description: target.description || "由管理員直接開放共同購買。", image_url: target.image_url || null, member_price: target.member_price, nonmember_price: target.nonmember_price, min_paid_quantity: minQuantity, supply_cap: supplyCap, per_user_cap: perUserCap, deadline: new Date(deadline).toISOString(), estimated_pickup_start: new Date(pickupStart).toISOString(), estimated_pickup_end: new Date(pickupEnd).toISOString(), can_ship: product?.can_ship || false, shipping_temperature: product?.can_ship ? product.shipping_temperature : null, allowed_shipping_channels: product?.can_ship ? product.allowed_shipping_channels : [] }) });
    },
    onSuccess: () => { setOpen(false); setTargetId(""); onDone(); },
  });
  return <section className="admin-create-block"><button className="button button-system" type="button" onClick={() => setOpen((value) => !value)}><Plus size={17} />直接建立正式團購</button>{open && <form className="social-form admin-master-form" onSubmit={(event) => { event.preventDefault(); create.mutate(); }}><div className="form-heading"><div><p className="eyebrow">DIRECT CAMPAIGN</p><h2>直接開團</h2></div><span>不經投票，直接使用既有商品或套組。</span></div><div className="field-grid three-columns"><label className="field"><span>項目類型</span><select value={targetType} onChange={(event) => { setTargetType(event.target.value as "product" | "bundle"); setTargetId(""); }}><option value="product">單一商品</option><option value="bundle">固定套組</option></select></label><label className="field"><span>團購項目</span><select required value={targetId} onChange={(event) => setTargetId(event.target.value)}><option value="">選擇項目</option>{targets.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label className="field"><span>付款門檻</span><input min={1} type="number" value={minQuantity} onChange={(event) => setMinQuantity(Number(event.target.value))} /></label><label className="field"><span>供應上限</span><input min={1} type="number" value={supplyCap} onChange={(event) => setSupplyCap(Number(event.target.value))} /></label><label className="field"><span>每人上限</span><input min={1} type="number" value={perUserCap} onChange={(event) => setPerUserCap(Number(event.target.value))} /></label><label className="field"><span>付款截止</span><input type="datetime-local" value={deadline} onChange={(event) => setDeadline(event.target.value)} /></label><label className="field"><span>預計取貨開始</span><input type="datetime-local" value={pickupStart} onChange={(event) => setPickupStart(event.target.value)} /></label><label className="field"><span>預計取貨結束</span><input type="datetime-local" value={pickupEnd} onChange={(event) => setPickupEnd(event.target.value)} /></label></div>{create.isError && <p className="form-error">{create.error.message}</p>}<div className="form-actions"><button className="button button-quiet" type="button" onClick={() => setOpen(false)}>取消</button><button className="button button-primary" disabled={!target || create.isPending}>建立團購</button></div></form>}</section>;
}

function CampaignAdminCard({ campaign, onDone }: { campaign: GroupCampaign; onDone: () => void }) {
  const [pickupAt, setPickupAt] = useState(localDateTime(2));
  const [reason, setReason] = useState("");
  const action = useMutation({
    mutationFn: (decision: "confirm" | "reject" | "cancel") => apiFetch<GroupCampaign>(`/v1/group-campaigns/${campaign.id}/admin/${decision}`, { method: "POST", body: JSON.stringify(decision === "confirm" ? { final_pickup_at: new Date(pickupAt).toISOString() } : { reason }) }),
    onSuccess: onDone,
  });
  return <article className="admin-review-card"><div className="review-card-copy"><span className="status-chip">{campaignDecisionLabel(campaign.decision_status)}</span><h3>{campaign.title}</h3><p>已付款 {campaign.paid_quantity}/{campaign.min_paid_quantity} · 保留 {campaign.reserved_quantity} · 剩餘 {campaign.available_quantity}</p><progress className="admin-progress" max={campaign.min_paid_quantity} value={campaign.paid_quantity} /><small>{formatDateTime(campaign.deadline)} 截止 · {campaign.can_ship ? "可配送" : "合作社取貨"}</small></div><div className="review-card-actions schedule-fields"><label className="field"><span>最終取貨時間</span><input type="datetime-local" value={pickupAt} onChange={(event) => setPickupAt(event.target.value)} /></label><label className="field"><span>取消／拒絕原因</span><input value={reason} onChange={(event) => setReason(event.target.value)} /></label><div>{campaign.decision_status === "pending_confirmation" && <button type="button" disabled={action.isPending} onClick={() => action.mutate("confirm")}><CheckCircle size={16} />確認成團</button>}{campaign.decision_status === "pending_confirmation" && <button type="button" disabled={!reason.trim() || action.isPending} onClick={() => action.mutate("reject")}>不成團</button>}{campaign.decision_status === "confirmed" && <button type="button" disabled={!reason.trim() || action.isPending} onClick={() => action.mutate("cancel")}>取消團購</button>}<Link to="/groups/$campaignId" params={{ campaignId: campaign.id }}>查看前台</Link></div>{action.isError && <p className="form-error">{action.error.message}</p>}</div></article>;
}

function BundleCreateForm({ products, onDone }: { products: Product[]; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [memberPrice, setMemberPrice] = useState(0);
  const [nonmemberPrice, setNonmemberPrice] = useState(0);
  const [items, setItems] = useState([{ product_id: "", quantity: 1 }]);
  const validItems = useMemo(() => items.filter((item) => item.product_id), [items]);
  const create = useMutation({ mutationFn: () => apiFetch<GroupBundle>("/v1/group-bundles", { method: "POST", body: JSON.stringify({ name, description, member_price: memberPrice, nonmember_price: nonmemberPrice, items: validItems, is_active: true }) }), onSuccess: () => { setOpen(false); setName(""); setDescription(""); setItems([{ product_id: "", quantity: 1 }]); onDone(); } });
  return <section className="admin-create-block"><button className="button button-system" type="button" onClick={() => setOpen((value) => !value)}><Plus size={17} />建立固定套組</button>{open && <form className="social-form admin-master-form" onSubmit={(event) => { event.preventDefault(); create.mutate(); }}><div className="form-heading"><div><p className="eyebrow">BUNDLE RECORD</p><h2>建立固定套組</h2></div></div><div className="field-grid two-columns"><label className="field"><span>套組名稱</span><input required value={name} onChange={(event) => setName(event.target.value)} /></label><label className="field"><span>社員價</span><input min={0} type="number" value={memberPrice} onChange={(event) => setMemberPrice(Number(event.target.value))} /></label><label className="field"><span>一般價</span><input min={0} type="number" value={nonmemberPrice} onChange={(event) => setNonmemberPrice(Number(event.target.value))} /></label><label className="field wide"><span>說明</span><textarea rows={3} value={description} onChange={(event) => setDescription(event.target.value)} /></label></div><div className="bundle-item-editor">{items.map((item, index) => <div key={index}><label className="field"><span>商品</span><select value={item.product_id} onChange={(event) => setItems((current) => current.map((row, rowIndex) => rowIndex === index ? { ...row, product_id: event.target.value } : row))}><option value="">選擇商品</option>{products.filter((product) => product.is_active).map((product) => <option key={product.id} value={product.id}>{product.name}</option>)}</select></label><label className="field"><span>數量</span><input min={1} type="number" value={item.quantity} onChange={(event) => setItems((current) => current.map((row, rowIndex) => rowIndex === index ? { ...row, quantity: Number(event.target.value) } : row))} /></label><button type="button" onClick={() => setItems((current) => current.filter((_, rowIndex) => rowIndex !== index))}>移除</button></div>)}</div><button className="button button-quiet" type="button" onClick={() => setItems((current) => [...current, { product_id: "", quantity: 1 }])}><Plus size={16} />加入商品</button>{memberPrice > nonmemberPrice && <p className="form-error">社員價不可高於一般價</p>}{create.isError && <p className="form-error">{create.error.message}</p>}<div className="form-actions"><button className="button button-quiet" type="button" onClick={() => setOpen(false)}>取消</button><button className="button button-primary" disabled={!name.trim() || validItems.length === 0 || memberPrice > nonmemberPrice || create.isPending}>儲存套組</button></div></form>}</section>;
}

function campaignPayloadFromTarget(proposal: GroupVoteProposal, target: Product | GroupBundle) {
  const now = Date.now();
  const product = proposal.target_type === "product" ? target as Product : null;
  return { source_proposal_id: proposal.id, target_type: proposal.target_type, target_id: target.id, title: `${proposal.target_name}共同購買`, description: "依投票需求開放付款，達門檻後由合作社確認成團。", image_url: target.image_url || null, member_price: Math.round(target.member_price * 0.92), nonmember_price: Math.round(target.nonmember_price * 0.95), min_paid_quantity: Math.max(proposal.threshold, 1), supply_cap: Math.max(proposal.estimated_quantity, proposal.threshold * 3, 1), per_user_cap: 5, deadline: new Date(now + 7 * 86400000).toISOString(), estimated_pickup_start: new Date(now + 10 * 86400000).toISOString(), estimated_pickup_end: new Date(now + 11 * 86400000).toISOString(), can_ship: product?.can_ship || false, shipping_temperature: product?.can_ship ? product.shipping_temperature : null, allowed_shipping_channels: product?.can_ship ? product.allowed_shipping_channels : [] };
}

function localDateTime(daysAhead: number) {
  const date = new Date(Date.now() + daysAhead * 86400000);
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 16);
}

function groupProposalStatus(status: string) { return { pending_review: "待審核", voting: "投票中", conversion_pending: "待開團", converted: "已開團", rejected: "未通過", expired: "已結束" }[status] || status; }
function campaignDecisionLabel(status: string) { return { recruiting: "募集中", pending_confirmation: "待成團確認", confirmed: "已成團", rejected: "不成團", failed_unmet: "未達門檻", expired_unconfirmed: "逾期未確認", cancelled: "已取消" }[status] || status; }
