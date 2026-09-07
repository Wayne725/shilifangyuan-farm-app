import { ArrowLeft, CreditCard, FileText, Storefront, UsersThree } from "@phosphor-icons/react";
import { Link } from "@tanstack/react-router";

const helpItems = [
  { icon: UsersThree, title: "團購投票", body: "投票只用來估計需求；正式開團後仍須加入並完成付款。" },
  { icon: CreditCard, title: "線上付款", body: "付款結果以訂單中心狀態為準；中斷時可由原訂單安全續接。" },
  { icon: Storefront, title: "合作社取貨", body: "一般商品可選擇合作社取貨點；便當依場次與指定地點現場取餐。" },
  { icon: FileText, title: "電子發票", body: "後端確認付款成功後排入開票；汎宇寄送發票通知，平台寄送付款成功通知，票號可在訂單紀錄查看。" },
];

export function HelpPage() {
  return <section className="help-page"><Link className="text-link" to="/"><ArrowLeft size={17} />返回首頁</Link><header><p className="eyebrow">SERVICE GUIDE</p><h1>使用說明</h1></header><div className="help-list">{helpItems.map(({ icon: Icon, title, body }, index) => <article key={title}><span>{String(index + 1).padStart(2, "0")}</span><Icon size={24} weight="light" /><div><h2>{title}</h2><p>{body}</p></div></article>)}</div></section>;
}

export function NotFoundPage() {
  return <section className="not-found-page"><span>404</span><p className="eyebrow">PAGE NOT FOUND</p><h1>這一頁不存在。</h1><p>網址可能已更新，或內容已經下架。</p><Link className="button button-primary" to="/">回到首頁</Link></section>;
}
