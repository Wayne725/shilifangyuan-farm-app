import * as Tabs from "@radix-ui/react-tabs";
import { Buildings, CheckCircle, Package, Plus } from "@phosphor-icons/react";
import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";

import { AdminNav } from "../components/AdminNav";
import { AdminWishesPanel } from "../components/AdminWishesPanel";
import { CampaignReviewPanel } from "../components/CampaignReviewPanel";
import { CampaignQuantityEditor } from "../components/CampaignQuantityEditor";
import { DataState, LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import { ApiError, apiFetch, captureSessionIdentityGuard, formatDateTime, formatMoney } from "../lib/api";
import { campaignAccessDenied, campaignDateTime, campaignScheduleError, campaignStatusLabel, campaignTimestamp, defaultReviewDeadline, groupProposalStatusLabel, retryCampaignRead } from "../lib/group-campaigns";
import { taipeiDate, taipeiDateTimeInput, taipeiInputToIso } from "../lib/meal-time";
import { useDeadlineClock } from "../lib/use-deadline-clock";
import type { GroupBundle, GroupCampaign, GroupVoteProposal, Product } from "../lib/types";
import { OptionalMark } from "../components/OptionalMark";
import { CatalogVisibilityControl } from '../components/CatalogVisibilityControl';

export function AdminGroupsPage() {
  const { user, openLogin } = useAuth();
  const isAdmin = user?.user_role === "admin";
  if (!isAdmin) {
    return <section className="admin-gate"><div className="admin-gate-mark"><Buildings size={38} weight="light" /></div><p className="eyebrow">GROUP OPERATIONS / 共同購買管理</p><h1>共同購買管理只向管理者開放。</h1>{!user ? <button className="button button-primary" type="button" onClick={openLogin}>管理者登入</button> : <Link className="button button-quiet" to="/shop">返回生活消費</Link>}</section>;
  }
  return <AdminGroupsWorkspace key={user.id} userId={user.id} />;
}

function AdminGroupsWorkspace({ userId }: { userId: string }) {
  const { tab = "proposals", view } = useSearch({ from: "/admin/groups" });
  const navigate = useNavigate({ from: "/admin/groups" });
  const now = useDeadlineClock();
  const queryClient = useQueryClient();
  const [revoked, setRevoked] = useState(false);
  const [sameSession] = useState(captureSessionIdentityGuard);
  const read = <T,>(path: string) => {
    if (!sameSession()) throw new ApiError(401, "登入狀態已變更，請重新登入。");
    return apiFetch<T>(path);
  };
  const [products, bundles, proposals, campaigns] = useQueries({ queries: [
    { queryKey: ["admin-products", userId], queryFn: () => read<Product[]>("/v1/products"), enabled: !revoked && tab !== "wishes", retry: retryCampaignRead },
    { queryKey: ["group-bundles", userId], queryFn: () => read<GroupBundle[]>("/v1/group-bundles"), enabled: !revoked && tab !== "wishes", retry: retryCampaignRead },
    { queryKey: ["group-vote-proposals", userId], queryFn: () => read<GroupVoteProposal[]>("/v1/vote-proposals"), enabled: !revoked && tab === "proposals", retry: retryCampaignRead },
    { queryKey: ["group-campaigns", "admin", userId], queryFn: () => read<GroupCampaign[]>("/v1/group-campaigns?include_hidden=true"), enabled: !revoked && (tab === "campaigns" || tab === "proposals"), retry: retryCampaignRead, refetchInterval: revoked ? false : 30000 },
  ] });
  const denied = revoked || [products, bundles, proposals, campaigns].some(query => campaignAccessDenied(query.error));
  useEffect(() => { if (denied) setRevoked(true); }, [denied]);
  const onAccessDenied = () => setRevoked(true);
  if (denied) return <section className="admin-gate"><DataState kind="error" title="共同購買管理權限已失效" detail="請重新登入管理員帳號" /></section>;

  const refreshProposals = () => queryClient.invalidateQueries({ queryKey: ["group-vote-proposals"] });
  const refreshCampaigns = () => queryClient.invalidateQueries({ queryKey: ["group-campaigns"] });
  const refreshBundles = () => queryClient.invalidateQueries({ queryKey: ["group-bundles"] });
  const visibleCampaigns = (campaigns.data || []).filter(campaign => {
    if (view === "active") return campaign.is_public !== false && campaignTimestamp(campaign.deadline) > now
      && ["recruiting", "confirmed"].includes(campaign.decision_status);
    if (view === "decision") return campaign.decision_status === "pending_confirmation";
    return true;
  });

  return (
    <section className="admin-module-page">
      <header className="workspace-heading"><div><p className="eyebrow">GROUP OPERATIONS / 共同購買管理</p><h1>需求投票與正式共同購買</h1></div></header>
      <AdminNav />
      <Tabs.Root className="admin-social-tabs" value={tab} onValueChange={value => {
        const nextTab = value === "campaigns" || value === "bundles" || value === "wishes" ? value : "proposals";
        void navigate({ search: { tab: nextTab }, resetScroll: false });
      }}>
        <Tabs.List className="tab-list" aria-label="共同購買管理分類"><Tabs.Trigger value="proposals">提案審核</Tabs.Trigger><Tabs.Trigger value="campaigns">正式共同購買</Tabs.Trigger><Tabs.Trigger value="bundles">固定套組</Tabs.Trigger><Tabs.Trigger value="wishes">願望管理</Tabs.Trigger></Tabs.List>
        <Tabs.Content className="tab-content" value="proposals">
          <GroupProposalList
            proposals={proposals.data ?? []}
            pending={proposals.isPending}
            error={proposals.error?.message}
            votingOnly={view === "voting"}
            onScopeChange={(votingOnly) => void navigate({ search: { tab: "proposals", view: votingOnly ? "voting" : undefined }, resetScroll: false })}
            now={now}
            renderProposal={(proposal) => <GroupProposalReview
              key={proposal.id}
              proposal={proposal}
              campaign={campaigns.data?.find((campaign) => campaign.source_proposal_id === proposal.id)}
              products={products.data ?? []}
              bundles={bundles.data ?? []}
              onAccessDenied={onAccessDenied}
              onDone={() => { refreshProposals(); refreshCampaigns(); }}
            />}
          />
        </Tabs.Content>
        <Tabs.Content className="tab-content" value="campaigns">
          <CampaignCreateForm products={products.data || []} bundles={bundles.data || []} onAccessDenied={onAccessDenied} onDone={refreshCampaigns} />
          <label className="field social-review-scope"><span>共同購買範圍</span><select value={view === "active" || view === "decision" ? view : ""} onChange={event => {
            const value = event.target.value;
            void navigate({ search: { tab: "campaigns", view: value === "active" || value === "decision" ? value : undefined }, resetScroll: false });
          }}><option value="">全部</option><option value="active">進行中</option><option value="decision">待確認成團</option></select></label>
          <AdminGroupSection title="正式共同購買" count={visibleCampaigns.length} pending={campaigns.isPending} error={campaigns.error?.message}>
            <div className="admin-review-list">{visibleCampaigns.map((campaign) => <CampaignAdminCard key={campaign.id} campaign={campaign} products={products.data || []} onAccessDenied={onAccessDenied} onDone={refreshCampaigns} />)}</div>
          </AdminGroupSection>
        </Tabs.Content>
        <Tabs.Content className="tab-content" value="bundles">
          <BundleCreateForm products={products.data || []} onAccessDenied={onAccessDenied} onDone={refreshBundles} />
          <AdminGroupSection title="固定套組" count={bundles.data?.length || 0} pending={bundles.isPending} error={bundles.error?.message}>
            <div className="admin-record-grid">{bundles.data?.map((bundle) => <article className="admin-record-card" key={bundle.id}><Package size={22} weight="light" /><span className="status-chip passed">啟用</span><h3>{bundle.name}</h3><p>{bundle.items.map((item) => `${item.product_name} × ${item.quantity}`).join("、")}</p><dl><div><dt>社員價</dt><dd>{formatMoney(bundle.member_price)}</dd></div><div><dt>一般價</dt><dd>{formatMoney(bundle.nonmember_price)}</dd></div></dl></article>)}</div>
          </AdminGroupSection>
        </Tabs.Content>
        <Tabs.Content className="tab-content" value="wishes"><AdminWishesPanel /></Tabs.Content>
      </Tabs.Root>
    </section>
  );
}

const emptyProposalSearch = { query: "", from: "", through: "" };
const proposalPageSize = 10;

function GroupProposalList({ proposals, pending, error, votingOnly, onScopeChange, now, renderProposal }: {
  proposals: GroupVoteProposal[];
  pending: boolean;
  error?: string;
  votingOnly: boolean;
  onScopeChange: (votingOnly: boolean) => void;
  now: number;
  renderProposal: (proposal: GroupVoteProposal) => React.ReactNode;
}) {
  const [draft, setDraft] = useState(emptyProposalSearch);
  const [search, setSearch] = useState(emptyProposalSearch);
  const [page, setPage] = useState(1);
  const invalidRange = Boolean(draft.from && draft.through && draft.from > draft.through);
  const keyword = search.query.trim().toLowerCase();
  const hasDateFilter = Boolean(search.from || search.through);
  const matches = proposals.filter((proposal) => {
    if (votingOnly && !(proposal.status === "voting" && campaignTimestamp(proposal.deadline) > now)) return false;
    if (keyword && !proposal.target_name.toLowerCase().includes(keyword)) return false;
    if (!hasDateFilter) return true;
    const created = taipeiDate(campaignTimestamp(proposal.created_at));
    return created && (!search.from || created >= search.from) && (!search.through || created <= search.through);
  });
  const pageCount = Math.max(1, Math.ceil(matches.length / proposalPageSize));
  const currentPage = Math.min(page, pageCount);
  const start = (currentPage - 1) * proposalPageSize;

  return <>
    <form className="admin-order-search" aria-label="查詢共同購買提案" onSubmit={(event) => {
      event.preventDefault();
      if (invalidRange) return;
      setSearch(draft);
      setPage(1);
    }}>
      <label className="field"><span>提案標題</span><input type="search" value={draft.query} maxLength={120} onChange={(event) => setDraft({ ...draft, query: event.target.value })} /></label>
      <label className="field"><span>提案日期起</span><input type="date" value={draft.from} onChange={(event) => setDraft({ ...draft, from: event.target.value })} /></label>
      <label className="field"><span>提案日期迄</span><input type="date" value={draft.through} onChange={(event) => setDraft({ ...draft, through: event.target.value })} /></label>
      <label className="field"><span>提案範圍</span><select value={votingOnly ? "voting" : ""} onChange={(event) => {
        onScopeChange(event.target.value === "voting");
        setPage(1);
      }}><option value="">全部</option><option value="voting">投票中</option></select></label>
      <button className="button button-primary" type="submit" disabled={invalidRange}>查詢提案</button>
      <button className="button button-quiet" type="button" onClick={() => {
        setDraft(emptyProposalSearch);
        setSearch(emptyProposalSearch);
        setPage(1);
        if (votingOnly) onScopeChange(false);
      }}>清除查詢</button>
    </form>
    {invalidRange && <p className="form-error" role="alert">結束日期不可早於開始日期</p>}
    <AdminGroupSection
      title="共同購買提案"
      count={matches.length}
      pending={pending}
      error={error}
      empty={keyword || hasDateFilter || votingOnly ? <DataState title="沒有符合條件的提案" detail="請調整查詢條件。" /> : undefined}
    >
      {pageCount > 1 && <nav className="admin-pagination" aria-label="團購提案分頁">
        <button className="button button-quiet" type="button" disabled={currentPage === 1} onClick={() => setPage(currentPage - 1)}>上一頁</button>
        <span role="status">第 {currentPage}／{pageCount} 頁</span>
        <button className="button button-quiet" type="button" disabled={currentPage === pageCount} onClick={() => setPage(currentPage + 1)}>下一頁</button>
      </nav>}
      <div className="admin-review-list">{matches.slice(start, start + proposalPageSize).map(renderProposal)}</div>
    </AdminGroupSection>
  </>;
}

function AdminGroupSection({ title, count, pending, error, empty, children }: { title: string; count: number; pending: boolean; error?: string; empty?: React.ReactNode; children: React.ReactNode }) {
  return <section className="admin-review-section"><div className="section-title-row"><div><p className="eyebrow">GROUP WORKFLOW / 共同購買流程</p><h2>{title}</h2></div><span>{count}</span></div>{pending && <LoadingLines count={3} />}{error && <DataState kind="error" title={`${title}無法讀取`} detail={error} />}{!pending && !error && count === 0 && (empty || <DataState title={`目前沒有${title}`} detail="新的項目會出現在這裡。" />)}{children}</section>;
}

function GroupProposalReview({ proposal, campaign, products, bundles, onDone, onAccessDenied }: { proposal: GroupVoteProposal; campaign?: GroupCampaign; products: Product[]; bundles: GroupBundle[]; onDone: () => void; onAccessDenied: () => void }) {
  const [threshold, setThreshold] = useState(proposal.threshold || 10);
  const [deadline, setDeadline] = useState(localDateTime(7));
  const [reason, setReason] = useState("");
  const created = taipeiDate(campaignTimestamp(proposal.created_at));
  const deadlineLabel = campaign ? `${campaignDateTime(campaign.deadline)} 付款截止` : proposal.status === "converted" ? "" : proposal.deadline ? `${formatDateTime(proposal.deadline)} 投票截止` : "待審核設定期限";
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
      <div className="review-card-copy"><span className="status-chip">{groupProposalStatusLabel(proposal.status)}</span><h3>{proposal.target_name}</h3>{campaign && <p>供應商品：{campaign.target_type === "bundle" ? bundles.find(item => item.id === campaign.target_id)?.name || campaign.title : products.find(item => item.id === campaign.target_id)?.name || campaign.title}</p>}{proposal.request_details && <p className="group-request-details">{proposal.request_details}</p>}<p>{campaign ? `已付款 ${campaign.paid_quantity}/${campaign.min_paid_quantity} ${campaignUnit(campaign, products)}` : proposal.status === "converted" ? "已開團" : `投票 ${proposal.vote_count}/${proposal.threshold} 人 · 預估 ${proposal.estimated_quantity} 組`}</p><small>{[created && `${created.replaceAll("-", "/")} 提案`, deadlineLabel].filter(Boolean).join(" · ")}</small></div>
      <div className="review-card-actions schedule-fields">
        {proposal.status === "pending_review" && <><label className="field"><span>投票門檻（人）</span><input min={1} type="number" value={threshold} onChange={(event) => setThreshold(Number(event.target.value))} /></label><label className="field"><span>投票截止（台灣）</span><input type="datetime-local" value={deadline} onChange={(event) => setDeadline(event.target.value)} /></label></>}

        {proposal.status === "pending_review" && <label className="field"><span>處理原因<OptionalMark /></span><input value={reason} onChange={(event) => setReason(event.target.value)} /></label>}
        <div>{proposal.status === "pending_review" && <button type="button" disabled={!deadline || action.isPending} onClick={() => action.mutate("approve")}><CheckCircle size={16} />通過並開放投票</button>}{proposal.status === "pending_review" && <button type="button" disabled={action.isPending} onClick={() => action.mutate("reject")}>不通過</button>}</div>
        {action.isError && <p className="form-error">{action.error.message}</p>}
      </div>
        {["pending_review", "voting", "conversion_pending"].includes(proposal.status) && <CampaignCreateForm proposal={proposal} products={products} bundles={bundles} onAccessDenied={onAccessDenied} onDone={onDone} />}
        {campaign && <CampaignReviewPanel campaign={campaign} unit={campaignUnit(campaign, products)} onAccessDenied={onAccessDenied} onDone={onDone} />}
        {campaign && <CampaignQuantityEditor campaign={campaign} unit={campaignUnit(campaign, products)} onAccessDenied={onAccessDenied} onDone={onDone} />}
    </article>
  );
}

