import { ArrowLeft, CreditCard, FileText, Storefront, UsersThree } from "@phosphor-icons/react";
import { Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useAuth } from "../context/AuthContext";
import { useAdminPanelAccess } from "../lib/admin-panel-access";
import { ApiError, apiFetch, captureSessionIdentityGuard } from "../lib/api";
import type { PickupLocation } from "../lib/types";
import "./HelpPages.css";

const helpItems = [
  { icon: FileText, title: "放大字體", body: "網站上方按「放大」，選擇會記在目前瀏覽器。需要恢復時按「標準」。" },
  { icon: Storefront, title: "如何訂便當", body: "從「我要訂餐」確認開放預訂的供餐日期與地點，直接點取餐時間，再選餐點與份數。取餐點沿用日常選品的取貨地點。確認取餐點、餐具需求、餐點、金額及發票資料後付款。付款完成後，可在「我的便當訂單」確認取餐時間，到場報上訂購者姓名。" },
  { icon: UsersThree, title: "共同購買投票", body: "投票只用來估計需求；正式開團後仍須加入並完成付款。" },
  { icon: CreditCard, title: "線上付款", body: "付款結果以訂單中心狀態為準；中斷時可由原訂單安全續接。" },
  { icon: FileText, title: "便當退單", body: "請在便當訂單按「電話聯絡退單」，向合作社提供訂單編號及退單原因，由管理員確認並辦理。聯絡後不代表訂單已取消；以原訂單顯示的訂單、退款及發票狀態為準。尚未設定電話時，頁面會顯示無法撥號的狀態。" },
  { icon: Storefront, title: "便當取餐時間", body: "一般供餐採工作日，週五、週六、週日可預訂下週一；遇國定假日順延至下一工作日。午餐可選 11:00、11:30、12:00、12:30、13:00；晚餐可選 16:30、17:00、17:30、18:00。場次實際可選時間以畫面上的可選時段為準，兩種餐別都到指定地點現場取餐。" },
  { icon: UsersThree, title: "入社資料", body: "入社申請不收身分證字號或證件。填寫基本資料及志願書、閱讀教材並完成測驗後，送交合作社審核。社員提案與各項會議分別從社務選單進入。" },
  { icon: FileText, title: "電子發票", body: "後端確認付款成功後排入開票；汎宇寄送發票通知，平台寄送付款成功通知，票號可在訂單紀錄查看。" },
];

interface HelpQuestion {
  question: string;
  answer: string;
}

