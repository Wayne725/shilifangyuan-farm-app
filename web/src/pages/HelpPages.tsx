import { ArrowLeft, CreditCard, FileText, Storefront, UsersThree } from "@phosphor-icons/react";
import { Link } from "@tanstack/react-router";

const helpItems = [
  { icon: FileText, title: "放大字體", body: "網站上方「字體大小」可選「放大」，選擇會記在目前瀏覽器。需要恢復時按「標準」。" },
  { icon: Storefront, title: "如何訂便當", body: "從「我要訂餐」確認明天的日期與地點，直接點取餐時間，再選餐點與份數。確認地點、餐點、金額及發票資料後付款。付款完成後，回到「我的便當訂單」查看取餐碼。" },
  { icon: UsersThree, title: "團購投票", body: "投票只用來估計需求；正式開團後仍須加入並完成付款。" },
  { icon: CreditCard, title: "線上付款", body: "付款結果以訂單中心狀態為準；中斷時可由原訂單安全續接。" },
  { icon: FileText, title: "便當退單", body: "在便當訂單內選擇「申請取消這筆便當」並填寫原因，由管理員核准或拒絕。審核前訂單仍有效；核准後取消訂單，已付款會交由原付款通路退款。退款及發票狀態會分別更新，請回到原訂單查看。已取餐或供餐結束的訂單請洽管理員處理。" },
  { icon: Storefront, title: "便當取餐時間", body: "只能提前一天預訂，今天可訂明天。午餐可選 11:00、11:30、12:00、12:30、13:00；晚餐可選 16:30、17:00、17:30、18:00。場次實際可選時間以畫面上的按鈕為準，兩種餐別都到指定地點現場取餐。" },
  { icon: UsersThree, title: "入社資料", body: "合作社已確認不需上傳身分證或其他證件；填妥基本資料即可送出入社申請。社員提案與社員會議分別從社務選單進入。" },
  { icon: FileText, title: "電子發票", body: "後端確認付款成功後排入開票；汎宇寄送發票通知，平台寄送付款成功通知，票號可在訂單紀錄查看。" },
];

const mealQuestions = [
  { question: "今天可以訂今天的餐嗎？", answer: "不可以。只能提前一天預訂，以台灣日期為準；今天可訂明天的餐點，也不能提前兩天以上下單。" },
  { question: "最晚什麼時候要訂？", answer: "請看該場次的「預訂截止」。最晚須在取餐日前一天結束前送出，管理員也可能設定較早截單。超過截止時間就無法建立新訂單。" },
  { question: "午餐、晚餐的操作一樣嗎？", answer: "一樣，餐點相同。直接選 11:00～13:00 或 16:30～18:00 的半小時取餐時段，再選餐點與份數，確認明細後付款。" },
  { question: "離開頁面後，餐點還在嗎？", answer: "同一個瀏覽器分頁返回時，會保留選餐、份數與取餐時間。若庫存、菜單或可預訂日期改變，系統會調整並提醒你重新確認。訂單成立後，這筆選餐草稿會清除。" },
  { question: "忘記訂單編號或取餐碼怎麼辦？", answer: "登入後開啟「我的便當訂單」，選擇對應取餐日期的訂單。訂單編號在明細上方；付款成功後可看到 QR 與六位取餐碼。" },
  { question: "付款頁中斷，要重新下一單嗎？", answer: "先回到原訂單確認付款狀態。仍可付款時，使用原訂單的「前往付款」；不要因為付款頁中斷或發票尚未開立就再下一單。" },
  { question: "我要取消訂單，會立即退款嗎？", answer: "不會。請在訂單明細按「申請取消這筆便當」，填原因並送出，由管理員核准或拒絕。核准前訂單仍有效；核准後，退款和發票進度會分別更新，實際入帳時間依付款通路而定。" },
  { question: "取餐時間可以直接修改嗎？", answer: "目前訂單成立後沒有自行修改取餐時間的功能，需要調整請聯絡管理員；請勿自行重複下單。" },
  { question: "為什麼看不到取消按鈕？", answer: "待審核、已取消、已取餐或供餐結束等狀態可能不提供申請入口。請先查看目前訂單與退單申請狀態，需要例外處理時洽管理員。" },
  { question: "發票還沒出來，可以取餐嗎？", answer: "付款成功且取餐碼有效就可依預約取餐。發票會另外處理，尚未開立或顯示待處理時，請勿為了發票重新付款。" },
  { question: "我是社員，為什麼還顯示一般會員？", answer: "社員資格須由合作社確認。若已線下繳費，請聯絡管理員核對並更新身分；畫面上自行選擇社員並不代表資格已生效。" },
  { question: "取餐時間過了怎麼辦？", answer: "請聯絡現場人員。取餐碼會依供餐時間與訂單狀態決定是否仍有效，請不要使用其他訂單的取餐碼或再付款。" },
];

export function HelpPage() {
  return <section className="help-page meal-help-page"><Link className="text-link" to="/shop"><ArrowLeft size={17} />回到便當預訂</Link><header><h1>操作說明與常見問題</h1><p>從訂餐到取餐，依照下方步驟操作。</p></header><nav className="meal-help-actions" aria-label="常用入口"><Link className="button button-primary" to="/shop">我要訂餐</Link><Link className="button button-quiet" to="/meal-orders">我的便當訂單</Link></nav><div className="help-list">{helpItems.map(({ icon: Icon, title, body }, index) => <article key={title}><span>{String(index + 1).padStart(2, "0")}</span><Icon size={24} weight="light" /><div><h2>{title}</h2><p>{body}</p></div></article>)}</div><section className="meal-faq" aria-label="便當常見問題"><h2>常見問題</h2>{mealQuestions.map(({ question, answer }) => <details key={question}><summary>{question}</summary><p>{answer}</p></details>)}</section></section>;
}

export function NotFoundPage() {
  return <section className="not-found-page"><span>404</span><p className="eyebrow">找不到頁面</p><h1>這一頁不存在。</h1><p>網址可能已更新，或內容已經下架。</p><Link className="button button-primary" to="/">回到首頁</Link></section>;
}
