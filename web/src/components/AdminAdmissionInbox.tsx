import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useAuth } from "../context/AuthContext";
import { ApiError, apiFetch } from "../lib/api";
import { admissionApi, admissionDateTime, admissionStatusLabel, type AdmissionRound } from "../lib/membership-admission";
import { DataState, LoadingLines } from "./Shared";
import "./AdminAdmissionInbox.css";
import { OptionalMark } from "./OptionalMark";

interface AdmissionList {
  items: AdmissionRound[];
  total: number;
  limit: number;
  offset: number;
}

type ReviewDecision = "approve" | "request_supplement" | "reject";
const decisions: { value: ReviewDecision; label: string }[] = [
  { value: "approve", label: "同意入社" },
  { value: "request_supplement", label: "要求補件" },
  { value: "reject", label: "不通過" },
];
const accessDenied = (error: unknown) => error instanceof ApiError && [401, 403].includes(error.status);
const reviewLabel = (value: string) => decisions.find(item => item.value === value)?.label || value;

export function AdminAdmissionInbox() {
  const { user } = useAuth();
  const client = useQueryClient();
  const [draft, setDraft] = useState({ q: "", status: "pending" });
  const [filters, setFilters] = useState({ q: "", status: "pending", offset: 0 });
  const [revoked, setRevoked] = useState(false);
  const [notice, setNotice] = useState("");
  const query = useQuery({
    queryKey: ["admin-membership-admissions", user?.id, filters],
    queryFn: () => apiFetch<AdmissionList>(`/v1/admin/membership/admissions?${new URLSearchParams({ q: filters.q, status: filters.status, limit: "25", offset: String(filters.offset) })}`),
    enabled: user?.user_role === "admin" && !revoked,
    retry: (count, error) => !accessDenied(error) && count < 1,
  });
  if (revoked || accessDenied(query.error)) return <section className="admin-review-section"><h2>新版入社審核</h2><p className="form-error" role="alert">入社審核權限已失效，請重新登入管理員帳號。</p></section>;

  const refresh = (round?: AdmissionRound) => {
    if (round) setNotice(`${round.profile?.legal_name || "申請人"}第 ${round.round_number} 輪：${admissionStatusLabel(round.status)}已記錄。`);
    void client.invalidateQueries({ queryKey: ["admin-membership-admissions", user?.id] });
  };
  function search(event: FormEvent) {
    event.preventDefault();
    setNotice("");
    setFilters({ q: draft.q.trim(), status: draft.status, offset: 0 });
  }
  return <section className="admin-review-section admission-inbox" aria-label="新版入社審核">
    <div className="section-title-row"><h2>新版入社審核</h2>{query.data && !query.isError && <span>共 {query.data.total} 輪</span>}</div>
    <form className="admission-inbox-filters" onSubmit={search} aria-label="查詢新版入社申請">
      <label className="field"><span>查詢申請</span><input type="search" value={draft.q} maxLength={100} placeholder="姓名、Email 或帳號名稱" onChange={event => setDraft({ ...draft, q: event.target.value })} /></label>
      <label className="field"><span>申請狀態</span><select value={draft.status} onChange={event => setDraft({ ...draft, status: event.target.value })}>
        <option value="pending">待審核及待補件</option><option value="all">全部狀態</option>
        {["draft", "submitted", "needs_supplement", "approved", "rejected", "withdrawn"].map(status => <option key={status} value={status}>{admissionStatusLabel(status)}</option>)}
      </select></label>
      <div className="form-actions"><button className="button button-primary">查詢</button><button type="button" className="button button-quiet" onClick={() => { setDraft({ q: "", status: "pending" }); setFilters({ q: "", status: "pending", offset: 0 }); }}>清除查詢</button></div>
    </form>
    {notice && <p role="status">{notice}</p>}
    {query.isPending ? <LoadingLines count={2} /> : query.isError ? <><DataState kind="error" title="入社申請無法讀取" detail={query.error.message} /><button type="button" className="button button-quiet" onClick={() => { void query.refetch(); }}>重新讀取</button></> : <>
      {query.data.items.length === 0 ? <DataState title="沒有符合條件的入社申請" detail="" /> : <div className="admin-review-list">{query.data.items.map(round => <AdmissionReviewCard key={`${user?.id}:${round.id}`} round={round} onDone={refresh} onDenied={() => setRevoked(true)} />)}</div>}
      {query.data.total > 25 && <nav className="admission-inbox-pagination" aria-label="入社申請分頁"><button type="button" className="button button-quiet" disabled={filters.offset === 0 || query.isFetching} onClick={() => setFilters({ ...filters, offset: Math.max(0, filters.offset - 25) })}>上一頁</button><span>{filters.offset + 1}–{Math.min(filters.offset + 25, query.data.total)}／{query.data.total}</span><button type="button" className="button button-quiet" disabled={filters.offset + 25 >= query.data.total || query.isFetching} onClick={() => setFilters({ ...filters, offset: filters.offset + 25 })}>下一頁</button></nav>}
    </>}
  </section>;
}