const publicQuestions: HelpQuestion[] = [
  { question: "什麼是會餐？", answer: "會餐供需要以公司統編報帳的訂餐使用，所有登入使用者均可預訂，整張訂單至少 2 份，須填寫統一編號與公司名稱。會餐最晚於取餐前一天 20:00 截止，部分場次僅開放會餐。送單後先保留餐點並送出備餐單。可於取餐當日線上付款，或取餐時付現給工作人員。付款完成後即可領餐，系統會在確認收款後開立統編發票。當日未付款或發票處理異常，請聯絡合作社核對。" },
  { question: "不確定自己是否是社員，如何確認並登入？", answer: "在 LINE 登入頁按「不確定是否是社員？」，直接進入既有社員資料核對。首次使用網站者填寫名冊登記的電話號碼與 Email，再繼續 LINE 授權；核對成功後仍須完成原信箱的認領驗證，才會啟用社員資格，不另寄獨立查詢結果信。已有網站帳號者，按「曾用 LINE 登入，直接登入」，或使用 Email 登入，沿用原帳號及資格，不需重新認領。無法核對不一定代表不是社員，請聯絡管理員確認名冊資料。" },
  { question: "完整入社流程現在可以完成嗎？", answer: "目前開放基本資料與志願書、教材閱讀確認、10 題測驗及管理員審核。每題 10 分，80 分合格；合格只適用這一次申請，補件沿用同輪，撤回或駁回後重新申請須重新完成。管理員設定額外重考次數及間隔；尚未設定時僅能首次作答。審核同意後的繳費、銀行核帳及發證仍待合作社確認，暫不產生新款項或寄送繳費通知；測驗合格與審核同意都不代表已取得社員資格。既有社員的名冊認領與歷史收款查詢另依原功能處理。" },
  { question: "活動報名、候補與付款如何進行？", answer: "社員與非社員皆可報名公開永續活動。每場由管理員設定社員價、非社員價及付款時限。取得名額時保存當時身分與金額，待付款會保留名額；候補不付款，依順序遞補後才建立應收。付款預設須在取得名額後 24 小時內完成，可由管理員調整，最晚不超過報名截止。可從永續活動的「我的活動報名」繼續付款或查閱歷次紀錄；舊免費活動不追溯收費。" },
  { question: "活動付款逾時、取消與退款怎麼處理？", answer: "金流結果未知時會繼續核對，不立即釋出名額。已釋出名額才收到的晚到款不會搶回名額，會保留實收並交管理員審核退款。已付費取消先提出申請，核准前仍保留報名；管理員只能核准整筆退款，沒有部分退款。取消整場時，管理員須先核對人數及退款總額再明確核准。金流退款失敗可在活動收款明細追查。" },
  { question: "共同購買達標後何時確認成團？", answer: "正式共同購買目前採先線上付款、持續集單，付款截止後再由管理員審核成團。截止前即使已達原門檻，仍可在供應上限內加入；截止後不接受新加入、新付款或未付款訂單改量。截止前已進入金流的有效交易會先完成結算，有結果待確認的款項時不能成團。結算完成後由管理員審核，未達標也可勾選例外成團，但沒有有效付款的空團不能確認。審核時會核對當下付款數量與門檻版本，資料變更時須重新讀取確認。已確認的歷史共同購買保留原結果。" },
  { question: "下拉選單如何使用？", answer: "點選欄位後選擇項目；便當主食的加價或減價會列在選項中，選填項目可選「不選擇」。選擇不會直接付款或完成審核，請核對明細後再按付款或確認按鈕。" },
  { question: "非社員可以參加永續活動嗎？", answer: "可以。永續活動開放所有人瀏覽，登入一般帳號即可報名或取消自己的報名，不需要社員資格。額滿時依活動設定開放候補，名額釋出後依候補順序遞補。發起活動仍限有效社員，須經管理員審核後才會公開。" },
  { question: "訂單成立後還能修改數量嗎？", answer: "尚未付款且仍在可訂購時間內的訂單，可在訂單中心按「修改數量」，調整後先查看優惠、運費及總額，再按「確認修改」。一般商品、共同購買及便當都使用相同流程，系統會重新檢查庫存與限購；選擇「移除此項」可刪除單一品項，但至少保留一項。訂單編號與取貨資料保留，修改紀錄可從訂單明細查看。已開始製作、已出貨、已截止或其他狀態不允許時，請聯絡合作社。" },
  { question: "修改數量後要使用哪一個付款頁？", answer: "請從更新後的訂單重新按付款，使用最新金額的付款頁。原先開啟的付款頁不再代表目前訂單；若舊版本款項晚到，系統會另行處理該筆款項，退款狀態仍以金流回覆為準。修改遇到版本衝突時，草稿會保留，請按「重新核對訂單」並再次確認金額。" },
  { question: "已付款訂單如何修改數量？", answer: "請聯絡合作社，由管理員依付款及履約狀態辦理整單退單與退款，再重新下單。此流程不提供部分退款或補差額付款；現場付款須由合作社實際退還款項後確認。請核對原訂單的退款結果，不能以訂單取消等同款項已退還。" },
  { question: "如何查詢餐點與一般訂單？", answer: "在訂單中心選擇「餐點訂單」或「一般訂單」，可用訂單編號、餐點／商品名稱、取貨地點查詢，並依付款狀態篩選。一般訂單包含商品與正式共同購買；餐點訂單可查看取餐時間及退單進度。" },
  { question: "社員頭像如何更新？", answer: "在社員名錄的「編輯我的資料」選擇 JPG、PNG 或 WebP 照片後儲存。每張最多 5 MB、1200 萬像素，系統會縮圖並移除定位等中繼資料。社員頭像僅供登入的有效社員查看，選擇不公開時只有本人能查看。" },
  { question: "共同購買想買的東西不在清單內怎麼辦？", answer: "發起共同購買投票時，把項目類型選為「其他需求」，填寫需求名稱及內容後送審。管理員可核准開放投票或退回提案。達門檻後，由管理員確認供應並選擇正式開團的商品或套組；原需求內容會保留。投票不會直接產生訂單或收款。" },
  { question: "便當可以到合作社現場付款嗎？", answer: "場次開放現場付款時，選合作社現場付款即可先保留餐點。到場依合作社提供的現金、POS 或現場 QR Code 方式付款；工作人員核對實收後，後台標記已付款。發票由現場 POS 處理，App 不會重複開立。現場 QR Code 是合作社既有收款方式，系統不會自動向它查帳。" },
  { question: "一般商品如何選擇取貨方式與地點？", answer: "結帳時從「取貨方式」與「取貨地點」下拉選單選擇，再核對訂單摘要的取貨地點。合作社取貨不另計運費，取貨時間另行通知。若開放宅配或超商取貨，建立訂單後會前往綠界選擇門市或確認宅配；中途離開時，可從訂單中心繼續完成付款或物流選擇。" },
  { question: "既有社員如何建立網站帳號？", answer: "選「註冊帳號」與「我是既有社員」，填入名冊上的電話號碼及 Email，繼續使用 LINE。顯示名稱直接使用 LINE 名稱。電話與信箱核對成功後，仍須輸入原信箱收到的驗證碼，才會取得正式社員資格。已有帳號請直接登入，避免重複建帳。" },
  { question: "平台暫停接單時，原訂單還能使用嗎？", answer: "暫停只阻止新訂單；既有訂單查詢、允許範圍內的付款、取餐及後台作業仍可使用。管理員可於「平台設定」切換接單開關。" },
  { question: "便當優惠碼與滿額折扣如何使用？", answer: "便當結帳可套用優惠碼；沒有使用優惠碼時，系統選擇符合門檻的最高自動折抵，兩者不疊加。活動依台灣時間、消費門檻、總使用次數及每人限制判斷。訂單建立就占用一次優惠名額，取消後不返還，請先確認再送出。一般商品與共同購買目前不適用便當促銷。" },
  { question: "如何查看合作點數與已獲徽章？", answer: "在帳號中心點「合作點數」或「已獲徽章」即可開啟明細。點數可按訂單編號、原因或台灣日期查詢，並區分一般訂單、便當訂單與其他紀錄；查詢不會改變目前總點數。消費累積及退款扣回可從已連結的紀錄開啟原訂單，退款紀錄連回原訂單，不會建立新訂單。管理員調整等沒有可靠訂單連結的紀錄仍保留原因。徽章明細顯示獲得條件及日期；目前沒有點數折抵購物功能。" },
  { question: "社員點數如何累積？", answer: "正式社員每筆確認付款消費滿 100 元累積 1 點，按折抵優惠後的實付金額計算；退款依原訂單扣回。帳號中心可看點數，管理員可在「社員帳冊」查詢與匯出。" },
  { question: "還沒有網站帳號，可以用 LINE 註冊嗎？", answer: "可以。按「帳號登入」再選「使用LINE登入」，在專頁選「我不是社員」、閱讀並同意資料使用說明後授權，系統會自動建立顧客帳號並回到原頁，不需填 Email 或建立密碼。訂單與發票通知信箱到結帳時再填。已有網站帳號請先登入原帳號，再連結 LINE，沿用訂單與社員資格。" },
  { question: "我是社員／我不是社員有什麼差別？", answer: "「我是社員」會進入既有社員註冊，先填電話號碼及名冊 Email，再使用 LINE 驗證；通過名冊核對及原信箱驗證後才會啟用社員資格。「我不是社員」繼續一般 LINE 登入／註冊。已建立網站帳號的人，請選「已有網站帳號，直接登入」，沿用原帳號及實際資格，不需重新認領或註冊。" },
  { question: "如何使用 LINE 登入？", answer: "開啟帳號登入頁，按下方「使用LINE登入」，既有帳號選「已有網站帳號，直接登入」；首次使用選「我不是社員」，或選「我是社員」填電話號碼與 Email，再繼續 LINE 授權。閱讀並同意資料使用說明後，依 LINE 畫面授權。已有 Email 帳號時，先登入原帳號，到帳號中心按「連結LINE」，之後才能用 LINE 沿用原訂單與社員資格。勾選「保持登入」可保留登入狀態，也會記住 Email 登入時填寫的信箱。" },
  { question: "今天可以訂今天的餐嗎？", answer: "便當預訂頁顯示今天有可訂時段時，可訂一般餐點並線上付款。一般供餐依工作日安排，週五至週日可預訂週一。週一放假時，供餐順延至下一個工作日。" },
  { question: "最晚什麼時候要訂？", answer: "一般場次及會餐最晚於取餐前一天 20:00 截止，部分場次會提早。開放當日訂餐的場次，選好取餐時段後，請依畫面顯示的截止時間完成線上付款。" },
  { question: "午餐、晚餐的操作一樣嗎？", answer: "一樣，餐點相同。直接選 11:00～13:00 或 16:30～18:00 的半小時取餐時段，再選餐點與份數，確認明細後付款。" },
  { question: "離開頁面後，餐點還在嗎？", answer: "同一個瀏覽器分頁返回時，會保留選餐、份數與取餐時間。若庫存、菜單或可預訂日期改變，系統會調整並提醒你重新確認。訂單成立後，這筆選餐草稿會清除。" },
  { question: "忘記訂單編號怎麼辦？", answer: "登入後開啟「我的便當訂單」，選擇對應取餐日期的訂單。訂單編號在明細上方。到場可報訂購者姓名。" },
  { question: "付款頁中斷，要重新下一單嗎？", answer: "先回到原訂單確認付款狀態。仍可付款時，使用原訂單的「前往付款」；不要因為付款頁中斷或發票尚未開立就再下一單。" },
  { question: "我要取消訂單，會立即退款嗎？", answer: "不會。便當、日常選品與共同購買訂單都請使用「電話聯絡退單」，提供訂單編號並向合作社詢問，由管理員確認後辦理。退款與發票進度分別更新；線上退款的實際入帳時間依付款通路而定，現場付款須由合作社實際退還後確認。" },
  { question: "取餐時間可以直接修改嗎？", answer: "目前訂單成立後沒有自行修改取餐時間的功能，需要調整請聯絡管理員；請勿自行重複下單。" },
  { question: "為什麼看不到取消按鈕？", answer: "消費者無法自行取消或送出退單申請，請透過訂單內的「電話聯絡退單」向合作社詢問。既有退單申請仍會顯示審核進度；未核准而仍需協助時，也請電話聯絡。" },
  { question: "電子發票中獎後如何領獎？", answer: "中獎後請至全家 FamiPort 列印領獎；本平台不提供 7-ELEVEN 列印流程。" },
  { question: "便當發票何時開立？", answer: "一般便當線上付款成功後，由系統開立發票。需要取餐日的公司統編發票，請選「會餐（統編報帳）」，於取餐當日線上付款或取餐時付現。系統在確認收款後開票，發票日期以實際開立日為準。" },
  { question: "發票通知會寄到哪裡？", answer: "訂單與發票通知寄到結帳填寫的 Email。個人電子發票不寄送紙本；選擇「Email 會員載具」時，此信箱也作為會員載具識別。公司統編發票的統編與抬頭依付款時填寫的內容開立，請在送出前確認。現場手動開票若選手機條碼載具且未填 Email，發票交付至手機載具，不寄送 Email 通知；填寫通知 Email 時由供應商依發票交付設定通知。" },
  { question: "手機條碼載具要如何填寫？", answer: "選擇「手機條碼載具」後，填入以 / 開頭、後接 7 碼英數符號的手機條碼。條碼預設只用於本次訂單；勾選「結帳時儲存此手機條碼供下次使用」並送出結帳後，驗證成功才會存到目前帳號，下次購買自動帶入。取消勾選不會刪除既有載具；可按「移除已儲存載具」清除帳號中的紀錄，當次訂單仍可使用已填的條碼。" },
  { question: "為什麼顯示預估合計？", answer: "資料尚未填妥或系統尚未確認金額時，畫面會顯示預估合計。完成取餐時間、餐點必選項目與發票資料後，系統會確認實際金額。" },
  { question: "發票還沒出來，可以取餐嗎？", answer: "付款成功後可依預約時間取餐。發票會另外處理，尚未開立或顯示待處理時，請勿為了發票重新付款。" },
  { question: "我是社員，為什麼還顯示一般會員？", answer: "社員資格須由合作社確認。若已線下繳費，請聯絡管理員核對並更新身分；畫面上自行選擇社員並不代表資格已生效。" },
  { question: "取餐時間過了怎麼辦？", answer: "請聯絡現場人員。請提供訂單編號與訂購者姓名，讓現場人員查詢。" },
];

