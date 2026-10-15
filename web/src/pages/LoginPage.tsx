import { ArrowLeft } from "@phosphor-icons/react";
import { useSearch } from "@tanstack/react-router";
import { useEffect, useState, type FormEvent } from "react";

import { LineLoginButton } from "../components/LineLogin";
import { LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import { loginReturnTo } from "../lib/login-navigation";
import { rememberedLoginEmail, rememberLoginEmail } from "../lib/login-preferences";
import "../styles/auth-options.css";

export function LoginPage() {
  const search = useSearch({ from: "/login" });
  const returnTo = loginReturnTo(search.return_to);
  const { user, isAuthReady, login } = useAuth();
  const [email, setEmail] = useState(rememberedLoginEmail);
  const [rememberMe, setRememberMe] = useState(true);
  const [lineIntent, setLineIntent] = useState<"member" | "nonmember">("nonmember");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { if (isAuthReady && user) window.location.replace(returnTo); }, [isAuthReady, user, returnTo]);
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true); setError("");
    try { await login(email, String(form.get("password")), rememberMe); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "登入失敗"); setBusy(false); }
  };
  if (!isAuthReady || user) return <section className="account-login-page" aria-label="正在確認登入"><LoadingLines count={3} /></section>;
  return <section className="account-login-page">
    <a className="text-link" href={returnTo}><ArrowLeft size={17} />返回上一頁</a>
    <header><p className="eyebrow">ACCOUNT / 帳號</p><h1>帳號登入</h1></header>
    <form className="login-form" onSubmit={submit}>
      <label>電子信箱<input name="email" type="email" autoComplete="username" required value={email} onChange={(event) => setEmail(event.target.value)} /></label>
      <label>密碼<input name="password" type="password" autoComplete="current-password" required /></label>
      <label className="login-preference"><input type="checkbox" checked={rememberMe} onChange={(event) => { setRememberMe(event.target.checked); if (!event.target.checked) rememberLoginEmail("", false); }} /><span>保持登入</span></label>
      {error && <p className="form-error" role="alert">{error}</p>}
      <button className="button button-primary" disabled={busy}>{busy ? "登入中…" : "登入系統"}</button>
    </form>
    <div className="login-links"><a href="/forgot-password">忘記密碼</a><a href="/register">註冊帳號</a></div>
    <div className="login-secondary">
      <div className="line-membership-intent" role="group" aria-label="社員身分選擇"><button className={lineIntent === "member" ? "is-selected" : undefined} type="button" aria-pressed={lineIntent === "member"} onClick={() => setLineIntent("member")}>我是社員</button><button className={lineIntent === "nonmember" ? "is-selected" : undefined} type="button" aria-pressed={lineIntent === "nonmember"} onClick={() => setLineIntent("nonmember")}>我不是社員</button></div>
      <LineLoginButton intent={lineIntent} rememberMe={rememberMe} returnTo={returnTo} />
    </div>
    <p className="dialog-footnote"><a href="/verify-email">輸入或重寄驗證碼</a></p>
  </section>;
}
