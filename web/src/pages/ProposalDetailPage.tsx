import { ArrowLeft, CheckCircle, ChatCircle, NotePencil, UsersThree, XCircle } from "@phosphor-icons/react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Link, useParams } from "@tanstack/react-router";

import { SocialNav } from "../components/SocialNav";
import { DataState, LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import { apiFetch, formatDateTime } from "../lib/api";
import type { MembershipSummary, Proposal, ProposalComment, ProposalVote } from "../lib/types";
import { proposalStatusLabel } from "./GovernancePage";

export function ProposalDetailPage() {
  const { proposalId } = useParams({ from: "/proposals/$proposalId" });
  const { user, openLogin } = useAuth();
  const queryClient = useQueryClient();
  const [comment, setComment] = useState("");
  const membership = useQuery({
    queryKey: ["membership-me", user?.id],
    queryFn: () => apiFetch<MembershipSummary>("/v1/members/me"),
    enabled: Boolean(user),
  });
  const canAccess = membership.data?.membership_type === "member";
  const [proposal, comments, namedVotes] = useQueries({ queries: [
    { queryKey: ["member-proposal", proposalId], queryFn: () => apiFetch<Proposal>(`/v1/member-proposals/${proposalId}`), enabled: canAccess },
    { queryKey: ["member-proposal-comments", proposalId], queryFn: () => apiFetch<ProposalComment[]>(`/v1/member-proposals/${proposalId}/comments`), enabled: canAccess },
    { queryKey: ["member-proposal-votes", proposalId], queryFn: () => apiFetch<ProposalVote[]>(`/v1/member-proposals/${proposalId}/votes`), enabled: canAccess },
  ] });
  const vote = useMutation({
    mutationFn: (body: { choice?: string; option_id?: string }) => apiFetch<Proposal>(`/v1/member-proposals/${proposalId}/vote`, { method: "PUT", body: JSON.stringify(body) }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["member-proposal", proposalId] });
      queryClient.invalidateQueries({ queryKey: ["member-proposal-votes", proposalId] });
      queryClient.invalidateQueries({ queryKey: ["member-proposals"] });
    },
  });
  const addComment = useMutation({
    mutationFn: () => apiFetch<ProposalComment>(`/v1/member-proposals/${proposalId}/comments`, { method: "POST", body: JSON.stringify({ body: comment }) }),
    onSuccess: () => {
      setComment("");
      queryClient.invalidateQueries({ queryKey: ["member-proposal-comments", proposalId] });
    },
  });
  const proposalAction = useMutation({
    mutationFn: (action: "submit" | "withdraw") => apiFetch<Proposal>(`/v1/member-proposals/${proposalId}/${action}`, { method: "POST" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["member-proposal", proposalId] });
      queryClient.invalidateQueries({ queryKey: ["member-proposals"] });
    },
  });

  function submitComment(event: FormEvent) {
    event.preventDefault();
    addComment.mutate();
  }

  if (!user) {
    return <section className="social-page-shell"><SocialNav /><div className="social-access-card"><NotePencil size={38} weight="light" /><h2>登入後查看社員提案</h2><button className="button button-primary" type="button" onClick={openLogin}>登入帳號</button></div></section>;
  }
  if (membership.isPending || proposal.isPending) return <section className="social-page-shell"><SocialNav /><LoadingLines count={5} /></section>;
  if (!canAccess) return <section className="social-page-shell"><SocialNav /><div className="social-access-card"><UsersThree size={38} weight="light" /><h2>此功能開放給正式社員</h2><Link className="button button-system" to="/membership">查看入社進度</Link></div></section>;
  if (proposal.isError || !proposal.data) return <section className="social-page-shell"><SocialNav /><DataState kind="error" title="找不到這筆提案" detail={proposal.error?.message || "提案不存在或你沒有查看權限。"} /></section>;

  const item = proposal.data;
  const isOwner = item.created_by_id === user.id;
  const canComment = ["discussion", "voting"].includes(item.status);

  return (
    <section className="social-page-shell proposal-detail-page">
      <SocialNav />
      <Link className="back-link" to="/governance"><ArrowLeft size={17} />返回提案議事</Link>
      <article className="proposal-detail">
        <header>
          <div><span className={`status-chip ${item.status}`}>{proposalStatusLabel(item.status)}</span><small>{item.proposal_type === "resolution" ? "決議表決" : "選項表決"}</small></div>
          <h1>{item.title}</h1>
          <p>提案人 {item.created_by_name} · {formatDateTime(item.created_at)}</p>
        </header>
        <div className="proposal-body">{item.body}</div>
        {item.review_reason && <div className="review-note"><strong>審核意見</strong><p>{item.review_reason}</p></div>}
        {item.result_summary && <div className="review-note result"><strong>結案說明</strong><p>{item.result_summary}</p></div>}
        <dl className="proposal-timeline">
          <div><dt>討論截止</dt><dd>{formatDateTime(item.discussion_ends_at)}</dd></div>
          <div><dt>投票截止</dt><dd>{formatDateTime(item.voting_ends_at)}</dd></div>
          <div><dt>最低投票數</dt><dd>{item.minimum_voters} 票</dd></div>
        </dl>
        {isOwner && ["draft", "pending_review", "discussion"].includes(item.status) && (
          <div className="proposal-owner-actions">
            {item.status === "draft" && <button className="button button-primary" type="button" disabled={proposalAction.isPending} onClick={() => proposalAction.mutate("submit")}>送出審核</button>}
            <button className="button button-quiet" type="button" disabled={proposalAction.isPending} onClick={() => proposalAction.mutate("withdraw")}>撤回提案</button>
          </div>
        )}
      </article>

      <section className="voting-panel">
        <div className="section-title-row"><div><p className="eyebrow">VOTING</p><h2>表決結果</h2></div><strong>{item.tally.total} 票</strong></div>
        {item.proposal_type === "resolution" ? (
          <div className="vote-grid resolution-votes">
            {(["yes", "no", "abstain"] as const).map((choice) => (
              <button key={choice} type="button" className={item.my_vote === choice ? "selected" : ""} disabled={item.status !== "voting" || vote.isPending} onClick={() => vote.mutate({ choice })}>
                {choice === "yes" ? <CheckCircle size={22} /> : choice === "no" ? <XCircle size={22} /> : <NotePencil size={22} />}
                <span>{voteChoiceLabel(choice)}</span><strong>{item.tally[choice]}</strong>
              </button>
            ))}
          </div>
        ) : (
          <div className="vote-grid option-votes">
            {item.options.map((option) => (
              <button key={option.id} type="button" className={item.my_option_id === option.id ? "selected" : ""} disabled={item.status !== "voting" || vote.isPending} onClick={() => vote.mutate({ option_id: option.id })}><span>{option.label}</span><strong>{option.vote_count}</strong></button>
            ))}
          </div>
        )}
        {item.status !== "voting" && <p className="quiet-note">目前不在投票期間。</p>}
        {vote.isError && <p className="form-error">{vote.error.message}</p>}
        <div className="named-votes">
          <h3>記名投票</h3>
          {namedVotes.data?.length === 0 && <p>目前尚無投票紀錄。</p>}
          {namedVotes.data?.map((entry) => <span key={entry.user_id}><strong>{entry.display_name}</strong>{entry.option_label || voteChoiceLabel(entry.choice || "abstain")}</span>)}
        </div>
      </section>

      <section className="discussion-panel">
        <div className="section-title-row"><div><p className="eyebrow">DISCUSSION</p><h2>社員討論</h2></div><ChatCircle size={27} weight="light" /></div>
        {comments.isPending && <LoadingLines count={3} />}
        <div className="comment-list">
          {comments.data?.map((entry) => <article key={entry.id}><div><strong>{entry.display_name}</strong><time>{formatDateTime(entry.created_at)}</time></div><p>{entry.body}</p></article>)}
          {comments.data?.length === 0 && <DataState title="尚未有社員留言" detail={canComment ? "成為第一位參與討論的社員。" : "此提案目前不開放留言。"} />}
        </div>
        {canComment && <form className="comment-form" onSubmit={submitComment}><label className="field"><span>留言內容</span><textarea required rows={4} value={comment} onChange={(event) => setComment(event.target.value)} /></label><button className="button button-system" disabled={addComment.isPending}>送出留言</button></form>}
        {(addComment.isError || proposalAction.isError) && <p className="form-error">{addComment.error?.message || proposalAction.error?.message}</p>}
      </section>
    </section>
  );
}

function voteChoiceLabel(choice: string) {
  return { yes: "贊成", no: "反對", abstain: "棄權" }[choice] || choice;
}
