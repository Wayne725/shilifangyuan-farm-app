import { ArrowLeft } from "@phosphor-icons/react";
import { Link, Navigate, useSearch } from "@tanstack/react-router";

import { useState } from "react";
import { lineLoginReturnTo } from "../lib/login-navigation";

import { LineLoginButton } from "../components/LineLogin";
import { LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import "../styles/auth-options.css";

export function LineConsentPage() {
  const { user, isAuthReady } = useAuth();
  const search = useSearch({ from: "/line-consent" });
  const returnTo = lineLoginReturnTo(search.return_to || "/shop");
  const [loginEntry, setLoginEntry] = useState<"nonmember" | "existing">();
  const loginUrl = `/login?return_to=${encodeURIComponent(returnTo)}`;
  if (!isAuthReady) return <section className="line-consent-page" aria-label="正在確認登入"><LoadingLines count={3} /></section>;
  if (user) return <Navigate from="/line-consent" href={returnTo} replace />;
  return <section className="line-consent-page">
    <a className="text-link" href={loginUrl}><ArrowLeft size={17} />返回帳號登入</a>
    <p className="eyebrow">LINE LOGIN / 資料使用說明</p>
    <h1>使用 LINE 登入</h1>
    <div className="line-membership-intent" role="group" aria-label="社員身分選擇">
      <Link to="/register" search={{ intent: "existing_member", return_to: returnTo, remember_me: search.remember_me !== false }}>我是社員</Link>
      <button className={loginEntry === "nonmember" ? "is-selected" : undefined} type="button" aria-pressed={loginEntry === "nonmember"} onClick={() => setLoginEntry("nonmember")}>我不是社員</button>
    </div>
    <button className="text-link line-existing-login" type="button" onClick={() => setLoginEntry("existing")}>已有網站帳號，直接登入</button>
    <section className="line-consent-details" aria-label="LINE登入資料使用說明">
      <h2>登入時會使用哪些資料</h2>
      <ul>
        <li>LINE 使用者識別碼：用來辨認同一個網站帳號並提供後續登入。</li>
        <li>LINE 顯示名稱：用來建立帳號及在網站顯示使用者名稱。</li>
        <li>訂單、取餐與社員資格仍由十里方圓網站管理；訂單或發票通知 Email 會在需要時另外詢問。</li>
      </ul>
      <p>LINE 授權只提供畫面列出的 LINE 資料，不會因此自動取得社員資格。取消 LINE 授權也不會自動刪除網站已建立的帳號或訂單資料。</p>
    </section>
    {loginEntry && <LineLoginButton intent="nonmember" rememberMe={search.remember_me !== false} returnTo={returnTo} consentLink={false} consentText="我已閱讀並同意十里方圓依上述用途使用及保存 LINE 登入資料。" />}
    <div className="line-consent-alternatives">
      <a className="text-link" href={loginUrl}>使用 Email 登入</a>
      <Link className="text-link" to="/register" search={{ intent: "existing_member", return_to: returnTo, remember_me: search.remember_me !== false }}>不確定是否是社員？</Link>
    </div>
  </section>;
}
