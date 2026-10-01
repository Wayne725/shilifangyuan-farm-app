import { ArrowLeft, ArrowRight, Minus, Plus, UsersThree } from "@phosphor-icons/react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import { DataState, LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import { apiFetch, formatDateTime } from "../lib/api";
import type { GroupBundle, GroupVoteProposal, Product } from "../lib/types";

export function GroupVotesPage() {
  const { user, openLogin } = useAuth();
  const proposals = useQuery({ queryKey: ["group-vote-proposals"], queryFn: () => apiFetch<GroupVoteProposal[]>("/v1/vote-proposals") });
  return (
    <section className="social-page-shell group-votes-page">
      <header className="social-page-heading"><div><p className="eyebrow">GROUP VOTING / 需求投票</p><h1>先確認需求，再一起購買</h1></div>{user ? <Link className="button button-system" to="/group-votes/new">發起投票</Link> : <button className="button button-system" type="button" onClick={openLogin}>登入後發起</button>}</header>
      <div className="group-vote-note"><UsersThree size={23} weight="light" /><p>投票只代表需求估計，不會產生訂單或扣款。管理端審核與供應確認後才會建立正式團購。</p></div>
      {proposals.isPending && <LoadingLines count={4} />}
      {proposals.isError && <DataState kind="error" title="團購投票無法讀取" detail={proposals.error.message} />}
      {proposals.data?.length === 0 && <DataState title="目前沒有投票" detail="可以從合作社現有商品或套組發起需求投票。" />}
      <div className="group-vote-list">
        {proposals.data?.map((proposal) => {
          return <Link key={proposal.id} to="/group-votes/$proposalId" params={{ proposalId: proposal.id }}><span className="status-chip">{groupVoteStatus(proposal.status)}</span><div><h2>{proposal.target_name}</h2><p>{proposal.vote_count} 人支持 · 預估 {proposal.estimated_quantity} 組</p></div><progress className="group-vote-progress" max={proposal.threshold} value={proposal.vote_count} /><small>{proposal.deadline ? `${formatDateTime(proposal.deadline)} 截止` : "審核後設定截止時間"}</small><ArrowRight size={20} weight="light" /></Link>;
        })}
      </div>
    </section>
  );
}

export function GroupVoteDetailPage() {
  const { proposalId } = useParams({ from: "/group-votes/$proposalId" });
  const { user, openLogin } = useAuth();
  const queryClient = useQueryClient();
  const [quantity, setQuantity] = useState(1);
  const proposal = useQuery({ queryKey: ["group-vote-proposal", proposalId], queryFn: () => apiFetch<GroupVoteProposal>(`/v1/vote-proposals/${proposalId}`) });
  useEffect(() => {
    if (proposal.data?.my_vote_quantity) setQuantity(proposal.data.my_vote_quantity);
  }, [proposal.data?.my_vote_quantity]);
  const vote = useMutation({ mutationFn: () => apiFetch<GroupVoteProposal>(`/v1/vote-proposals/${proposalId}/vote`, { method: "PUT", body: JSON.stringify({ estimated_quantity: quantity }) }), onSuccess: (data) => { setQuantity(data.my_vote_quantity || 1); queryClient.invalidateQueries({ queryKey: ["group-vote-proposal", proposalId] }); queryClient.invalidateQueries({ queryKey: ["group-vote-proposals"] }); } });
  const withdraw = useMutation({ mutationFn: () => apiFetch<void>(`/v1/vote-proposals/${proposalId}/vote`, { method: "DELETE" }), onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["group-vote-proposal", proposalId] }); queryClient.invalidateQueries({ queryKey: ["group-vote-proposals"] }); } });
  if (proposal.isPending) return <section className="offer-page"><LoadingLines count={4} /></section>;
  if (proposal.isError || !proposal.data) return <section className="offer-page"><DataState kind="error" title="找不到團購投票" detail={proposal.error?.message || "這筆投票可能已結束"} /></section>;
  const data = proposal.data;
  return <section className="offer-page group-vote-detail"><Link className="text-link" to="/group-votes"><ArrowLeft size={17} />回到需求投票</Link><div className="group-vote-hero"><span className="status-chip">{groupVoteStatus(data.status)}</span><p className="eyebrow">DEMAND ESTIMATE</p><h1>{data.target_name}</h1><p>用預估數量表達需求；截止前可修改或撤回。</p><div className="group-vote-stats"><div><strong>{data.vote_count}</strong><span>支持人數</span></div><div><strong>{data.threshold}</strong><span>評估門檻</span></div><div><strong>{data.estimated_quantity}</strong><span>預估組數</span></div></div><progress className="group-vote-progress" max={data.threshold} value={data.vote_count} /></div><div className="group-vote-action-panel"><div><h2>我的預估數量</h2><p>{data.deadline ? `${formatDateTime(data.deadline)} 截止` : "等待管理端審核"}</p></div>{data.status === "voting" && <div className="quantity-control"><button type="button" aria-label="減少數量" onClick={() => setQuantity((value) => Math.max(1, value - 1))}><Minus size={15} /></button><span>{quantity}</span><button type="button" aria-label="增加數量" onClick={() => setQuantity((value) => Math.min(999, value + 1))}><Plus size={15} /></button></div>}{!user ? <button className="button button-primary" type="button" onClick={openLogin}>登入後投票</button> : data.status === "voting" ? <div className="group-vote-buttons"><button className="button button-primary" type="button" disabled={vote.isPending} onClick={() => vote.mutate()}>{data.my_vote_quantity ? "更新投票" : "投下支持票"}</button>{data.my_vote_quantity && <button className="button button-quiet" type="button" disabled={withdraw.isPending} onClick={() => withdraw.mutate()}>撤回投票</button>}</div> : <p className="form-success">本次投票目前不接受變更。</p>}{(vote.isError || withdraw.isError) && <p className="form-error">{vote.error?.message || withdraw.error?.message}</p>}</div></section>;
}