const newProductTarget = "__new_product__";
const newProductCategories = ["當季蔬果", "米・雜糧", "蛋品", "加工品", "飲品", "生活用品"];

function CampaignCreateForm({ products, bundles, proposal, onDone, onAccessDenied }: { products: Product[]; bundles: GroupBundle[]; proposal?: GroupVoteProposal; onDone: () => void; onAccessDenied: () => void }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const targetLocked = Boolean(proposal && proposal.target_type !== "custom");
  const initialTargetId = proposal?.target_type === "custom" ? newProductTarget : proposal?.target_id || "";
  const [targetType, setTargetType] = useState<"product" | "bundle">(proposal?.target_type === "bundle" ? "bundle" : "product");
  const [targetId, setTargetId] = useState(initialTargetId);
  const [newName, setNewName] = useState(proposal?.target_type === "custom" ? proposal.target_name : "");
  const [newDescription, setNewDescription] = useState(proposal?.request_details || "");
  const [newUnit, setNewUnit] = useState("");
  const [newCategory, setNewCategory] = useState("");
  const [newTaxType, setNewTaxType] = useState<"" | Product["tax_type"]>("");
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
  const addingProduct = targetType === "product" && targetId === newProductTarget && !targetLocked;
  const unit = addingProduct ? newUnit.trim() : targetType === "bundle" ? "組" : (target as Product | undefined)?.unit;
  const targetReady = addingProduct ? Boolean(newName.trim() && newUnit.trim() && newCategory && newTaxType) : Boolean(target);
  const quantityLabel = (label: string) => unit ? `${label}（${unit}）` : label;
  useEffect(() => {
    if (target) { setMemberPrice(target.member_price); setNonmemberPrice(target.nonmember_price); }
  }, [target?.id, target?.member_price, target?.nonmember_price]);
  const create = useMutation({
    mutationFn: () => {
      if (!targetReady) throw new Error(addingProduct ? "請填寫商品名稱、單位、分類與稅別" : "請選擇共同購買項目");
      if (scheduleError) throw new Error(scheduleError);
      const product = targetType === "product" && !addingProduct ? target as Product : null;
      const newProduct = addingProduct ? {
        name: newName.trim(), description: newDescription.trim(), unit: newUnit.trim(), category: newCategory,
        tax_type: newTaxType, member_price: memberPrice, nonmember_price: nonmemberPrice,
        stock_quantity: 0, can_ship: false, is_active: true, daily_sales_enabled: false,
      } : undefined;
      const campaignName = addingProduct ? newName.trim() : target!.name;
      const path = proposal ? `/v1/vote-proposals/${proposal.id}/admin/open-direct` : "/v1/group-campaigns";
      return apiFetch<GroupCampaign>(path, { method: "POST", body: JSON.stringify({
        target_type: targetType, target_id: addingProduct ? undefined : target!.id, new_product: newProduct,
        title: campaignName.length <= 116 ? `${campaignName}共同購買` : campaignName,
        description: addingProduct ? newDescription.trim() : target!.description,
        image_url: addingProduct ? null : target!.image_url || null,
        member_price: memberPrice, nonmember_price: nonmemberPrice,
        min_paid_quantity: minQuantity, supply_cap: supplyCap, per_user_cap: perUserCap,
        deadline: taipeiInputToIso(deadline), confirmation_deadline: taipeiInputToIso(reviewDeadline),
        estimated_pickup_start: taipeiInputToIso(pickupStart), estimated_pickup_end: taipeiInputToIso(pickupEnd),
        can_ship: product?.can_ship || false, shipping_temperature: product?.can_ship ? product.shipping_temperature : null,
        allowed_shipping_channels: product?.can_ship ? product.allowed_shipping_channels : [],
      }) });
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["admin-products"] }),
        queryClient.invalidateQueries({ queryKey: ["products"] }),
      ]);
      setOpen(false);
      setTargetId(initialTargetId);
      if (!proposal) { setNewName(""); setNewDescription(""); setNewUnit(""); setNewCategory(""); setNewTaxType(""); setMemberPrice(0); setNonmemberPrice(0); }
      onDone();
    },
    onError: error => { if (campaignAccessDenied(error)) onAccessDenied(); },
  });
  return <section className="admin-create-block">
    <button className="button button-system" type="button" disabled={create.isPending} onClick={() => setOpen(value => !value)}><Plus size={17} />{proposal ? "直接確認並開團" : "直接建立正式共同購買"}</button>
    {open && <form className="social-form admin-master-form" aria-label={proposal ? `${proposal.target_name}直接開團` : "直接建立共同購買"} onSubmit={event => { event.preventDefault(); if (!create.isPending) create.mutate(); }}>
      <div className="form-heading"><div><p className="eyebrow">DIRECT CAMPAIGN / 直接開團</p><h2>直接開團</h2></div></div>
      <div className="field-grid two-columns">
        <label className="field"><span>項目類型</span><select disabled={targetLocked || create.isPending} value={targetType} onChange={event => { setTargetType(event.target.value as "product" | "bundle"); setTargetId(""); }}><option value="product">單一商品</option><option value="bundle">固定套組</option></select></label>
        <label className="field"><span>共同購買項目</span><select required disabled={targetLocked || create.isPending} value={targetId} onChange={event => { setTargetId(event.target.value); create.reset(); }}><option value="">選擇項目</option>{targetType === "product" && !targetLocked && <option value={newProductTarget}>其他／新增商品</option>}{targets.map(item => <option key={item.id} value={item.id}>{item.name}{targetType === "product" ? `（${(item as Product).unit}）` : "（組）"}</option>)}</select></label>
      </div>
      {addingProduct && <fieldset className="campaign-new-product" disabled={create.isPending}>
        <legend>新增共同購買商品</legend>
        <div className="field-grid two-columns">
          <label className="field"><span>商品名稱</span><input required maxLength={120} value={newName} onChange={event => setNewName(event.target.value)} /></label>
          <label className="field"><span>計量單位</span><input required maxLength={40} placeholder="例如：顆、包、箱" value={newUnit} onChange={event => setNewUnit(event.target.value)} /></label>
          <label className="field"><span>商品分類</span><select required value={newCategory} onChange={event => setNewCategory(event.target.value)}><option value="">選擇分類</option>{newProductCategories.map(category => <option key={category}>{category}</option>)}</select></label>
          <label className="field"><span>商品稅別</span><select required value={newTaxType} onChange={event => setNewTaxType(event.target.value as Product["tax_type"])}><option value="">選擇稅別</option><option value="taxable">應稅</option><option value="tax_exempt">免稅</option></select></label>
          <label className="field wide"><span>商品說明</span><textarea rows={3} value={newDescription} onChange={event => setNewDescription(event.target.value)} /></label>
        </div>
      </fieldset>}
      <div className="field-grid three-columns">
        <label className="field"><span>{quantityLabel("社員單價")}</span><input type="number" min={0} required value={memberPrice} onChange={event => setMemberPrice(Number(event.target.value))} /></label>
        <label className="field"><span>{quantityLabel("一般單價")}</span><input type="number" min={0} required value={nonmemberPrice} onChange={event => setNonmemberPrice(Number(event.target.value))} /></label>
        <label className="field"><span>{quantityLabel("成團所需總數量")}</span><input required min={1} type="number" value={minQuantity} onChange={event => setMinQuantity(Number(event.target.value))} /></label>
        <label className="field"><span>{quantityLabel("供應上限")}</span><input required min={1} type="number" value={supplyCap} onChange={event => setSupplyCap(Number(event.target.value))} /></label>
        <label className="field"><span>{quantityLabel("每人限購數量")}</span><input required min={1} type="number" value={perUserCap} onChange={event => setPerUserCap(Number(event.target.value))} /></label>
        <label className="field"><span>付款截止（台灣）</span><input type="datetime-local" required value={deadline} onChange={event => { setDeadline(event.target.value); if (!reviewEdited) setReviewDeadline(defaultReviewDeadline(event.target.value)); }} /></label>
        <label className="field"><span>預計取貨開始（台灣）</span><input type="datetime-local" required value={pickupStart} onChange={event => setPickupStart(event.target.value)} /></label>
        <label className="field"><span>預計取貨結束（台灣）</span><input type="datetime-local" required value={pickupEnd} onChange={event => setPickupEnd(event.target.value)} /></label>
      </div>
      <details className="campaign-reminder-settings"><summary>管理員處理提醒設定</summary><label className="field"><span>管理員處理提醒日（台灣）</span><input type="datetime-local" value={reviewDeadline} onChange={event => { setReviewEdited(true); setReviewDeadline(event.target.value); }} /></label></details>
      {memberPrice > nonmemberPrice && <p className="form-error">社員價不可高於一般價</p>}
      {supplyCap < minQuantity && <p className="form-error">供應上限不可低於成團所需總數量</p>}
      {scheduleError && <p className="form-error" role="alert">{scheduleError}</p>}
      {create.isError && <p className="form-error" role="alert">{create.error.message}</p>}
      <div className="form-actions"><button className="button button-quiet" type="button" disabled={create.isPending} onClick={() => setOpen(false)}>取消</button><button className="button button-primary" disabled={Boolean(scheduleError) || !targetReady || memberPrice > nonmemberPrice || supplyCap < minQuantity || create.isPending}>{create.isPending ? "正在建立…" : addingProduct ? "新增商品並開放共同購買" : "確認開放共同購買"}</button></div>
    </form>}
  </section>;
}

