import { ArrowLeft } from "@phosphor-icons/react";
import { Link, useSearch } from "@tanstack/react-router";

import { useState } from "react";
import { loginReturnTo } from "../lib/login-navigation";

import { LineLoginButton } from "../components/LineLogin";
import { LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import "../styles/auth-options.css";

export function LineConsentPage() {
  const { user, isAuthReady } = useAuth();
  const search = useSearch({ from: "/line-consent" });
  const returnTo = loginReturnTo(search.return_to || "/shop");
  const [intent, setIntent] = useState<"member" | "nonmember">("nonmember");
  const loginUrl = `/login?return_to=${encodeURIComponent(returnTo)}`;
  if (!isAuthReady) return <section className="line-consent-page"><LoadingLines count={3} /></section>;
  return <section className="line-consent-page">
    <a className="text-link" href={loginUrl}><ArrowLeft size={17} />返回帳號登入</a>
    <p className="eyebrow">LINE LOGIN / 資料使用說明</p>
    <h1>使用 LINE 登入</h1>
    {!user && <div className="line-membership-intent" role="group" aria-label="社員身分選擇">
      <button className={intent === "member" ? "is-selected" : undefined} type="button" aria-pressed={intent === "member"} onClick={() => setIntent("member")}>我是社員</button>
      <button className={intent === "nonmember" ? "is-selected" : undefined} type="button" aria-pressed={intent === "nonmember"} onClick={() => setIntent("nonmember")}>我不是社員</button>
    </div>}
    <section className="line-consent-details" aria-label="LINE登入資料使用說明">
      <h2>登入時會使用哪些資料</h2>
      <ul>
        <li>LINE 使用者識別碼：用來辨認同一個網站帳號並提供後續登入。</li>
        <li>LINE 顯示名稱：用來建立帳號及在網站顯示使用者名稱。</li>
        <li>訂單、取餐與社員資格仍由十里方圓網站管理；訂單或發票通知 Email 會在需要時另外詢問。</li>
      </ul>
      <p>LINE 授權只提供畫面列出的 LINE 資料，不會因此自動取得社員資格。取消 LINE 授權也不會自動刪除網站已建立的帳號或訂單資料。</p>
    </section>
    {user ? <div className="line-consent-signed-in"><p>你已登入，可以直接開始訂餐。</p><div className="line-consent-actions"><a className="button button-primary" href={returnTo}>{returnTo === "/shop" ? "開始訂餐" : "繼續操作"}</a><Link className="text-link" to="/account">管理 LINE 連結</Link></div></div>
      : <LineLoginButton intent={intent} rememberMe={search.remember_me !== false} returnTo={returnTo} consentLink={false} consentText="我已閱讀並同意十里方圓依上述用途使用及保存 LINE 登入資料。" />}
    {!user && <a className="text-link line-consent-email" href={loginUrl}>使用 Email 登入</a>}
  </section>;
}
