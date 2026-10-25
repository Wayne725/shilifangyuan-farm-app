import * as Tabs from "@radix-ui/react-tabs";
import { Buildings, CheckCircle, Package, Plus } from "@phosphor-icons/react";
import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";

import { AdminNav } from "../components/AdminNav";
import { CampaignReviewPanel } from "../components/CampaignReviewPanel";
import { CampaignQuantityEditor } from "../components/CampaignQuantityEditor";
import { DataState, LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import { apiFetch, formatDateTime, formatMoney } from "../lib/api";
import { campaignAccessDenied, campaignDateTime, campaignScheduleError, campaignStatusLabel, defaultReviewDeadline, groupProposalStatusLabel, retryCampaignRead } from "../lib/group-campaigns";
import { taipeiDateTimeInput, taipeiInputToIso } from "../lib/meal-time";
import { useDeadlineClock } from "../lib/use-deadline-clock";
import type { GroupBundle, GroupCampaign, GroupVoteProposal, Product } from "../lib/types";

export function AdminGroupsPage() {
  const { user, openLogin } = useAuth();
  const isAdmin = user?.user_role === "admin";
  if (!isAdmin) {
    return <section className="admin-gate"><div className="admin-gate-mark"><Buildings size={38} weight="light" /></div><p className="eyebrow">GROUP OPERATIONS / 共同購買管理</p><h1>共同購買管理只向管理者開放。</h1>{!user ? <button className="button button-primary" type="button" onClick={openLogin}>管理者登入</button> : <Link className="button button-quiet" to="/shop">返回生活消費</Link>}</section>;
  }
  return <AdminGroupsWorkspace key={user.id} userId={user.id} />;
}

function AdminGroupsWorkspace({ userId }: { userId: string }) {
  const queryClient = useQueryClient();
  const [revoked, setRevoked] = useState(false);
  const [products, bundles, proposals, campaigns] = useQueries({ queries: [
    { queryKey: ["admin-products", userId], queryFn: () => apiFetch<Product[]>("/v1/products"), enabled: !revoked, retry: retryCampaignRead },
    { queryKey: ["group-bundles", userId], queryFn: () => apiFetch<GroupBundle[]>("/v1/group-bundles"), enabled: !revoked, retry: retryCampaignRead },
    { queryKey: ["group-vote-proposals", userId], queryFn: () => apiFetch<GroupVoteProposal[]>("/v1/vote-proposals"), enabled: !revoked, retry: retryCampaignRead },
    { queryKey: ["group-campaigns", "admin", userId], queryFn: () => apiFetch<GroupCampaign[]>("/v1/group-campaigns"), enabled: !revoked, retry: retryCampaignRead, refetchInterval: revoked ? false : 30000 },
  ] });
  const denied = revoked || [products, bundles, proposals, campaigns].some(query => campaignAccessDenied(query.error));
  useEffect(() => { if (denied) setRevoked(true); }, [denied]);
  const onAccessDenied = () => setRevoked(true);
  if (denied) return <section className="admin-gate"><DataState kind="error" title="共同購買管理權限已失效" detail="請重新登入管理員帳號" /></section>;

  const refreshProposals = () => queryClient.invalidateQueries({ queryKey: ["group-vote-proposals"] });
  const refreshCampaigns = () => queryClient.invalidateQueries({ queryKey: ["group-campaigns"] });
  const refreshBundles = () => queryClient.invalidateQueries({ queryKey: ["group-bundles"] });

  return (
    <section className="admin-module-page">
      <header className="workspace-heading"><div><p className="eyebrow">GROUP OPERATIONS / 共同購買管理</p><h1>需求投票與正式共同購買</h1></div></header>
      <AdminNav />
      <Tabs.Root className="admin-social-tabs" defaultValue="proposals">
        <Tabs.List className="tab-list" aria-label="共同購買管理分類"><Tabs.Trigger value="proposals">提案審核</Tabs.Trigger><Tabs.Trigger value="campaigns">正式共同購買</Tabs.Trigger><Tabs.Trigger value="bundles">固定套組</Tabs.Trigger></Tabs.List>
        <Tabs.Content className="tab-content" value="proposals">
          <AdminGroupSection title="共同購買提案" count={proposals.data?.length || 0} pending={proposals.isPending} error={proposals.error?.message}>
            <div className="admin-review-list">{proposals.data?.map((proposal) => <GroupProposalReview key={proposal.id} proposal={proposal} campaign={campaigns.data?.find((campaign) => campaign.source_proposal_id === proposal.id)} products={products.data || []} bundles={bundles.data || []} onAccessDenied={onAccessDenied} onDone={() => { refreshProposals(); refreshCampaigns(); }} />)}</div>
          </AdminGroupSection>
        </Tabs.Content>
        <Tabs.Content className="tab-content" value="campaigns">
          <CampaignCreateForm products={products.data || []} bundles={bundles.data || []} onAccessDenied={onAccessDenied} onDone={refreshCampaigns} />
          <AdminGroupSection title="正式共同購買" count={campaigns.data?.length || 0} pending={campaigns.isPending} error={campaigns.error?.message}>
            <div className="admin-review-list">{campaigns.data?.map((campaign) => <CampaignAdminCard key={campaign.id} campaign={campaign} products={products.data || []} onAccessDenied={onAccessDenied} onDone={refreshCampaigns} />)}</div>
          </AdminGroupSection>
        </Tabs.Content>
        <Tabs.Content className="tab-content" value="bundles">
          <BundleCreateForm products={products.data || []} onAccessDenied={onAccessDenied} onDone={refreshBundles} />
          <AdminGroupSection title="固定套組" count={bundles.data?.length || 0} pending={bundles.isPending} error={bundles.error?.message}>
            <div className="admin-record-grid">{bundles.data?.map((bundle) => <article className="admin-record-card" key={bundle.id}><Package size={22} weight="light" /><span className="status-chip passed">啟用</span><h3>{bundle.name}</h3><p>{bundle.items.map((item) => `${item.product_name} × ${item.quantity}`).join("、")}</p><dl><div><dt>社員價</dt><dd>{formatMoney(bundle.member_price)}</dd></div><div><dt>一般價</dt><dd>{formatMoney(bundle.nonmember_price)}</dd></div></dl></article>)}</div>
          </AdminGroupSection>
        </Tabs.Content>
      </Tabs.Root>
    </section>
  );
}

function AdminGroupSection({ title, count, pending, error, children }: { title: string; count: number; pending: boolean; error?: string; children: React.ReactNode }) {
  return <section className="admin-review-section"><div className="section-title-row"><div><p className="eyebrow">GROUP WORKFLOW / 共同購買流程</p><h2>{title}</h2></div><span>{count}</span></div>{pending && <LoadingLines count={3} />}{error && <DataState kind="error" title={`${title}無法讀取`} detail={error} />}{!pending && !error && count === 0 && <DataState title={`目前沒有${title}`} detail="新的項目會出現在這裡。" />}{children}</section>;
}

function GroupProposalReview({ proposal, campaign, products, bundles, onDone, onAccessDenied }: { proposal: GroupVoteProposal; campaign?: GroupCampaign; products: Product[]; bundles: GroupBundle[]; onDone: () => void; onAccessDenied: () => void }) {
  const [threshold, setThreshold] = useState(proposal.threshold || 10);
  const [deadline, setDeadline] = useState(localDateTime(7));
  const [reason, setReason] = useState("");
  const action = useMutation({
    mutationFn: async (decision: "approve" | "reject") => {
      if (decision === "approve") return apiFetch(`/v1/vote-proposals/${proposal.id}/admin/approve`, { method: "POST", body: JSON.stringify({ threshold, deadline: taipeiInputToIso(deadline) }) });
      if (decision === "reject") return apiFetch(`/v1/vote-proposals/${proposal.id}/admin/reject`, { method: "POST", body: JSON.stringify({ reason }) });

    },
    onSuccess: onDone,
    onError: error => { if (campaignAccessDenied(error)) onAccessDenied(); },
  });
  return (
    <article className="admin-review-card group-proposal-card">
      <div className="review-card-copy"><span className="status-chip">{groupProposalStatusLabel(proposal.status)}</span><h3>{proposal.target_name}</h3>{proposal.request_details && <p className="group-request-details">{proposal.request_details}</p>}<p>{campaign ? `已付款 ${campaign.paid_quantity}/${campaign.min_paid_quantity} ${campaignUnit(campaign, products)}` : proposal.status === "converted" ? "已開團" : `投票 ${proposal.vote_count}/${proposal.threshold} 人 · 預估 ${proposal.estimated_quantity} 組`}</p><small>{campaign ? `${campaignDateTime(campaign.deadline)} 付款截止` : proposal.status === "converted" ? "" : proposal.deadline ? `${formatDateTime(proposal.deadline)} 投票截止` : "待審核設定期限"}</small></div>
      <div className="review-card-actions schedule-fields">
        {proposal.status === "pending_review" && <><label className="field"><span>投票門檻（人）</span><input min={1} type="number" value={threshold} onChange={(event) => setThreshold(Number(event.target.value))} /></label><label className="field"><span>投票截止（台灣）</span><input type="datetime-local" value={deadline} onChange={(event) => setDeadline(event.target.value)} /></label></>}

        {proposal.status === "pending_review" && <label className="field"><span>處理原因</span><input value={reason} onChange={(event) => setReason(event.target.value)} /></label>}
        <div>{proposal.status === "pending_review" && <button type="button" disabled={!deadline || action.isPending} onClick={() => action.mutate("approve")}><CheckCircle size={16} />通過並開放投票</button>}{proposal.status === "pending_review" && <button type="button" disabled={!reason.trim() || action.isPending} onClick={() => action.mutate("reject")}>不通過</button>}</div>
        {action.isError && <p className="form-error">{action.error.message}</p>}
      </div>
        {["pending_review", "voting", "conversion_pending"].includes(proposal.status) && <CampaignCreateForm proposal={proposal} products={products} bundles={bundles} onAccessDenied={onAccessDenied} onDone={onDone} />}
        {campaign && <CampaignReviewPanel campaign={campaign} onAccessDenied={onAccessDenied} onDone={onDone} />}
        {campaign && <CampaignQuantityEditor campaign={campaign} unit={campaignUnit(campaign, products)} onAccessDenied={onAccessDenied} onDone={onDone} />}
    </article>
  );
}

function CampaignCreateForm({ products, bundles, proposal, onDone, onAccessDenied }: { products: Product[]; bundles: GroupBundle[]; proposal?: GroupVoteProposal; onDone: () => void; onAccessDenied: () => void }) {
  const [open, setOpen] = useState(false);
  const [targetType, setTargetType] = useState<"product" | "bundle">(proposal?.target_type === "bundle" ? "bundle" : "product");
  const [targetId, setTargetId] = useState(proposal?.target_id || "");
  const [minQuantity, setMinQuantity] = useState(proposal ? 1 : 10);
  const [memberPrice, setMemberPrice] = useState(0);
  const [nonmemberPrice, setNonmemberPrice] = useState(0);
  const [supplyCap, setSupplyCap] = useState(30);
  const [perUserCap, setPerUserCap] = useState(5);
  const [deadline, setDeadline] = useState(localDateTime(7));
  const [reviewDeadline, setReviewDeadline] = useState(() => defaultReviewDeadline(deadline));
  const [reviewEdited, setReviewEdited] = useState(false);
  const [pickupStart, setPickupStart] = useState(localDateTime(11));
  const [pickupEnd, setPickupEnd] = useState(localDateTime(12));
  const scheduleError = campaignScheduleError(deadline, reviewDeadline, pickupStart, pickupEnd);
  const targets = (targetType === "product" ? products : bundles).filter((item) => item.is_active);
  const target = targets.find((item) => item.id === targetId);
  useEffect(() => { setMemberPrice(target?.member_price || 0); setNonmemberPrice(target?.nonmember_price || 0); }, [target?.id, target?.member_price, target?.nonmember_price]);
  const create = useMutation({
    mutationFn: () => {
      if (!target) throw new Error("請選擇共同購買項目");
      if (scheduleError) throw new Error(scheduleError);
      const product = targetType === "product" ? target as Product : null;
      const path = proposal ? `/v1/vote-proposals/${proposal.id}/admin/open-direct` : "/v1/group-campaigns";
      return apiFetch<GroupCampaign>(path, { method: "POST", body: JSON.stringify({ target_type: targetType, target_id: target.id, title: `${target.name}共同購買`, description: target.description || "由管理員直接開放共同購買。", image_url: target.image_url || null, member_price: memberPrice, nonmember_price: nonmemberPrice, min_paid_quantity: minQuantity, supply_cap: supplyCap, per_user_cap: perUserCap, deadline: taipeiInputToIso(deadline), confirmation_deadline: taipeiInputToIso(reviewDeadline), estimated_pickup_start: taipeiInputToIso(pickupStart), estimated_pickup_end: taipeiInputToIso(pickupEnd), can_ship: product?.can_ship || false, shipping_temperature: product?.can_ship ? product.shipping_temperature : null, allowed_shipping_channels: product?.can_ship ? product.allowed_shipping_channels : [] }) });
    },
    onSuccess: () => { setOpen(false); setTargetId(""); onDone(); },
    onError: error => { if (campaignAccessDenied(error)) onAccessDenied(); },
  });
  return <section className="admin-create-block"><button className="button button-system" type="button" onClick={() => setOpen((value) => !value)}><Plus size={17} />{proposal ? "直接確認並開團" : "直接建立正式共同購買"}</button>{open && <form className="social-form admin-master-form" onSubmit={(event) => { event.preventDefault(); if (!create.isPending) create.mutate(); }}><div className="form-heading"><div><p className="eyebrow">DIRECT CAMPAIGN / 直接開團</p><h2>直接開團</h2></div></div><div className="field-grid three-columns"><label className="field"><span>項目類型</span><select disabled={Boolean(proposal && proposal.target_type !== "custom")} value={targetType} onChange={(event) => { setTargetType(event.target.value as "product" | "bundle"); setTargetId(""); }}><option value="product">單一商品</option><option value="bundle">固定套組</option></select></label><label className="field"><span>共同購買項目</span><select required disabled={Boolean(proposal && proposal.target_type !== "custom")} value={targetId} onChange={(event) => setTargetId(event.target.value)}><option value="">選擇項目</option>{targets.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label className="field"><span>社員價</span><input type="number" min={0} required value={memberPrice} onChange={(event) => setMemberPrice(Number(event.target.value))} /></label><label className="field"><span>一般價</span><input type="number" min={0} required value={nonmemberPrice} onChange={(event) => setNonmemberPrice(Number(event.target.value))} /></label><label className="field"><span>成團所需總數量</span><input min={1} type="number" value={minQuantity} onChange={(event) => setMinQuantity(Number(event.target.value))} /></label><label className="field"><span>供應上限</span><input min={1} type="number" value={supplyCap} onChange={(event) => setSupplyCap(Number(event.target.value))} /></label><label className="field"><span>每人限購數量</span><input min={1} type="number" value={perUserCap} onChange={(event) => setPerUserCap(Number(event.target.value))} /></label><label className="field"><span>付款截止（台灣）</span><input type="datetime-local" required value={deadline} onChange={(event) => { setDeadline(event.target.value); if (!reviewEdited) setReviewDeadline(defaultReviewDeadline(event.target.value)); }} /></label><label className="field"><span>審核截止（台灣）</span><input type="datetime-local" required value={reviewDeadline} onChange={event => { setReviewEdited(true); setReviewDeadline(event.target.value); }} /></label><label className="field"><span>預計取貨開始（台灣）</span><input type="datetime-local" required value={pickupStart} onChange={(event) => setPickupStart(event.target.value)} /></label><label className="field"><span>預計取貨結束（台灣）</span><input type="datetime-local" required value={pickupEnd} onChange={(event) => setPickupEnd(event.target.value)} /></label></div>{memberPrice > nonmemberPrice && <p className="form-error">社員價不可高於一般價</p>}{supplyCap < minQuantity && <p className="form-error">供應上限不可低於付款門檻</p>}{scheduleError && <p className="form-error" role="alert">{scheduleError}</p>}{create.isError && <p className="form-error">{create.error.message}</p>}<div className="form-actions"><button className="button button-quiet" type="button" onClick={() => setOpen(false)}>取消</button><button className="button button-primary" disabled={Boolean(scheduleError) || !target || memberPrice > nonmemberPrice || supplyCap < minQuantity || create.isPending}>{proposal ? "確認開放共同購買" : "建立共同購買"}</button></div></form>}</section>;
}

function CampaignAdminCard({ campaign, products, onDone, onAccessDenied }: { campaign: GroupCampaign; products: Product[]; onDone: () => void; onAccessDenied: () => void }) {
  const now = useDeadlineClock(campaign.deadline);
  return <article className="admin-review-card group-proposal-card" aria-label={`共同購買 ${campaign.title}`}>
    <div className="review-card-copy"><span className="status-chip">{campaignStatusLabel(campaign, now)}</span><h3>{campaign.title}</h3><p>已付款 {campaign.paid_quantity}/{campaign.min_paid_quantity} · 保留 {campaign.reserved_quantity} · 剩餘 {campaign.available_quantity}</p><progress className="admin-progress" max={campaign.min_paid_quantity} value={campaign.paid_quantity} /><small>{campaignDateTime(campaign.deadline)} 付款截止 · {campaign.can_ship ? "可配送" : "合作社取貨"}</small></div>
    <Link to="/groups/$campaignId" params={{ campaignId: campaign.id }}>查看前台</Link>
    <CampaignReviewPanel campaign={campaign} onAccessDenied={onAccessDenied} onDone={onDone} />
    <CampaignQuantityEditor campaign={campaign} unit={campaignUnit(campaign, products)} onAccessDenied={onAccessDenied} onDone={onDone} />
  </article>;
}

function BundleCreateForm({ products, onDone, onAccessDenied }: { products: Product[]; onDone: () => void; onAccessDenied: () => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [memberPrice, setMemberPrice] = useState(0);
  const [nonmemberPrice, setNonmemberPrice] = useState(0);
  const [items, setItems] = useState([{ product_id: "", quantity: 1 }]);
  const validItems = useMemo(() => items.filter((item) => item.product_id), [items]);
  const create = useMutation({ mutationFn: () => apiFetch<GroupBundle>("/v1/group-bundles", { method: "POST", body: JSON.stringify({ name, description, member_price: memberPrice, nonmember_price: nonmemberPrice, items: validItems, is_active: true }) }), onSuccess: () => { setOpen(false); setName(""); setDescription(""); setItems([{ product_id: "", quantity: 1 }]); onDone(); }, onError: error => { if (campaignAccessDenied(error)) onAccessDenied(); } });
  return <section className="admin-create-block"><button className="button button-system" type="button" onClick={() => setOpen((value) => !value)}><Plus size={17} />建立固定套組</button>{open && <form className="social-form admin-master-form" onSubmit={(event) => { event.preventDefault(); if (!create.isPending) create.mutate(); }}><div className="form-heading"><div><p className="eyebrow">BUNDLE RECORD / 商品套組</p><h2>建立固定套組</h2></div></div><div className="field-grid two-columns"><label className="field"><span>套組名稱</span><input required value={name} onChange={(event) => setName(event.target.value)} /></label><label className="field"><span>社員價</span><input min={0} type="number" value={memberPrice} onChange={(event) => setMemberPrice(Number(event.target.value))} /></label><label className="field"><span>一般價</span><input min={0} type="number" value={nonmemberPrice} onChange={(event) => setNonmemberPrice(Number(event.target.value))} /></label><label className="field wide"><span>說明</span><textarea rows={3} value={description} onChange={(event) => setDescription(event.target.value)} /></label></div><div className="bundle-item-editor">{items.map((item, index) => <div key={index}><label className="field"><span>商品</span><select value={item.product_id} onChange={(event) => setItems((current) => current.map((row, rowIndex) => rowIndex === index ? { ...row, product_id: event.target.value } : row))}><option value="">選擇商品</option>{products.filter((product) => product.is_active).map((product) => <option key={product.id} value={product.id}>{product.name}</option>)}</select></label><label className="field"><span>數量</span><input min={1} type="number" value={item.quantity} onChange={(event) => setItems((current) => current.map((row, rowIndex) => rowIndex === index ? { ...row, quantity: Number(event.target.value) } : row))} /></label><button type="button" onClick={() => setItems((current) => current.filter((_, rowIndex) => rowIndex !== index))}>移除</button></div>)}</div><button className="button button-quiet" type="button" onClick={() => setItems((current) => [...current, { product_id: "", quantity: 1 }])}><Plus size={16} />加入商品</button>{memberPrice > nonmemberPrice && <p className="form-error">社員價不可高於一般價</p>}{create.isError && <p className="form-error">{create.error.message}</p>}<div className="form-actions"><button className="button button-quiet" type="button" onClick={() => setOpen(false)}>取消</button><button className="button button-primary" disabled={!name.trim() || validItems.length === 0 || memberPrice > nonmemberPrice || create.isPending}>儲存套組</button></div></form>}</section>;
}

function localDateTime(daysAhead: number) {
  return taipeiDateTimeInput(Date.now() + daysAhead * 86400000);
}


function campaignUnit(campaign: GroupCampaign, products: Product[]) {
  return campaign.target_type === "bundle" ? "組" : products.find((product) => product.id === campaign.target_id)?.unit || "份";
}