function CampaignAdminCard({ campaign, products, onDone, onAccessDenied }: { campaign: GroupCampaign; products: Product[]; onDone: () => void; onAccessDenied: () => void }) {
  const now = useDeadlineClock(campaign.deadline);
  return <article className="admin-review-card group-proposal-card" aria-label={`共同購買 ${campaign.title}`}>
    <div className="review-card-copy"><span className="status-chip">{campaignStatusLabel(campaign, now)}</span><h3>{campaign.title}</h3><p>已付款 {campaign.paid_quantity}/{campaign.min_paid_quantity} · 保留 {campaign.reserved_quantity} · 剩餘 {campaign.available_quantity}</p><progress className="admin-progress" max={campaign.min_paid_quantity} value={campaign.paid_quantity} /><small>{campaignDateTime(campaign.deadline)} 付款截止 · {campaign.can_ship ? "可配送" : "合作社取貨"}</small></div>
    <Link to="/groups/$campaignId" params={{ campaignId: campaign.id }}>查看前台</Link>
    <CampaignTextEditor campaign={campaign} onDone={onDone} onAccessDenied={onAccessDenied} />
    <CatalogVisibilityControl kind="group" record={campaign} onDone={onDone} onDenied={onAccessDenied}/>
    <CampaignReviewPanel campaign={campaign} unit={campaignUnit(campaign, products)} onAccessDenied={onAccessDenied} onDone={onDone} />
    <CampaignQuantityEditor campaign={campaign} unit={campaignUnit(campaign, products)} onAccessDenied={onAccessDenied} onDone={onDone} />
  </article>;
}

