import { ArrowLeft } from "@phosphor-icons/react";
import { Link } from "@tanstack/react-router";

import { LineLoginButton } from "../components/LineLogin";
import { LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import "../styles/auth-options.css";

export function LineConsentPage() {
  const { user, isAuthReady } = useAuth();
  if (!isAuthReady) return <section className="line-consent-page"><LoadingLines count={3} /></section>;
  return <section className="line-consent-page">
    <Link className="text-link" to="/"><ArrowLeft size={17} />回到首頁</Link>
    <p className="eyebrow">LINE LOGIN / 資料使用說明</p>
    <h1>使用 LINE 登入</h1>
    <p className="line-consent-lead">從十里方圓官方帳號進入後，閱讀以下資料用途，再按 LINE 登入並於 LINE 畫面授權。</p>
    <section className="line-consent-details" aria-label="LINE登入資料使用說明">
      <h2>登入時會使用哪些資料</h2>
      <ul>
        <li>LINE 使用者識別碼：用來辨認同一個網站帳號並提供後續登入。</li>
        <li>LINE 顯示名稱：用來建立帳號及在網站顯示使用者名稱。</li>
        <li>訂單、取餐與社員資格仍由十里方圓網站管理；訂單或發票通知 Email 會在需要時另外詢問。</li>
      </ul>
      <p>LINE 授權只提供畫面列出的 LINE 資料，不會因此自動取得社員資格。取消 LINE 授權也不會自動刪除網站已建立的帳號或訂單資料。</p>
    </section>
    {user ? <div className="line-consent-signed-in"><p>你已登入，可以直接開始訂餐。</p><div className="line-consent-actions"><Link className="button button-primary" to="/shop">開始訂餐</Link><Link className="text-link" to="/account">管理 LINE 連結</Link></div></div>
      : <LineLoginButton returnTo="/shop" consentLink={false} consentText="我已閱讀並同意十里方圓依上述用途使用及保存 LINE 登入資料。" />}
    {!user && <><p className="line-consent-note">不同意資料使用說明時，可返回網站使用 Email 帳號登入。</p><a className="text-link" href="/login?return_to=%2Fshop">使用 Email 登入</a></>}
  </section>;
}
