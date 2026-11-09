import { CheckCircle, UserCircle } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { DataState, LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import { ApiError, apiFetch, formatMoney } from "../lib/api";
import {
  admissionApi, admissionDateTime, admissionSavePayload, admissionStatusLabel, createAdmissionDraft,
  type AdmissionConfig, type AdmissionDraft, type AdmissionProfile, type AdmissionQuizAttempt,
  type AdmissionRead, type AdmissionRound, type AdmissionSavePayload,
} from "../lib/membership-admission";
import type { MembershipSummary, User } from "../lib/types";
import "./MembershipAdmissionPage.css";

type Step = 1 | 2 | 3 | 4;
type Command =
  | { kind: "start"; key: string }
  | { kind: "save"; id: string; payload: AdmissionSavePayload }
  | { kind: "education"; id: string; version: number; materialVersion: string }
  | { kind: "quiz"; id: string; version: number; key: string; quizVersion: string; answers: { question_id: string; option: string }[] }
  | { kind: "submit" | "withdraw"; id: string; version: number };

const steps = ["資料與志願書", "閱讀教材", "入社測驗", "申請進度"];
const genderOptions = ["男", "女", "非二元", "不透露", "其他"];
const originOptions = ["台北市", "新北市", "桃園市", "台中市", "台南市", "高雄市", "基隆市", "新竹市", "嘉義市", "新竹縣", "苗栗縣", "彰化縣", "南投縣", "雲林縣", "嘉義縣", "屏東縣", "宜蘭縣", "花蓮縣", "台東縣", "澎湖縣", "金門縣", "連江縣", "其他／國外"];
const occupationOptions = ["農林漁牧", "製造／營造", "商業／金融", "教育／研究", "醫療／照護", "資訊／科技", "餐飲／服務", "公務／行政", "自由業", "學生", "家管／照護", "退休", "待業", "其他"];
const accessDenied = (error: unknown) => error instanceof ApiError && [401, 403].includes(error.status);

function latestAttempt(round: AdmissionRound | null): AdmissionQuizAttempt | undefined {
  return round?.attempts.reduce<AdmissionQuizAttempt | undefined>((latest, attempt) => !latest || attempt.attempt_number > latest.attempt_number ? attempt : latest, undefined);
}

function savedProfile(round: AdmissionRound | null): boolean {
  return Boolean(round?.profile && round.consented_at);
}

function completedReading(round: AdmissionRound | null, config: AdmissionConfig): boolean {
  return Boolean(round?.material_completed_at && round.material_version === config.material.version);
}

function passedQuiz(round: AdmissionRound | null, config: AdmissionConfig): boolean {
  return Boolean(round?.attempts.some(attempt => attempt.passed && attempt.quiz_version === config.quiz.version));
}

function currentStep(round: AdmissionRound | null, config: AdmissionConfig): Step {
  if (round && !round.can_edit) return 4;
  if (!savedProfile(round)) return 1;
  if (!completedReading(round, config)) return 2;
  return passedQuiz(round, config) ? 4 : 3;
}

function execute(command: Command): Promise<AdmissionRound> {
  switch (command.kind) {
    case "start": return admissionApi.start(command.key);
    case "save": return admissionApi.save(command.id, command.payload);
    case "education": return admissionApi.education(command.id, command.version, command.materialVersion);
    case "quiz": return admissionApi.quiz(command.id, { expected_version: command.version, idempotency_key: command.key, quiz_version: command.quizVersion, answers: command.answers });
    case "submit": return admissionApi.submit(command.id, command.version);
    case "withdraw": return admissionApi.withdraw(command.id, command.version);
  }
}

export function MembershipAdmissionPage() {
  const { user, openLogin } = useAuth();
  const membership = useQuery({
    queryKey: ["membership-me", user?.id],
    queryFn: () => apiFetch<MembershipSummary>("/v1/members/me"),
    enabled: Boolean(user),
  });
  if (!user) return <section className="account-gate"><UserCircle size={48} weight="light" /><p className="eyebrow">MEMBERSHIP / 入社申請</p><h1>開始入社申請</h1><button className="button button-primary" type="button" onClick={openLogin}>登入後開始</button></section>;
  if (membership.isPending) return <section className="membership-admission"><LoadingLines count={5} /></section>;
  if (membership.isError) return <section className="membership-admission"><DataState kind="error" title="無法確認社員資格" detail={membership.error.message} onAction={() => void membership.refetch()} /></section>;
  if (membership.data.membership_type === "member") return <section className="membership-admission"><header className="membership-heading"><CheckCircle size={42} weight="light" /><p className="eyebrow">ACTIVE MEMBER / 正式社員</p><h1>你已是正式社員</h1><p>{membership.data.membership?.member_number || "社員資格已啟用"}</p><div><Link className="button button-primary" to="/social">進入社務系統</Link><Link className="button button-quiet" to="/account" search={{ membership_charge_id: undefined, payment: undefined }}>查看社員中心</Link></div></header></section>;
  return <AdmissionWorkspace key={user.id} user={user} />;
}

function AdmissionWorkspace({ user }: { user: User }) {
  const client = useQueryClient();
  const queryKey = ["membership-admission", user.id];
  const [revoked, setRevoked] = useState(false);
  const read = useQuery({ queryKey, queryFn: admissionApi.read, enabled: !revoked, refetchOnWindowFocus: false, retry: (count, error) => !accessDenied(error) && count < 1 });
  const initialized = useRef(false);
  const [round, setRound] = useState<AdmissionRound | null>(null);
  const [draft, setDraft] = useState<AdmissionDraft | null>(null);
  const [step, setStep] = useState<Step>(1);
  const [dirty, setDirty] = useState(false);
  const [readConfirmed, setReadConfirmed] = useState(false);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [answering, setAnswering] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [reloading, setReloading] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [reloadError, setReloadError] = useState("");
  const startKey = useRef(crypto.randomUUID());
  const quizRequest = useRef({ fingerprint: "", key: crypto.randomUUID() });
  const email = user.notification_email || user.email || "";

  function revoke() {
    setRevoked(true);
    setRound(null);
    setDraft(null);
    setAnswers({});
    client.removeQueries({ queryKey: ["membership-admission", user.id] });
    client.removeQueries({ queryKey: ["membership-admission-history", user.id] });
  }

  useEffect(() => {
    if (!accessDenied(read.error)) return;
    revoke();
  }, [read.error, client, user.id]);

  useEffect(() => {
    if (!read.data || initialized.current) return;
    initialized.current = true;
    setRound(read.data.current_round);
    setDraft(createAdmissionDraft(read.data, email));
    setStep(currentStep(read.data.current_round, read.data.config));
    setAnswering(!latestAttempt(read.data.current_round));
  }, [read.data, email]);

  const action = useMutation({
    mutationFn: execute,
    retry: false,
    onSuccess: (next, command) => {
      setRound(next);
      setConflict(false);
      client.setQueryData<AdmissionRead>(queryKey, previous => previous ? { ...previous, current_round: next } : previous);
      void client.invalidateQueries({ queryKey });
      if (command.kind === "start") {
        startKey.current = crypto.randomUUID();
        const value = read.data!;
        setDraft(createAdmissionDraft({ ...value, current_round: next }, email));
        setDirty(false);
        setAnswers({});
        setAnswering(true);
        setReadConfirmed(false);
        setStep(1);
        setFeedback("本次入社申請已建立。");
      } else if (command.kind === "save") {
        setDirty(false);
        setStep(2);
        setFeedback("資料與志願書已儲存。");
      } else if (command.kind === "education") {
        setStep(3);
        setFeedback("閱讀聲明已儲存，可以開始測驗。");
      } else if (command.kind === "quiz") {
        setAnswering(false);
        setFeedback("本次測驗已交卷。");
      } else {
        setStep(4);
        setFeedback(command.kind === "submit" ? "申請已送出，請在這裡查看審核進度。" : "本次申請已撤回。");
      }
    },
    onError: error => {
      if (accessDenied(error)) {
        revoke();
      } else if (error instanceof ApiError && error.status === 409) setConflict(true);
    },
  });

  function edit(change: Partial<AdmissionDraft>) {
    setDraft(previous => previous ? { ...previous, ...change } : previous);
    setDirty(true);
    setFeedback("");
    if (!conflict) action.reset();
  }

  function editProfile(key: keyof AdmissionProfile, value: string) {
    if (draft) edit({ profile: { ...draft.profile, [key]: value } });
  }

  async function reload(preserveDraft: boolean) {
    setReloading(true);
    setReloadError("");
    try {
      const result = await read.refetch();
      if (result.isError || !result.data) {
        setReloadError(result.error?.message || "無法讀取最新進度，填寫內容仍保留。");
        return;
      }
      const changedRound = result.data.current_round?.id !== round?.id;
      const changedAttempt = latestAttempt(result.data.current_round)?.id !== latestAttempt(round)?.id;
      setRound(result.data.current_round);
      if (!preserveDraft) {
        setDraft(createAdmissionDraft(result.data, email));
        setDirty(false);
      } else if (changedRound) {
        setDraft(previous => previous ? { ...previous, declaration_accepted: false, privacy_accepted: false, share_rules_understood: false, payment_notice_understood: false } : previous);
        setDirty(true);
      }
      if (changedRound) {
        setAnswers({});
        setReadConfirmed(false);
        setAnswering(!latestAttempt(result.data.current_round));
      } else if (changedAttempt) {
        setAnswering(!latestAttempt(result.data.current_round));
      }
      setConflict(false);
      action.reset();
      setFeedback(preserveDraft ? "已讀取最新進度，填寫內容仍保留。請確認後再儲存。" : "申請進度已更新。");
      if (!preserveDraft) setStep(currentStep(result.data.current_round, result.data.config));
    } finally {
      setReloading(false);
    }
  }

  if (revoked || accessDenied(read.error)) return <section className="membership-admission"><h1>請重新登入</h1><p className="form-error" role="alert">入社資料的存取權限已失效，請重新登入後查看。</p><a className="button button-primary" href="/login?return_to=%2Fmembership">重新登入</a></section>;
  if (read.isPending || !initialized.current && !read.isError) return <section className="membership-admission"><LoadingLines count={5} /></section>;
  if (read.isError && !read.data) return <section className="membership-admission"><DataState kind="error" title="入社申請暫時無法讀取" detail={read.error.message} onAction={() => void read.refetch()} /></section>;
  if (!read.data || !draft) return null;
  const { config } = read.data;
  const busy = action.isPending || reloading;
  const editable = Boolean(round?.can_edit) && !busy && !conflict;
  const hasProfile = savedProfile(round);
  const hasReading = completedReading(round, config);
  const passed = passedQuiz(round, config);
  const lastAttempt = latestAttempt(round);
  const shareMinimum = draft.student_claimed ? config.declaration.student_min_shares : config.declaration.general_min_shares;
  const shareMaximum = draft.student_claimed ? config.declaration.student_max_shares : config.declaration.general_max_shares;
  const shareValid = /^\d+$/.test(draft.requested_share_count) && Number(draft.requested_share_count) >= shareMinimum && Number(draft.requested_share_count) <= shareMaximum;
  const consentValid = draft.declaration_accepted && draft.privacy_accepted && draft.share_rules_understood && draft.payment_notice_understood;
  const canSave = editable && shareValid && consentValid && Boolean(draft.discovery_channel);
  const canAnswer = editable && hasReading && !dirty && Boolean(round?.quiz_eligibility.allowed) && !passed;
  const allAnswered = config.quiz.questions.every(question => question.options.some(option => option.value === answers[question.id]));
  const stepAvailable = (value: Step) => value === 1 || value === 4 || !dirty && (value === 2 ? hasProfile : hasReading);

  function save(event: FormEvent) {
    event.preventDefault();
    if (canSave && round && draft) action.mutate({ kind: "save", id: round.id, payload: admissionSavePayload(draft, config, round.version) });
  }

  function submitQuiz(event: FormEvent) {
    event.preventDefault();
    if (!canAnswer || !allAnswered || !round) return;
    const submittedAnswers = config.quiz.questions.map(question => ({ question_id: question.id, option: answers[question.id] }));
    const fingerprint = JSON.stringify({ version: round.version, quiz_version: config.quiz.version, answers: submittedAnswers });
    if (quizRequest.current.fingerprint !== fingerprint) quizRequest.current = { fingerprint, key: crypto.randomUUID() };
    action.mutate({ kind: "quiz", id: round.id, version: round.version, key: quizRequest.current.key, quizVersion: config.quiz.version, answers: submittedAnswers });
  }

  return <section className="membership-admission">
    <header className="membership-admission-heading">
      <div><p className="eyebrow">MEMBERSHIP APPLICATION / 入社申請</p><h1>入社申請</h1></div>
      <div className="admission-heading-status">{round && <span className="status-chip">第 {round.round_number} 次申請 · {admissionStatusLabel(round.status)}</span>}<Link className="text-link" to="/membership/legacy">查看舊版入社資料</Link></div>
    </header>
    {conflict && <div className="admission-conflict" role="alert"><p>申請資料已變更。填寫內容仍保留，請先重新讀取最新進度，再確認要儲存的內容。</p><button type="button" className="button button-quiet" disabled={busy} onClick={() => void reload(true)}>重新讀取最新進度</button></div>}
    {reloadError && <p className="form-error" role="alert">{reloadError}</p>}
    {action.isError && !conflict && <p className="form-error" role="alert">{action.error.message}</p>}
    {feedback && <p className="form-success admission-feedback" role="status">{feedback}</p>}

    {!round ? <div className="admission-panel"><h2>開始你的入社申請</h2><ol>{steps.map(label => <li key={label}>{label}</li>)}</ol>{read.data.can_start ? <div className="admission-actions"><button type="button" className="button button-primary" disabled={busy || conflict} onClick={() => action.mutate({ kind: "start", key: startKey.current })}>{busy ? "建立中…" : "開始入社申請"}</button></div> : <p className="form-error" role="status">{read.data.start_blocked_reason || "目前無法開始新的入社申請，請聯絡合作社確認資格。"}</p>}</div> : <>
      <nav aria-label="入社申請步驟"><ol className="admission-steps">{steps.map((label, index) => { const value = index + 1 as Step; return <li key={label}><button type="button" aria-current={step === value ? "step" : undefined} disabled={busy || !stepAvailable(value)} onClick={() => { setStep(value); setFeedback(""); }}><span>{String(value).padStart(2, "0")}</span><span>{label}</span></button></li>; })}</ol></nav>

      {step === 1 && <form className="admission-panel" onSubmit={save}>
        <h2>資料與志願書</h2>
        <fieldset disabled={!editable}>
          <h3>基本資料（必填）</h3>
          <div className="field-grid two-columns">
            <label className="field"><span>姓名</span><input required autoComplete="name" maxLength={100} value={draft.profile.legal_name} onChange={event => editProfile("legal_name", event.target.value)} /></label>
            <label className="field"><span>出生日期</span><input required type="date" autoComplete="bday" value={draft.profile.birth_date} onChange={event => editProfile("birth_date", event.target.value)} /></label>
            <label className="field"><span>手機</span><input required type="tel" autoComplete="tel" minLength={8} maxLength={40} value={draft.profile.phone} onChange={event => editProfile("phone", event.target.value)} /></label>
            <label className="field"><span>聯絡 Email</span><input required type="email" autoComplete="email" maxLength={254} value={draft.profile.contact_email} onChange={event => editProfile("contact_email", event.target.value)} /></label>
            <label className="field wide"><span>現居地址</span><input required autoComplete="street-address" maxLength={500} value={draft.profile.address} onChange={event => editProfile("address", event.target.value)} /></label>
            <label className="field wide"><span>緊急聯絡人與電話</span><input required maxLength={200} value={draft.profile.emergency_contact} onChange={event => editProfile("emergency_contact", event.target.value)} /></label>
          </div>
          <details className="admission-optional-fields"><summary>其他基本資料（選填）</summary><div className="field-grid two-columns">
            <ProfileSelect label="性別" value={draft.profile.gender} options={genderOptions} onChange={value => editProfile("gender", value)} />
            <ProfileSelect label="籍貫" value={draft.profile.place_of_origin} options={originOptions} onChange={value => editProfile("place_of_origin", value)} />
            <ProfileSelect label="職業" value={draft.profile.occupation} options={occupationOptions} onChange={value => editProfile("occupation", value)} />
            <label className="field"><span>市話</span><input type="tel" maxLength={30} value={draft.profile.landline_phone || ""} onChange={event => editProfile("landline_phone", event.target.value)} /></label>
            <label className="field"><span>LINE ID</span><input maxLength={100} value={draft.profile.line_id || ""} onChange={event => editProfile("line_id", event.target.value)} /></label>
            <label className="field wide"><span>戶籍地址</span><input maxLength={500} value={draft.profile.registered_address || ""} onChange={event => editProfile("registered_address", event.target.value)} /></label>
            <label className="field wide"><span>通訊地址</span><input maxLength={500} value={draft.profile.correspondence_address || ""} onChange={event => editProfile("correspondence_address", event.target.value)} /></label>
          </div></details>
          <div className="admission-volunteer"><h3>入社志願書</h3><p className="admission-volunteer-copy">{config.declaration.statement}</p><details><summary>社員權利與義務</summary><h3>社員權利</h3><ul>{config.declaration.rights.map(text => <li key={text}>{text}</li>)}</ul><h3>社員義務</h3><ul>{config.declaration.obligations.map(text => <li key={text}>{text}</li>)}</ul></details></div>
          <fieldset className="admission-share-fields"><legend>認股意向</legend><div className="admission-choice-row">
            <label><input type="radio" name="admission_student" checked={!draft.student_claimed} onChange={() => edit({ student_claimed: false })} />一般申請</label>
            <label><input type="radio" name="admission_student" checked={draft.student_claimed} onChange={() => edit({ student_claimed: true })} />全職在校學生</label>
          </div><label className="field"><span>認購股數（{shareMinimum}–{shareMaximum} 股，每股 {formatMoney(config.declaration.share_unit_amount)}）</span><input required type="number" min={shareMinimum} max={shareMaximum} step={1} inputMode="numeric" value={draft.requested_share_count} onChange={event => edit({ requested_share_count: event.target.value })} /></label>{!shareValid && <p className="form-error" role="alert">請填寫 {shareMinimum}–{shareMaximum} 的整數股數。</p>}</fieldset>
          <div className="field-grid two-columns admission-discovery-fields"><label className="field"><span>如何得知合作社</span><select required value={draft.discovery_channel} onChange={event => edit({ discovery_channel: event.target.value, discovery_other: "" })}><option value="" disabled>請選擇來源</option>{config.discovery_channels.map(channel => <option key={channel} value={channel}>{channel}</option>)}</select></label>{draft.discovery_channel === "其他" && <label className="field"><span>其他來源（選填）</span><input maxLength={500} value={draft.discovery_other} onChange={event => edit({ discovery_other: event.target.value })} /></label>}</div>
          <div className="admission-consents">
            <label className="check-field"><input required type="checkbox" checked={draft.declaration_accepted} onChange={event => edit({ declaration_accepted: event.target.checked })} /><span>我已閱讀並同意上述入社志願書、社員權利與義務。</span></label>
            <label className="check-field"><input required type="checkbox" checked={draft.privacy_accepted} onChange={event => edit({ privacy_accepted: event.target.checked })} /><span>我同意合作社為入社審核與社員管理使用上述資料，並已閱讀<a href={config.declaration.privacy_url} target="_blank" rel="noopener noreferrer">個人資料使用說明</a>。</span></label>
            <label className="check-field"><input required type="checkbox" checked={draft.share_rules_understood} onChange={event => edit({ share_rules_understood: event.target.checked })} /><span>我了解一般申請認購 {config.declaration.general_min_shares}–{config.declaration.general_max_shares} 股；學生認購 {config.declaration.student_min_shares}–{config.declaration.student_max_shares} 股，學生資格由管理員核驗。</span></label>
            <label className="check-field"><input required type="checkbox" checked={draft.payment_notice_understood} onChange={event => edit({ payment_notice_understood: event.target.checked })} /><span>我了解審核同意後才依後續通知辦理入社費及股金，目前不需繳費。</span></label>
          </div>
        </fieldset>
        {round.can_edit && <div className="admission-actions"><button className="button button-primary" disabled={!canSave}>{action.isPending ? "儲存中…" : "儲存並閱讀教材"}</button>{hasProfile && !dirty && <button type="button" className="button button-quiet" disabled={busy || conflict} onClick={() => setStep(2)}>繼續閱讀教材</button>}</div>}
      </form>}

      {step === 2 && <section className="admission-panel" aria-labelledby="admission-material-heading"><h2 id="admission-material-heading">閱讀入社教材</h2><div className="admission-material"><strong>{config.material.title}</strong><a className="button button-quiet" href={config.material.url} target="_blank" rel="noopener noreferrer">開啟閱讀教材</a></div><div className="admission-material-sections">{config.material.sections.map(section => <section key={section.title}><h3>{section.title}</h3><p>{section.body}</p></section>)}</div>{hasReading ? <><p className="form-success" role="status">已於 {admissionDateTime(round.material_completed_at)} 完成閱讀聲明。</p><div className="admission-actions"><button type="button" className="button button-primary" disabled={busy || conflict || dirty} onClick={() => setStep(passed ? 4 : 3)}>{passed ? "查看申請進度" : "進入入社測驗"}</button></div></> : <><label className="check-field admission-reading-confirmation"><input type="checkbox" checked={readConfirmed} disabled={!editable || !hasProfile || dirty} onChange={event => setReadConfirmed(event.target.checked)} /><span>我已閱讀本次入社教材及以上閱讀重點。</span></label><div className="admission-actions"><button type="button" className="button button-primary" disabled={!editable || !hasProfile || dirty || !readConfirmed} onClick={() => action.mutate({ kind: "education", id: round.id, version: round.version, materialVersion: config.material.version })}>{action.isPending ? "儲存中…" : "確認閱讀並開始測驗"}</button></div></>}</section>}

      {step === 3 && <section className="admission-panel" aria-labelledby="admission-quiz-heading"><h2 id="admission-quiz-heading">入社測驗</h2><p className="admission-quiz-rule">共 {config.quiz.questions.length} 題，每題 {config.quiz.points_per_question} 分，{config.quiz.pass_score} 分合格。</p>{lastAttempt && !answering && <QuizResult attempt={lastAttempt} config={config} />}{passed ? <div className="admission-actions"><button type="button" className="button button-primary" disabled={busy || conflict || dirty} onClick={() => setStep(4)}>前往送出申請</button></div> : <>
        <div className="admission-quiz-availability"><p>本輪已交卷 {round.attempts.length} 次 · 剩餘可交卷 {round.quiz_eligibility.remaining_attempts} 次</p>{!round.quiz_eligibility.allowed && <p className="form-error" role="status">{round.quiz_eligibility.reason || "目前無法再次交卷，請更新資格或聯絡管理員。"}{round.quiz_eligibility.next_available_at && ` 可再次作答時間：${admissionDateTime(round.quiz_eligibility.next_available_at)}（台灣時間）`}</p>}</div>
        {answering ? <form onSubmit={submitQuiz}><fieldset disabled={!canAnswer}>{config.quiz.questions.map((question, index) => <fieldset className="admission-quiz-question" key={question.id}><legend>{index + 1}. {question.question}</legend><div className="admission-quiz-options">{question.options.map(option => <label className="admission-quiz-option" key={option.value}><input type="radio" required name={`admission_quiz_${question.id}`} value={option.value} checked={answers[question.id] === option.value} onChange={() => { setAnswers(previous => ({ ...previous, [question.id]: option.value })); setFeedback(""); }} /><span>{option.value}. {option.label}</span></label>)}</div></fieldset>)}</fieldset><p>已完成 {config.quiz.questions.filter(question => answers[question.id]).length}／{config.quiz.questions.length} 題</p><div className="admission-actions"><button className="button button-primary" disabled={!canAnswer || !allAnswered}>{action.isPending ? "交卷中…" : "交卷並查看結果"}</button></div></form> : canAnswer && <div className="admission-actions"><button type="button" className="button button-primary" onClick={() => { setAnswers({}); setAnswering(true); setFeedback(""); }}>開始重考</button></div>}
        {!round.quiz_eligibility.allowed && <div className="admission-actions"><button type="button" className="button button-quiet" disabled={busy} onClick={() => void reload(true)}>更新測驗資格</button></div>}
      </>}</section>}

      {step === 4 && <section className="admission-panel" aria-labelledby="admission-progress-heading"><h2 id="admission-progress-heading">申請進度</h2><dl className="admission-progress-list"><div><dt>目前狀態</dt><dd>{admissionStatusLabel(round.status)}</dd></div><div><dt>資料與志願書</dt><dd>{hasProfile ? "已儲存" : "尚未完成"}</dd></div><div><dt>教材閱讀</dt><dd>{hasReading ? admissionDateTime(round.material_completed_at) : "尚未完成"}</dd></div><div><dt>入社測驗</dt><dd>{passed ? "合格" : lastAttempt ? `${lastAttempt.score} 分，尚未合格` : "尚未交卷"}</dd></div>{round.submitted_at && <div><dt>送出申請</dt><dd>{admissionDateTime(round.submitted_at)}</dd></div>}{round.reviewed_at && <div><dt>審核時間</dt><dd>{admissionDateTime(round.reviewed_at)}</dd></div>}{round.review_reason && <div><dt>審核意見</dt><dd>{round.review_reason}</dd></div>}{round.decision_reference && <div><dt>審核決議</dt><dd>{round.decision_reference}</dd></div>}</dl>
        {round.status === "approved" && <p className="form-success" role="status">本次申請已審核同意。繳費與正式入社程序尚未開放，待合作社後續通知；目前尚未取得正式社員資格。</p>}
        {round.status === "needs_supplement" && <div className="admission-actions"><button type="button" className="button button-primary" disabled={busy || conflict} onClick={() => setStep(1)}>依審核意見補充資料</button></div>}
        {round.can_edit && <div className="admission-actions">{round.can_submit && !dirty ? <button type="button" className="button button-primary" disabled={busy || conflict} onClick={() => action.mutate({ kind: "submit", id: round.id, version: round.version })}>{action.isPending ? "送出中…" : round.status === "needs_supplement" ? "重新送出審核" : "送出入社申請"}</button> : <button type="button" className="button button-primary" disabled={busy || conflict} onClick={() => setStep(dirty || !hasProfile ? 1 : !hasReading ? 2 : 3)}>繼續完成申請</button>}</div>}
        <div className="admission-actions"><button type="button" className="button button-quiet" disabled={busy} onClick={() => void reload(dirty || conflict)}>更新申請進度</button>{["rejected", "withdrawn"].includes(round.status) && read.data.can_start && <button type="button" className="button button-primary" disabled={busy || conflict} onClick={() => action.mutate({ kind: "start", key: startKey.current })}>重新開始入社申請</button>}</div>
        {round.can_withdraw && <details className="admission-withdraw"><summary>撤回本次申請</summary><div className="admission-actions"><button type="button" className="button button-quiet" disabled={busy || conflict} onClick={() => action.mutate({ kind: "withdraw", id: round.id, version: round.version })}>確認撤回本次申請</button></div></details>}
      </section>}
    </>}
    {read.data.history.some(item => item.id !== round?.id) && <AdmissionHistory key={`${user.id}:${round?.id}`} userId={user.id} history={read.data.history.filter(item => item.id !== round?.id)} config={config} onDenied={revoke} />}
  </section>;
}

function AdmissionHistory({ userId, history, config, onDenied }: { userId: string; history: AdmissionRead["history"]; config: AdmissionConfig; onDenied: () => void }) {
  const [selectedId, setSelectedId] = useState("");
  const query = useQuery({ queryKey: ["membership-admission-history", userId, selectedId], queryFn: () => admissionApi.round(selectedId), enabled: Boolean(selectedId), refetchOnWindowFocus: false, retry: (count, error) => !accessDenied(error) && count < 1 });
  useEffect(() => { if (accessDenied(query.error)) onDenied(); }, [query.error, onDenied]);
  if (accessDenied(query.error)) return null;
  return <details className="admission-history"><summary>歷次入社申請（{history.length} 次）</summary><label className="field"><span>選擇歷次申請</span><select value={selectedId} onChange={event => setSelectedId(event.target.value)}><option value="" disabled>請選擇申請輪次</option>{history.map(item => <option key={item.id} value={item.id}>第 {item.round_number} 次 · {admissionStatusLabel(item.status)} · {admissionDateTime(item.created_at)}</option>)}</select></label>
    {selectedId && (query.isPending ? <LoadingLines count={3} /> : query.isError ? <DataState kind="error" title="歷次申請無法讀取" detail={query.error.message} onAction={() => void query.refetch()} /> : <section className="admission-history-detail" aria-label="歷次入社申請詳情"><h3>第 {query.data.round_number} 次申請 · {admissionStatusLabel(query.data.status)}</h3><dl className="admission-progress-list"><div><dt>教材閱讀</dt><dd>{admissionDateTime(query.data.material_completed_at)}</dd></div><div><dt>送審時間</dt><dd>{admissionDateTime(query.data.submitted_at)}</dd></div>{query.data.review_reason && <div><dt>審核意見</dt><dd>{query.data.review_reason}</dd></div>}</dl>{query.data.attempts.map(attempt => <details key={attempt.id}><summary>第 {attempt.attempt_number} 次測驗 · {attempt.score} 分 · {attempt.passed ? "合格" : "未合格"}</summary><QuizResult attempt={attempt} config={config} /></details>)}{query.data.reviews.map(review => <p key={review.id}>{admissionDateTime(review.reviewed_at)}{review.reason && ` · ${review.reason}`}{review.decision_reference && ` · ${review.decision_reference}`}</p>)}</section>)}
  </details>;
}

function ProfileSelect({ label, value, options, onChange }: { label: string; value: string | null; options: string[]; onChange: (value: string) => void }) {
  return <label className="field"><span>{label}</span><select value={value || ""} onChange={event => onChange(event.target.value)}><option value="">請選擇</option>{value && !options.includes(value) && <option value={value}>{value}</option>}{options.map(option => <option key={option} value={option}>{option}</option>)}</select></label>;
}

function QuizResult({ attempt, config }: { attempt: AdmissionQuizAttempt; config: AdmissionConfig }) {
  return <section aria-label="本次測驗結果"><div className="admission-result-score"><strong>{attempt.score} 分</strong><span>第 {attempt.attempt_number} 次測驗 · {attempt.passed ? "合格" : "尚未合格"}</span></div><ol className="admission-result-list">{attempt.results.map(result => {
    const question = attempt.quiz_version === config.quiz.version ? config.quiz.questions.find(item => item.id === result.question_id) : undefined;
    const optionText = (value: string) => question?.options.find(option => option.value === value)?.label || value;
    return <li key={result.question_id}><strong>{question?.question || result.question_id}</strong><p>{result.correct ? "答對" : "答錯"} · 你的答案：{result.selected_option}. {optionText(result.selected_option)}</p><p>正確答案：{result.correct_option}. {optionText(result.correct_option)}</p></li>;
  })}</ol></section>;
}