function CampaignTextEditor({ campaign, onDone, onAccessDenied }: { campaign: GroupCampaign; onDone: () => void; onAccessDenied: () => void }) {
  const client = useQueryClient();
  const [sameSession] = useState(captureSessionIdentityGuard);
  const [editor, setEditor] = useState<{ title: string; description: string; originalTitle: string; originalDescription: string } | null>(null);
  const update = useMutation({
    mutationFn: (changes: { title?: string; description?: string }) => {
      if (!sameSession()) throw new ApiError(401, "登入狀態已變更，請重新登入。");
      return apiFetch<GroupCampaign>(`/v1/group-campaigns/${campaign.id}`, { method: "PATCH", body: JSON.stringify(changes) });
    },
    onSuccess: () => {
      if (!sameSession()) return;
      setEditor(null);
      void client.invalidateQueries({ queryKey: ["group-campaign", campaign.id] });
      onDone();
    },
    onError: error => { if (campaignAccessDenied(error)) onAccessDenied(); },
  });

  if (!editor) return <div className="admin-create-block">
    <button className="button button-quiet" type="button" onClick={() => {
      update.reset();
      setEditor({ title: campaign.title, description: campaign.description, originalTitle: campaign.title, originalDescription: campaign.description });
    }}>編輯名稱與說明</button>
    {update.isSuccess && <p className="form-success" role="status">名稱與說明已儲存</p>}
  </div>;

  const title = editor.title.trim();
  const description = editor.description.trim();
  const changes = {
    ...(title !== editor.originalTitle.trim() ? { title } : {}),
    ...(description !== editor.originalDescription.trim() ? { description } : {}),
  };
  const canSave = Boolean(title) && Object.keys(changes).length > 0 && !update.isPending;
  return <form className="social-form admin-master-form admin-create-block" aria-label="編輯共同購買名稱與說明" onSubmit={event => {
    event.preventDefault();
    if (canSave) update.mutate(changes);
  }}>
    <label className="field"><span>名稱</span><input required maxLength={120} disabled={update.isPending} value={editor.title} onChange={event => setEditor({ ...editor, title: event.target.value })} /></label>
    <label className="field"><span id={`campaign-description-${campaign.id}`}>說明</span><textarea aria-labelledby={`campaign-description-${campaign.id}`} rows={3} disabled={update.isPending} value={editor.description} onChange={event => setEditor({ ...editor, description: event.target.value })} /></label>
    {update.isError && <p className="form-error" role="alert">{update.error.message}</p>}
    <div className="form-actions">
      <button className="button button-quiet" type="button" disabled={update.isPending} onClick={() => setEditor(null)}>取消</button>
      <button className="button button-primary" disabled={!canSave}>{update.isPending ? "儲存中…" : "儲存"}</button>
    </div>
  </form>;
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
