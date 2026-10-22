import { ArrowLeft, ArrowRight, HandHeart, ShoppingBag } from "@phosphor-icons/react";
import { Link, Navigate, useSearch } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { apiFetch } from "../lib/api";
import { loginReturnTo } from "../lib/login-navigation";
import { LineLoginButton } from "../components/LineLogin";
import type { AuthActionResponse } from "../lib/types";
import "../styles/auth-options.css";

type Intent = "existing_member" | "nonmember";
export function RegisterPage() {
  const search = useSearch({ from: "/register" });
  const { user, openLogin } = useAuth();
  const returnTo = loginReturnTo(search.return_to || "/shop");
  const [intent, setIntent] = useState<Intent | undefined>(search.intent);
  const [step, setStep] = useState<"identity" | "method" | "email" | "line">(search.intent === "nonmember" ? "method" : "identity");
  const [legalName, setLegalName] = useState("");
  const [memberNumber, setMemberNumber] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<AuthActionResponse | null>(null);
  if (user) return <Navigate to="/account" replace />;
  const member = intent === "existing_member";
  const lineClaim = { phone: phone.trim(), email: email.trim() };
  const claim = { member_number: memberNumber.trim(), legal_name: legalName.trim(), email: email.trim() };
  const selectIntent = (value: Intent) => { setIntent(value); setStep(value === "existing_member" ? "identity" : "method"); setError(""); };
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setError("");
    if (password !== confirmation) { setError("兩次輸入的密碼不一致"); return; }
    setBusy(true);
    try {
      setResult(await apiFetch<AuthActionResponse>(member ? "/v1/auth/register-existing-member" : "/v1/auth/register", { method: "POST", body: JSON.stringify(member ? { ...claim, display_name: claim.legal_name, password } : { display_name: displayName.trim(), email: email.trim(), password }) }));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "無法完成註冊"); }
    finally { setBusy(false); }
  };
  const title = !intent ? "你是否為既有社員？" : step === "identity" ? "社員資料驗證" : step === "method" ? "選擇註冊方式" : step === "line" ? "使用 LINE 註冊" : "使用 Email 註冊";
  return <section className="auth-page registration-flow">
    <aside className="auth-page-intro"><Link className="auth-back-link" to="/">十里方圓合作社</Link><div><p className="eyebrow">CREATE ACCOUNT / 建立帳號</p><h1>註冊帳號</h1></div></aside>
    <div className="auth-page-body"><div className="auth-card">
      {intent && !result && <button className="registration-back" type="button" onClick={() => { setError(""); if (step === "identity" || step === "method" && !member) setIntent(undefined); else setStep(member ? "identity" : "method"); }}><ArrowLeft size={17} />上一步</button>}
      <p className="eyebrow">{!intent ? "MEMBERSHIP / 社員身分" : member ? "EXISTING MEMBER / 既有社員" : "CUSTOMER / 非社員"}</p>
      <h2>{result ? "請驗證你的 Email" : title}</h2>
      {result ? <div className="auth-result"><p>{result.message}</p>{result.development_token && <p>本機測試碼：{result.development_token}</p>}<Link className="button button-primary" to="/verify-email" search={{ email, token: result.development_token, intent }}>輸入驗證碼<ArrowRight size={17} /></Link></div>
        : !intent ? <div className="registration-choice-grid"><button className="registration-choice-card member" type="button" onClick={() => selectIntent("existing_member")}><HandHeart size={28} /><h3>我是既有社員</h3></button><button className="registration-choice-card customer" type="button" onClick={() => selectIntent("nonmember")}><ShoppingBag size={28} /><h3>我不是社員</h3></button></div>
        : step === "identity" ? <form className="auth-form" onSubmit={(event) => { event.preventDefault(); setStep("line"); }}><label className="field"><span>電話號碼</span><input required type="tel" autoComplete="tel" minLength={8} maxLength={24} value={phone} onChange={(event) => setPhone(event.target.value)} /></label><label className="field"><span>Email</span><input required type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} /></label><button className="button button-primary" disabled={phone.replace(/\D/g, "").length < 8 || !email.trim()}>繼續使用 LINE<ArrowRight size={17} /></button><button className="button button-quiet" type="button" onClick={() => setStep("email")}>使用 Email 註冊</button></form>
        : step === "method" ? <div className="registration-methods"><button className="button button-primary" type="button" onClick={() => setStep("line")}>使用 LINE 註冊</button><button className="button button-quiet" type="button" onClick={() => setStep("email")}>使用 Email 註冊</button></div>
        : step === "line" ? <><section className="line-consent-details"><h3>LINE 資料使用說明</h3><p>使用 LINE 識別碼及顯示名稱建立網站帳號。{member ? "電話號碼與 Email 用於核對社員名冊；顯示名稱使用 LINE 名稱，完成原信箱驗證後啟用資格。" : "註冊後建立非社員帳號，通知信箱會於需要時填寫。"}</p><Link to="/line-consent">閱讀完整資料使用說明</Link></section><LineLoginButton intent={member ? "member" : "nonmember"} returnTo={returnTo} rememberMe={search.remember_me !== false} memberClaim={member ? lineClaim : undefined} consentLink={false} consentText="我已閱讀並同意 LINE 登入及社員資料的使用說明。" /></>
        : <form className="auth-form" onSubmit={submit}>{member ? <><label className="field"><span>姓名</span><input required autoComplete="name" maxLength={80} value={legalName} onChange={(event) => setLegalName(event.target.value)} /></label><label className="field"><span>社員編號</span><input required maxLength={32} value={memberNumber} onChange={(event) => setMemberNumber(event.target.value)} /></label><label className="field"><span>Email</span><input required type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} /></label></> : <><label className="field"><span>姓名或顯示名稱</span><input required autoComplete="name" maxLength={80} value={displayName} onChange={(event) => setDisplayName(event.target.value)} /></label><label className="field"><span>Email</span><input required type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} /></label></>}<label className="field"><span>密碼</span><input required type="password" autoComplete="new-password" minLength={8} maxLength={128} value={password} onChange={(event) => setPassword(event.target.value)} /></label><label className="field"><span>再次輸入密碼</span><input required type="password" autoComplete="new-password" minLength={8} maxLength={128} value={confirmation} onChange={(event) => setConfirmation(event.target.value)} /></label>{error && <p className="form-error" role="alert">{error}</p>}<button className="button button-primary" disabled={busy}>{busy ? "建立中…" : "建立帳號"}</button></form>}
      {!result && <p className="auth-switch">已有帳號？<button type="button" className="text-link" onClick={openLogin}>直接登入</button></p>}
    </div></div>
  </section>;
}
