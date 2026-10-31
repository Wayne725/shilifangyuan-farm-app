# 舊站與歷史 App 退場紀錄

日期：2026-10-31（Asia/Taipei）  
本次查核基準：`a50ed8149fda3c2e0ae24fbccf5cf1d0b3237e5b`。本文件只記錄來源、保留條件及退場檢查，不執行刪除、關站、轉址或資料搬移。

## 三類網站來源

| 來源 | 網址與程式 | 本次判定 |
|---|---|---|
| 現行交易網站 | `https://shilifangyuan.com`；Render預設入口 `https://shilifangyuan-web.onrender.com`；API `https://shilifangyuan-api.onrender.com`。程式為本repo的 `web/`＋`backend/` | 正式交易系統。Render預設網址可能是同一服務的另一入口，不因網址不同就當成另一套舊App。依實際部署版本核對，保留登入、下單、付款返回、LINE與取貨功能。 |
| Lovable早期概念網站 | `https://ten-square-miles-coop.lovable.app`；本機設計來源 `/Users/liuweiwei/Downloads/--main` | 本次公開讀取仍為HTTP200，畫面與正式站不同。下載的來源含另一套TanStack Start／Supabase架構，沒有嵌入目前farm Web。停用本機資料夾不會關閉線上站；線上退場需另確認所有權及資料。 |
| 合作社內容與教材網站 | `https://www.coop4sustainability.live`，包含 `/entrence-intro` 等內容 | 本次公開讀取仍為HTTP200，使用Wix內容系統。它是合作社介紹／教材來源，不能因不是farm Web就一併當舊App刪除。先核對教材、公告及外部連結是否仍使用。 |

線上讀取結果與Render部署狀態由本輪主審查紀錄保存。本機檔案大小或舊README的Live App字樣，不能證明線上站是否已關閉，也不能證明另一套資料庫沒有真實資料。

## 歷史 Expo 來源

原根目錄 `app/`、`src/`、`app.json`、`tsconfig.json` 屬Expo／React Native版本。它們已於 `33abeaafa80193a120a51b196029f79c88e74ff4` 遷移Vite時從正式樹移除，現行 `package.json`、workspace、CI與Render建置均只使用 `web/`。

舊原生scheme、Expo Router與原生套件不存在於本次基準樹。Git歷史仍可還原舊程式，但不會因歷史存在就被打包進目前網站。`backend/app/` 是現在的FastAPI程式，不是這裡所指的舊根目錄App，必須保留。

本機 `main`／`origin/main` 仍指向較早Expo版本，remote HEAD也指main；若從預設分支另開新工作目錄，可能再次得到舊App。後續應先核對GitHub預設分支、CI及Render的實際來源，再決定整理方式；本次不改分支、refs或服務設定。

## 退場順序

1. **先盤資料與入口**：確認各站管理權、使用中的資料庫／儲存、是否留有會員、訂單、付款、申請與同意紀錄。盤點LINE圖文選單、QR、郵件、瀏覽器書籤、教材與公告連結。不可只憑概念站外觀判定沒有真實資料。
2. **保存回復資料**：保存明確來源提交、原始設計、必要檔案及服務設定；敏感設定透過受控備份保存，不寫入Git或說明文件。Git不包含未追蹤或忽略檔，工作樹歸檔前須另確認。
3. **先完成入口移交，再退場**：將確實使用舊站的入口更新到正式站對應功能，保留必要公告／說明。教材與內容站若仍是業務來源，先補替代內容，才安排其退場。
4. **驗證舊連結**：網站登入、LINE既有社員／非社員入口、Email驗證返回、付款返回、訂單詳情、便當取餐與社務申請等路徑須逐類確認。舊交易連結不得全部不分狀態導向首頁；第三方回傳端點不能以一般靜態轉址代替。
5. **最後才關閉獨立舊站**：由服務管理者針對已確認的舊專案操作，確認不影響正式域名、API、資料庫、R2、通知與LINE設定。刪除本機來源或Git分支不會完成此步。

## Legacy路徑保留

- 保留正在使用的 `/shop`、`/products/:id`、`/checkout`、`/group-votes`、`/groups/:campaignId`、`/meals/:eventId`、`/meal-orders`、`/orders`、`/account` 及既有驗證／付款／物流返回路由。
- 保留FastAPI `/v1/*`、provider callback／查單／返回端點、歷史狀態及資料migration。名稱含 `group`、`v2`、`legacy` 或 `app` 不代表可以刪除；它們可能維持現有訂單與資料相容。
- 保留 `assets/`。Vite以它為publicDir；目前商品、餐點、Logo、活動及圖片失敗備援均使用這裡的內容。資料庫保存的圖片URL也可能仍引用舊檔名。
- `docs/design/01-home.png` 至 `05-admin.png` 是舊手機概念圖，沒有進入目前Web建置，可另案保存／歸檔；LINE選單PNG／SVG是不同用途，不包含在這個候選清單。

## 回復與驗收

現有回復點包括本次基準 `a50ed81`、正式線查核基準 `656acfe`、Vite遷移 `33abeaa`、原Expo `219405a`。本次只記錄這些已存在提交，沒有新建或刪除refs。

每次後續退場應記錄：操作人、日期、來源與目標、資料保存位置、入口變更、關閉服務、驗收及可回復方式。若涉及正式交易入口，須確認舊連結與新入口皆正常、沒有跳錯帳號或重建訂單，並保留回復原設定的方式。

本次沒有授權刪除其他工作樹、Downloads內容、歷史分支或外部服務。最初本機 `web/node_modules` 曾借用其他工作樹；依賴修補後已移到本次專用驗證 prefix，與該工作樹解耦。它仍是本機測試所需的暫存依賴，不能直接刪掉正在使用的 prefix；CI依鎖檔乾淨安裝，不靠這個本機目錄。

## 續查：目前可精簡的實際範圍

`4be705a`來源與對應163檔immutable build再次盤點：lock中沒有Expo／react-native，Web來源與此隔離build沒有硬寫舊Lovable／Render網址。166份tracked來源經TypeScript import與route glob展開，164份可由入口到達；其餘為API contract JSON及ambient型別，均有CI／編譯用途。91個輸出JS皆在靜態／動態import圖內，沒有可直接刪除的孤立chunk，也沒有位元相同的重複公開資產。不能據此推論正式環境變數、舊金流快照或使用者瀏覽歷史已全部排除。

五張`docs/design/01-home.png`至`05-admin.png`舊概念圖共9,117,278 bytes可另行歸檔；它們原本就不進dist，移出只減少目前checkout空間，不會加快網站載入，也不會自動移除Git歷史blob。本次保留它們。會員legacy歷史、cart redirect、付款／物流／LINE callback、公開圖片與CSV範本、內容教材連結仍有實際依賴，不能按名稱刪除。

完整來源／dist hash、import圖與依賴證據見`outputs/remediation-completion/legacy-web-lightweight-verification.md`。首頁初始3個JS＋1個CSS為511,700 bytes；所有lazy路由JS合計1,229,355 bytes，後者不能當成首頁一次下載量。既有本人歷史分頁、輪詢範圍與列表N+1修復的效益另見`ORDER_HISTORY.md`，不把文件圖片歸檔當成網站效能修復。
