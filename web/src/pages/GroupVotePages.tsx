import { ArrowLeft, ArrowRight, UsersThree } from "@phosphor-icons/react";
import { useMutation, useQueries, useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { useState } from "react";

import { DataState, LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import { apiFetch, formatDateTime } from "../lib/api";
import { groupProposalStatusLabel } from "../lib/group-campaigns";
import type { GroupBundle, GroupVoteProposal, Product } from "../lib/types";

export function GroupVotesPage() {
  const proposals = useQuery({ queryKey: ["group-vote-proposals"], queryFn: () => apiFetch<GroupVoteProposal[]>("/v1/vote-proposals") });
  return (
    <section className="social-page-shell group-votes-page">
      <header className="social-page-heading"><div><p className="eyebrow">GROUP VOTING / 共同購買投票紀錄</p><h1>共同購買投票紀錄</h1></div></header>
      {proposals.isPending && <LoadingLines count={4} />}
      {proposals.isError && <DataState kind="error" title="共同購買投票無法讀取" detail={proposals.error.message} />}
      {proposals.data?.length === 0 && <DataState title="目前沒有投票紀錄" detail="" />}
      <div className="group-vote-list">
        {proposals.data?.map((proposal) => {
          return <Link key={proposal.id} to="/group-votes/$proposalId" params={{ proposalId: proposal.id }}><span className="status-chip">{proposal.status === "ended_unmet" ? "已結束" : groupProposalStatusLabel(proposal.status)}</span><div><h2>{proposal.target_name}</h2><p>{proposal.vote_count} 人支持 · 預估 {proposal.estimated_quantity} 組</p></div><small>{proposal.deadline ? `${formatDateTime(proposal.deadline)} 截止` : "未設定截止時間"}</small><ArrowRight size={20} weight="light" /></Link>;
        })}
      </div>
    </section>
  );
}

export function GroupVoteDetailPage() {
  const { proposalId } = useParams({ from: "/group-votes/$proposalId" });
  const proposal = useQuery({ queryKey: ["group-vote-proposal", proposalId], queryFn: () => apiFetch<GroupVoteProposal>(`/v1/vote-proposals/${proposalId}`) });
  if (proposal.isPending) return <section className="offer-page"><LoadingLines count={4} /></section>;
  if (proposal.isError || !proposal.data) return <section className="offer-page"><DataState kind="error" title="找不到共同購買投票紀錄" detail={proposal.error?.message || "找不到這筆紀錄"} /></section>;
  const data = proposal.data;
  return <section className="offer-page group-vote-detail">
    <Link className="text-link" to="/group-votes"><ArrowLeft size={17} />回到投票紀錄</Link>
    <div className="group-vote-hero">
      <span className="status-chip">{data.status === "ended_unmet" ? "已結束" : groupProposalStatusLabel(data.status)}</span>
      <p className="eyebrow">DEMAND HISTORY / 歷史需求</p>
      <h1>{data.target_name}</h1>
      {data.request_details && <p className="group-request-details">{data.request_details}</p>}
      <div className="group-vote-stats"><div><strong>{data.vote_count}</strong><span>支持人數</span></div><div><strong>{data.estimated_quantity}</strong><span>預估組數</span></div></div>
    </div>
    {Boolean(data.created_at || data.deadline || data.my_vote_quantity) && <div className="group-vote-action-panel">
      <dl>
        {data.created_at && <div><dt>建立時間</dt><dd>{formatDateTime(data.created_at)}</dd></div>}
        {data.deadline && <div><dt>原投票截止</dt><dd>{formatDateTime(data.deadline)}</dd></div>}
        {Boolean(data.my_vote_quantity) && <div><dt>我的原預估數量</dt><dd>{data.my_vote_quantity} 組</dd></div>}
      </dl>
    </div>}
  </section>;
}

export function NewGroupVotePage() {
  const { user, openLogin } = useAuth();
  const navigate = useNavigate();
  const [targetType, setTargetType] = useState<"product" | "bundle" | "custom">("product");
  const [targetId, setTargetId] = useState("");
  const [requestName, setRequestName] = useState("");
  const [requestDetails, setRequestDetails] = useState("");
  const [products, bundles] = useQueries({ queries: [
    { queryKey: ["products"], queryFn: () => apiFetch<Product[]>("/v1/products") },
    { queryKey: ["group-bundles"], queryFn: () => apiFetch<GroupBundle[]>("/v1/group-bundles") },
  ] });
  const targets = targetType === "product" ? products.data || [] : bundles.data || [];
  const create = useMutation({ mutationFn: () => apiFetch<GroupVoteProposal>("/v1/vote-proposals", { method: "POST", body: JSON.stringify(targetType === "custom" ? { target_type: "custom", request_name: requestName, request_details: requestDetails } : { target_type: targetType, target_id: targetId }) }), onSuccess: (proposal) => navigate({ to: "/group-votes/$proposalId", params: { proposalId: proposal.id } }) });
  if (!user) return <section className="admin-gate"><div className="admin-gate-mark"><UsersThree size={38} weight="light" /></div><p className="eyebrow">GROUP VOTING / 共同購買需求投票</p><h1>登入後才能發起需求投票。</h1><button className="button button-primary" type="button" onClick={openLogin}>登入帳號</button></section>;
  return <section className="social-page-shell new-group-vote"><Link className="text-link" to="/group-votes"><ArrowLeft size={17} />回到需求投票</Link><header className="social-page-heading"><div><p className="eyebrow">NEW GROUP VOTE / 發起需求投票</p><h1>發起共同購買投票</h1></div></header><form className="social-form" onSubmit={(event) => { event.preventDefault(); create.mutate(); }}><div className="form-heading"><div><p className="eyebrow">SELECT TARGET / 選擇商品</p><h2>選擇項目或提出其他需求</h2></div></div><div className="field-grid two-columns"><label className="field"><span>項目類型</span><select value={targetType} onChange={(event) => { setTargetType(event.target.value as "product" | "bundle" | "custom"); setTargetId(""); }}><option value="product">單一商品</option><option value="bundle">固定套組</option><option value="custom">其他需求</option></select></label>{targetType === "custom" ? <><label className="field"><span>需求名稱</span><input required maxLength={120} value={requestName} onChange={(event) => setRequestName(event.target.value)} /></label><label className="field wide"><span>需求內容</span><textarea required maxLength={3000} rows={5} value={requestDetails} onChange={(event) => setRequestDetails(event.target.value)} /></label></> : <label className="field"><span>提案項目</span><select required value={targetId} onChange={(event) => setTargetId(event.target.value)}><option value="">選擇項目</option>{targets.filter((item) => item.is_active).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>}</div>{create.isError && <p className="form-error">{create.error.message}</p>}<div className="form-actions"><button className="button button-primary" disabled={(targetType === "custom" ? !requestName.trim() || !requestDetails.trim() : !targetId) || create.isPending}>送出審核</button></div></form></section>;
}
