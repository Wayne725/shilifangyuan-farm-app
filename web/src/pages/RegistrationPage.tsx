import { ArrowLeft, ArrowRight, HandHeart, ShoppingBag } from "@phosphor-icons/react";
import { Link, Navigate, useSearch } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { apiFetch } from "../lib/api";
import { loginReturnTo } from "../lib/login-navigation";
import { LineLoginButton } from "../components/LineLogin";
import { useStorefrontStatus } from "../components/StorefrontStatus";
import { LoadingLines } from "../components/Shared";
import type { AuthActionResponse } from "../lib/types";
import "../styles/auth-options.css";

type Intent = "existing_member" | "nonmember";
export function RegisterPage() {
  const search = useSearch({ from: "/register" });
  const { user, isAuthReady } = useAuth();
  const returnTo = loginReturnTo(search.return_to || "/shop");
  const [intent, setIntent] = useState<Intent | undefined>(search.intent);
  const [step, setStep] = useState<"identity" | "method" | "email" | "line" | "login">(search.intent === "nonmember" ? "method" : "identity");
  const [legalName, setLegalName] = useState("");
  const [memberNumber, setMemberNumber] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const information = useStorefrontStatus().data?.public_information;
  const [dataConsent, setDataConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<AuthActionResponse | null>(null);
  if (!isAuthReady) return <section className="auth-page" aria-label="正在確認登入"><LoadingLines count={3} /></section>;
  if (user) return <Navigate from="/register" href={returnTo} replace />;
  const member = intent === "existing_member";
  const returning = step === "login";
  const lineClaim = { phone: phone.trim(), email: email.trim() };
  const claim = { member_number: memberNumber.trim(), legal_name: legalName.trim(), email: email.trim() };
  const selectIntent = (value: Intent) => { setIntent(value); setStep(value === "existing_member" ? "identity" : "method"); setError(""); };
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setError("");
    if (information?.published && !dataConsent) { setError("請閱讀並同意個資告知與服務條款"); return; }
    if (password !== confirmation) { setError("兩次輸入的密碼不一致"); return; }
    setBusy(true);
    try {
      setResult(await apiFetch<AuthActionResponse>(member ? "/v1/auth/register-existing-member" : "/v1/auth/register", { method: "POST", body: JSON.stringify({ ...(member ? { ...claim, display_name: claim.legal_name, password } : { display_name: displayName.trim(), email: email.trim(), password }), ...(information?.published ? { data_consent: dataConsent, policy_version: information.policy_version } : {}) }) }));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "無法完成註冊"); }
    finally { setBusy(false); }
  };
  const title = returning ? "使用 LINE 登入" : !intent ? "你是否為既有社員？" : step === "identity" ? "社員資料驗證" : step === "method" ? "選擇註冊方式" : step === "line" ? "使用 LINE 註冊" : "使用 Email 註冊";
  return <section className="auth-page registration-flow">
    <aside className="auth-page-intro"><Link className="auth-back-link" to="/">十里方圓合作社</Link><div><p className="eyebrow">{returning ? "SIGN IN / 既有帳號" : "CREATE ACCOUNT / 建立帳號"}</p><h1>{returning ? "登入帳號" : "註冊帳號"}</h1></div></aside>
    <div className="auth-page-body"><div className="auth-card">
      {intent && !result && <button className="registration-back" type="button" onClick={() => { setError(""); if (step === "identity" || step === "method" && !member) setIntent(undefined); else setStep(member ? "identity" : "method"); }}><ArrowLeft size={17} />上一步</button>}
      <p className="eyebrow">{returning ? "SIGN IN / 既有帳號" : !intent ? "MEMBERSHIP / 社員身分" : member ? "EXISTING MEMBER / 既有社員" : "CUSTOMER / 非社員"}</p>
      <h2>{result ? "請驗證你的 Email" : title}</h2>
      {member && !returning && !result && <button className="button button-quiet registration-existing-entry" type="button" onClick={() => { setStep("login"); setError(""); }}>曾用 LINE 登入，直接登入</button>}
      {result ? <div className="auth-result"><p>{result.message}</p>{result.development_token && <p>本機測試碼：{result.development_token}</p>}<Link className="button button-primary" to="/verify-email" search={{ email, token: result.development_token, intent, return_to: returnTo }}>輸入驗證碼<ArrowRight size={17} /></Link></div>
        : !intent ? <div className="registration-choice-grid"><button className="registration-choice-card member" type="button" onClick={() => selectIntent("existing_member")}><HandHeart size={28} /><h3>我是既有社員</h3></button><button className="registration-choice-card customer" type="button" onClick={() => selectIntent("nonmember")}><ShoppingBag size={28} /><h3>我不是社員</h3></button></div>
        : step === "identity" ? <form className="auth-form" onSubmit={(event) => { event.preventDefault(); setStep("line"); }}><label className="field"><span>電話號碼</span><input required type="tel" autoComplete="tel" minLength={8} maxLength={24} value={phone} onChange={(event) => setPhone(event.target.value)} /></label><label className="field"><span>Email</span><input required type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} /></label><button className="button button-primary" disabled={phone.replace(/\D/g, "").length < 8 || !email.trim()}>繼續使用 LINE<ArrowRight size={17} /></button><button className="button button-quiet" type="button" onClick={() => setStep("email")}>使用 Email 註冊</button></form>
        : step === "method" ? <div className="registration-methods"><button className="button button-primary" type="button" onClick={() => setStep("line")}>使用 LINE 註冊</button><button className="button button-quiet" type="button" onClick={() => setStep("email")}>使用 Email 註冊</button></div>
        : step === "line" || returning ? <><section className="line-consent-details"><h3>LINE 資料使用說明</h3><p>{returning ? "使用 LINE 識別碼及顯示名稱登入已連結的網站帳號，沿用原帳號的訂單與社員資格。" : <>使用 LINE 識別碼及顯示名稱建立網站帳號。{member ? "電話號碼與 Email 用於核對社員名冊；顯示名稱使用 LINE 名稱，完成原信箱驗證後啟用資格。" : "註冊後建立非社員帳號，通知信箱會於需要時填寫。"}</>}</p><Link to="/line-consent" search={{ return_to: returnTo, remember_me: search.remember_me !== false }}>閱讀完整資料使用說明</Link></section><LineLoginButton key={returning ? "login" : "registration"} intent={member && !returning ? "member" : "nonmember"} returnTo={returnTo} rememberMe={search.remember_me !== false} memberClaim={member && !returning ? lineClaim : undefined} consentLink={false} consentText={returning ? "我已閱讀並同意十里方圓依上述用途使用及保存 LINE 登入資料。" : "我已閱讀並同意 LINE 登入及社員資料的使用說明。"} /></>
        : <form className="auth-form" onSubmit={submit}>{member ? <><label className="field"><span>姓名</span><input required autoComplete="name" maxLength={80} value={legalName} onChange={(event) => setLegalName(event.target.value)} /></label><label className="field"><span>社員編號</span><input required maxLength={32} value={memberNumber} onChange={(event) => setMemberNumber(event.target.value)} /></label><label className="field"><span>Email</span><input required type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} /></label></> : <><label className="field"><span>姓名或顯示名稱</span><input required autoComplete="name" maxLength={80} value={displayName} onChange={(event) => setDisplayName(event.target.value)} /></label><label className="field"><span>Email</span><input required type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} /></label></>}<label className="field"><span>密碼</span><input required type="password" autoComplete="new-password" minLength={8} maxLength={128} value={password} onChange={(event) => setPassword(event.target.value)} /></label><label className="field"><span>再次輸入密碼</span><input required type="password" autoComplete="new-password" minLength={8} maxLength={128} value={confirmation} onChange={(event) => setConfirmation(event.target.value)} /></label>{information?.published && <label className="check-field"><input type="checkbox" required checked={dataConsent} onChange={event => setDataConsent(event.target.checked)} /><span>我已閱讀<Link to="/privacy" target="_blank" rel="noopener">個資告知</Link>並同意<Link to="/terms" target="_blank" rel="noopener">服務條款</Link></span></label>}{error && <p className="form-error" role="alert">{error}</p>}<button className="button button-primary" disabled={busy}>{busy ? "建立中…" : "建立帳號"}</button></form>}
      {!result && <p className="auth-switch">已有帳號？<Link className="text-link" to="/login" search={{ return_to: returnTo }}>{returning ? "使用 Email 登入" : "直接登入"}</Link></p>}
    </div></div>
  </section>;
}