export function HelpPage() {
  const { user, sessionScope } = useAuth();
  return <section className="help-page meal-help-page">
    <Link className="text-link" to="/"><ArrowLeft size={17} />回到首頁</Link>
    <header><h1>操作說明與常見問題</h1></header>
    <div className="help-list">{helpItems.map(({ icon: Icon, title, body }, index) => <article key={title}><span>{String(index + 1).padStart(2, "0")}</span><Icon size={24} weight="light" /><div><h2>{title}</h2><p>{body}</p></div></article>)}</div>
    <section className="meal-faq" aria-label="便當常見問題">
      <h2>常見問題</h2>
      <PickupLocationHelp />
      <HelpQuestions questions={publicQuestions} />
    </section>
    {user?.user_role === "admin" && <AdminHelpQuestions key={`${user.id}:${sessionScope}`} adminId={user.id} />}
  </section>;
}

function HelpQuestions({ questions }: { questions: HelpQuestion[] }) {
  return questions.map(({ question, answer }) => <details key={question}><summary>{question}</summary><p>{answer}</p></details>);
}

const adminHelpQueryKeys = [["admin-help"]] as const;

function AdminHelpQuestions({ adminId }: { adminId: string }) {
  const [sameIdentity] = useState(captureSessionIdentityGuard);
  const access = useAdminPanelAccess(adminHelpQueryKeys);
  const questions = useQuery({
    queryKey: ["admin-help", adminId],
    queryFn: ({ signal }) => {
      if (!sameIdentity()) throw new ApiError(401, "請重新登入管理員帳號。");
      return access.request<HelpQuestion[]>("/v1/admin/help", { signal });
    },
    enabled: !access.denied,
    staleTime: 0,
    retry: false,
    gcTime: 0,
  });

  if (access.denied || !sameIdentity()) return null;
  return <section className="meal-faq" aria-labelledby="admin-help-heading">
    <h2 id="admin-help-heading">管理員常見問題</h2>
    {questions.isPending ? <p role="status">讀取管理員問答中…</p> : questions.isError ? <div>
      <p role="alert">管理員問答暫時無法讀取。</p>
      <button type="button" className="button button-quiet" disabled={questions.isFetching} onClick={() => void questions.refetch()}>重新讀取</button>
    </div> : <HelpQuestions questions={questions.data} />}
  </section>;
}