export function NewGroupVotePage() {
  const { user, openLogin } = useAuth();
  const navigate = useNavigate();
  const [targetType, setTargetType] = useState<"product" | "bundle">("product");
  const [targetId, setTargetId] = useState("");
  const [products, bundles] = useQueries({ queries: [
    { queryKey: ["products"], queryFn: () => apiFetch<Product[]>("/v1/products") },
    { queryKey: ["group-bundles"], queryFn: () => apiFetch<GroupBundle[]>("/v1/group-bundles") },
  ] });
  const targets = targetType === "product" ? products.data || [] : bundles.data || [];
  const create = useMutation({ mutationFn: () => apiFetch<GroupVoteProposal>("/v1/vote-proposals", { method: "POST", body: JSON.stringify({ target_type: targetType, target_id: targetId }) }), onSuccess: (proposal) => navigate({ to: "/group-votes/$proposalId", params: { proposalId: proposal.id } }) });
  if (!user) return <section className="admin-gate"><div className="admin-gate-mark"><UsersThree size={38} weight="light" /></div><p className="eyebrow">GROUP VOTING</p><h1>登入後才能發起需求投票。</h1><button className="button button-primary" type="button" onClick={openLogin}>登入帳號</button></section>;
  return <section className="social-page-shell new-group-vote"><Link className="text-link" to="/group-votes"><ArrowLeft size={17} />回到需求投票</Link><header className="social-page-heading"><div><p className="eyebrow">NEW GROUP VOTE</p><h1>發起團購投票</h1></div></header><form className="social-form" onSubmit={(event) => { event.preventDefault(); create.mutate(); }}><div className="form-heading"><div><p className="eyebrow">SELECT TARGET</p><h2>選擇合作社既有項目</h2></div><span>送出後由管理端確認供應條件。</span></div><div className="field-grid two-columns"><label className="field"><span>項目類型</span><select value={targetType} onChange={(event) => { setTargetType(event.target.value as "product" | "bundle"); setTargetId(""); }}><option value="product">單一商品</option><option value="bundle">固定套組</option></select></label><label className="field"><span>提案項目</span><select required value={targetId} onChange={(event) => setTargetId(event.target.value)}><option value="">選擇項目</option>{targets.filter((item) => item.is_active).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label></div>{create.isError && <p className="form-error">{create.error.message}</p>}<div className="form-actions"><button className="button button-primary" disabled={!targetId || create.isPending}>送出審核</button></div></form></section>;
}

function groupVoteStatus(status: string) { return { pending_review: "待審核", voting: "投票中", conversion_pending: "等待正式開團", converted: "已建立正式團購", rejected: "未通過", expired: "已結束" }[status] || status; }