function AdmissionReviewCard({ round, onDone, onDenied }: { round: AdmissionRound; onDone: (round?: AdmissionRound) => void; onDenied: () => void }) {
  const [latest, setLatest] = useState(round);
  const current = latest.version >= round.version ? latest : round;
  const [expectedVersion, setExpectedVersion] = useState(round.version);
  const [decision, setDecision] = useState<ReviewDecision>("approve");
  const [reason, setReason] = useState("");
  const [reference, setReference] = useState("");
  const [studentVerified, setStudentVerified] = useState(false);
  const [conflict, setConflict] = useState(false);
  const changed = conflict || expectedVersion !== current.version;
  const reload = useMutation({
    mutationFn: () => admissionApi.round(round.id),
    onSuccess: value => { setLatest(value); setExpectedVersion(value.version); setStudentVerified(false); setConflict(false); review.reset(); onDone(); },
    onError: error => { if (accessDenied(error)) onDenied(); },
  });
  const review = useMutation({
    mutationFn: () => apiFetch<AdmissionRound>(`/v1/admin/membership/admissions/${encodeURIComponent(round.id)}/review`, {
      method: "POST", body: JSON.stringify({ expected_version: expectedVersion, decision, reason: reason.trim(), ...(decision === "approve" ? { decision_reference: reference.trim(), student_verified: current.student_claimed ? studentVerified : false } : {}) }),
    }),
    onSuccess: value => { setLatest(value); setExpectedVersion(value.version); onDone(value); },
    onError: error => { if (accessDenied(error)) onDenied(); else if (error instanceof ApiError && error.status === 409) setConflict(true); },
  });
  const currentAttempts = current.attempts.filter(attempt => attempt.quiz_version === current.quiz_version);
  const bestScore = currentAttempts.length ? Math.max(...currentAttempts.map(attempt => attempt.score)) : null;
  const qualified = currentAttempts.some(attempt => attempt.passed);
  const disabled = review.isPending || reload.isPending || changed || (decision === "approve" && (!reference.trim() || !qualified || (current.student_claimed && !studentVerified)));
  return <article className="admission-review-card">
    <div className="admission-review-copy">
      <span className="status-chip">{admissionStatusLabel(current.status)}</span><h3>{current.profile?.legal_name || "尚未填寫姓名"}</h3>
      <p>第 {current.round_number} 輪 · 送審 {admissionDateTime(current.submitted_at)}</p>
      {current.profile && <p>{current.profile.contact_email} · {current.profile.phone}</p>}
      <dl className="admission-review-facts"><div><dt>教材閱讀</dt><dd>{admissionDateTime(current.material_completed_at)}</dd></div><div><dt>本輪測驗</dt><dd>{bestScore === null ? "尚未作答" : `${bestScore} 分 · ${qualified ? "合格" : "未合格"}`}</dd></div><div><dt>認股意向</dt><dd>{current.requested_share_count} 股</dd></div><div><dt>學生資格聲明</dt><dd>{current.student_claimed ? "申請學生資格" : "一般身分"}</dd></div></dl>
      <details><summary>申請資料與志願書紀錄</summary>{current.profile ? <dl className="admission-review-profile">{[["出生日期", current.profile.birth_date], ["現居地址", current.profile.address], ["戶籍地址", current.profile.registered_address], ["通訊地址", current.profile.correspondence_address], ["性別", current.profile.gender], ["籍貫", current.profile.place_of_origin], ["職業", current.profile.occupation], ["市話", current.profile.landline_phone], ["LINE ID", current.profile.line_id], ["緊急聯絡人與電話", current.profile.emergency_contact], ["得知管道", current.discovery_channel === "其他" ? current.discovery_other : current.discovery_channel], ["志願書版本", current.declaration_version], ["個資告知版本", current.privacy_version], ["同意時間", admissionDateTime(current.consented_at)]].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value || "未填寫"}</dd></div>)}</dl> : <p>尚未儲存申請資料</p>}{current.submissions.map(submission => <p key={submission.id}>第 {submission.revision_number} 次送審：{admissionDateTime(submission.submitted_at)}</p>)}</details>
      {current.attempts.length > 0 && <details><summary>測驗紀錄（{current.attempts.length} 次）</summary>{current.attempts.map(attempt => <p key={attempt.id}>第 {attempt.attempt_number} 次：{attempt.score} 分 · {attempt.passed ? "合格" : "未合格"} · {admissionDateTime(attempt.submitted_at)}</p>)}</details>}
      {current.reviews.length > 0 && <details><summary>審核紀錄</summary>{current.reviews.map(item => <div key={item.id}><p>{reviewLabel(item.decision)} · {admissionDateTime(item.reviewed_at)}</p>{item.reason && <p>{item.reason}</p>}{item.decision_reference && <p>決議紀錄：{item.decision_reference}</p>}{item.student_verified === true && <p>學生資格已核驗</p>}</div>)}</details>}
    </div>
    <div className="admission-review-actions">
      {current.status === "submitted" && <form aria-label={`${current.profile?.legal_name || "申請人"}第${current.round_number}輪入社審核`} onSubmit={event => { event.preventDefault(); if (!disabled) review.mutate(); }}>
        <fieldset className="admission-review-decisions"><legend>審核結果</legend>{decisions.map(item => <label key={item.value} className={`button button-quiet${decision === item.value ? " selected" : ""}`}><input type="radio" name={`admission-decision-${round.id}`} value={item.value} checked={decision === item.value} disabled={review.isPending} onChange={() => setDecision(item.value)} />{item.label}</label>)}</fieldset>
        <label className="field"><span>審核原因<OptionalMark /></span><textarea maxLength={2000} value={reason} disabled={review.isPending} onChange={event => setReason(event.target.value)} /></label>
        {decision === "approve" && <><label className="field"><span>理事會決議編號或日期</span><input required maxLength={200} value={reference} disabled={review.isPending} onChange={event => setReference(event.target.value)} /></label>{current.student_claimed && <label className="check-field"><input required type="checkbox" checked={studentVerified} disabled={review.isPending} onChange={event => setStudentVerified(event.target.checked)} />已核驗學生資格</label>}{!qualified && <p className="form-error" role="alert">本輪測驗尚未合格，不能同意入社。</p>}</>}
        {changed && <><p className="form-error" role="alert">申請資料已變更，請重新讀取並核對最新資料；已填內容會保留。</p><button type="button" className="button button-quiet" disabled={reload.isPending} onClick={() => reload.mutate()}>重新讀取最新資料</button></>}
        {(review.isError || reload.isError) && <p className="form-error" role="alert">{reload.error?.message || review.error?.message}</p>}
        <button className="button button-primary" disabled={disabled}>{review.isPending ? "儲存中…" : `確認${reviewLabel(decision)}`}</button>
      </form>}
      {review.isSuccess && <p role="status">審核結果已記錄。</p>}
      {current.status === "approved" && <p role="status">審核已同意；繳費、銀行核帳及發證尚未開放，尚未取得正式社員資格。</p>}
      {current.status === "needs_supplement" && <p role="status">等待申請人補件並重新送審。</p>}
      {current.review_reason && current.status !== "submitted" && <p>審核原因：{current.review_reason}</p>}
    </div>
  </article>;
}