function PickupLocationHelp() {
  const [open, setOpen] = useState(false);
  const locations = useQuery({
    queryKey: ["pickup-locations"],
    queryFn: () => apiFetch<PickupLocation[]>("/v1/pickup-locations"),
    enabled: open,
    retry: false,
  });
  return <details onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>各取貨點有哪些領取說明？</summary>
    {open && (locations.isPending ? <p role="status">讀取取貨點中…</p> : locations.isError ? <div><p role="alert">取貨點說明暫時無法讀取。</p><button type="button" className="button button-quiet" disabled={locations.isFetching} onClick={() => void locations.refetch()}>重新讀取</button></div> : locations.data.length === 0 ? <p>目前沒有可用的取貨點。</p> : <div className="pickup-location-help-list">{locations.data.map((location) => <section key={location.id}><h3>{location.name}</h3>{location.address && <p>{location.address}</p>}{location.instructions && <p className="pickup-location-help-instructions">{location.instructions}</p>}</section>)}</div>)}
  </details>;
}

export function NotFoundPage() {
  return <section className="not-found-page"><span>404</span><p className="eyebrow">PAGE NOT FOUND / 找不到頁面</p><h1>這一頁不存在。</h1><p>網址可能已更新，或內容已經下架。</p><Link className="button button-primary" to="/">回到首頁</Link></section>;
}
