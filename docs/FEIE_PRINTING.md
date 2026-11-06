# 飛鵝雲端出單

初版日期：2026-10-22；付款後出單規則更新：2026-11-06。下段為 10/31 的歷史狀態；目前規則見「2026-11-06 付款後出單規則」，最新部署與驗收另記。

歷史紀錄：已在中國站核對同一台 FP-V58-W 在線、正常、待印 0；送出一張無個資測試單，使用者確認「印出1張，繁體中文正常」。這證明中國站至實機可遠端列印，尚不代表網站訂單串接完成。亞太站仍離線、待印 4 筆，來源未確認，未清除或重送。正式網站出單仍未啟用。

2026-10-31 三個方法專屬路徑修正已經 PR #23 部署至 API commit `6f39b471064255b6ed834d9d0fa103277a4b7b93`。後續中國／亞太雙區與工作隔離修改已完成本機 131 項相關測試，發布狀態另見需求及發布紀錄；正式開關及憑證未更改。配網教學及來源見 [到貨查核](FP_V58_W_ARRIVAL_SETUP.md)，區域實測見 [區域調查](FEIE_CLOUD_REGION_SETUP_RESEARCH.md)，API 契約見 [API 查核](FEIE_API_RESEARCH.md)。下方 2026-10-22 的測試與部署描述保留為當時紀錄。

## 2026-11-06 付款後出單規則

本節取代下方歷史版本中「成立即印、現場未付款也印」的規則。

- 建立便當訂單時，列印工作仍與訂單同交易保存；此時只排隊，不呼叫飛鵝送印。
- 線上付款須由既有後端金流查單／通知流程確認成功；現場付款須管理員確認實收。兩者確認後才可自動送印。
- 背景程序以目前訂單付款、取消狀態及版本作送印資格核對；付款未完成不占用其他已付款工作，舊待送單同樣受保護。
- 已有雲端編號的結果查詢仍可進行，不因退款、收款更正或取消而重送。曾送出的訂單取消時，保留獨立取消通知。
- 舊版已凍結為未付款、但尚未送出的內容不直接改寫或發送；停止原工作，管理員確認付款後另建補印。已付款的安全重試仍沿用同一內容。
- 取消或進入退款流程的未送工作停止；付款失敗／逾期但尚未取消的訂單仍可按既有金流規則重試或接受有效晚付款，確認前一律不送印。
- 補印製作單也須目前已確認付款；付款通知重送或再次確認收款，不會自動新增第二張原單。
- 後台以「等待付款」區分未付款的待送工作。付款後沿用原工作，沒有資料庫狀態或遷移變更。
- worker 取得送印資格並提交 claim 後，HTTP 在交易外執行；這段期間發生的取消／收款更正無法撤回已在途的請求，仍須按取消通知及紙本結果處理。

發布與驗收證據會另列；不以隔離測試代替正式紙本驗收。

## 現有架構與整合決策

2026-11-06 新增管理員「再次出單」入口，詳見 [管理員訂單補印](ADMIN_ORDER_REPRINT.md)。付款後自動出單規則保持不變；管理員再次出單也僅限已確認付款且未取消的訂單，已取餐等歷史紀錄標示「請勿製作」。未付款、退款中、已退款與已取消不提供手動補印；舊排隊工作與舊補印 API 同樣受付款門檻限制，既有結果查詢與自動停做取消通知保留。

- 後端為 **FastAPI**，入口 `backend/app/main.py`；訂單 API 為 `backend/app/routers/meals.py` 的 `POST /v1/meal-events/{event_id}/orders`。不是 Flask，也不需另建訂餐網站。
- SQLAlchemy async ORM；Render 設定使用 PostgreSQL，本機與測試使用 SQLite。
- 原流程鎖定場次、核價、建立 Order／OrderItem／OrderFulfillment，現場付款保留餐點名額，最後 commit。出單工作加入**同一交易**，外部 API 全在 commit 後的背景程序呼叫。
- 原有 `jobs.py` 有非同步定期對帳與一般 outbox，另有 GitHub Actions 每十分鐘對帳。一般 outbox 的自動重試不適合結果不明的列印，所以新增獨立持久化 PrintJob 與 worker，沒有增加 Redis 或 Celery。
- 管理員已有便當管理、退單審核、廚房備餐、瀏覽器 80mm 預覽補印及現場付款核對。雲端 58mm 出單沿用廚房頁及 `require_admin` 權限，保留原有流程。
- `render.yaml` 定義 Python API、PostgreSQL 與靜態前端。2026-10-22 公開 `https://shilifangyuan-api.onrender.com/health`、`https://shilifangyuan.com` 均 HTTP 200 且有 Render `rndr-id` 回應標頭，確認仍使用 Render。**沒有登入 Render 後台，無法從公開回應確認實際方案／副本數。**版本庫範本仍是 free 方案，不能視作即時出單的常駐保證。

初版曾在便當訂單成立後立即送出小白單，包括現場未付款訂單；2026-11-06 起的付款後出單規則見上節。付款認列、發票與備餐清單仍沿用既有流程。

## 修改檔案

| 檔案 | 用途 |
| --- | --- |
| `backend/app/integrations/feie.py` | 中國／亞太端點白名單、簽章、送印、查單、設備狀態、逾時分類 |
| `backend/app/printing.py` | 不可變訂單快照、58mm 換行、取消通知、管理員讀取格式 |
| `backend/app/printer_worker.py` | 背景喚醒／輪詢、資料庫 claim／租約、有限安全重試與查單 |
| `backend/app/models.py`、`schemas.py` | PrintJob、便當備註及送單去重識別碼 |
| `backend/alembic/versions/0030_feie_print_jobs.py` | 列印工作及訂單欄位遷移，接續 0029 現場付款版本 |
| `backend/app/routers/meals.py` | 交易內排單、重送回傳原訂單、核准退單／整場取消通知 |
| `backend/app/routers/printers.py`、`main.py` | 管理 API、權限稽核、啟動背景程序 |
| `web/src/components/CloudPrintPanel.*`、`pages/AdminKitchenPage.tsx` | 設備查詢、狀態篩選／分頁、預覽、補印、人工紙本核對 |
| `web/src/lib/commerce.ts`、`pages/MealEventPage.tsx` | 餐點備註、送單 UUID；同一分頁工作階段重試／重新整理沿用識別碼 |
| `web/src/pages/HelpPages.tsx`、`lib/api.routes.json` | 操作說明與 API 契約 |
| `backend/.env.example`、`render.yaml` | 預設關閉及模擬模式設定 |
| `backend/tests/test_feie_printing.py`、`test_feie_api_flow.py`、`tests/browser/cloud-print-flow.mjs` | 無真實憑證／無硬體的測試與完整接口串接 |

## 工作與防重印

`pending → sending → submitted → printed`。`submitted` 僅雲端接受；供應商查單 true 才確認列印。查單 false 保持 submitted；設備離線時由飛鵝保留已接受工作，本系統只查結果，不重送。

- `failed`：本地內容過長、文件明確拒絕、可確定未送出的連線失敗達重試上限，或取消前尚未發送。
- `unknown`：read/write timeout、HTTP 異常、無效回應、成功卻缺雲端 ID、發送中程序中斷。沒有雲端 ID 就無法可靠查回那張單，須核對紙本，**絕不自動重送**。
- `simulated`：Dry Run 已產生排版內容，沒有網路請求、雲端 ID 或實體成功時間。
- 每個原單及取消通知各有唯一 `event_key`；補印有獨立 UUID，並關聯原工作、原因及管理員稽核。同一補印 request_id 重送只回傳原補印工作。
- 訂單以 `(user_id, client_request_id)` 唯一約束去重。同一 UUID、同一內容回傳原訂單；換內容卻沿用 UUID 回 409。舊客戶端不送 UUID 仍相容，但沒有此去重保護。前端以 SHA-256 摘要作 sessionStorage 鍵，不把姓名／信箱直接放進儲存鍵；已成功回應會清除識別碼。
- 先持久化 sending 與租約才送 API。程序在送出後中斷時，租約到期改 unknown，不重新發送。多個程序以訂單鎖、工作鎖與條件更新 claim；worker 不在網路等待時持有資料庫交易。
- 正式／模擬模式與設備身分各自保存在工作上，改設定不會把既有模擬單自動變成正式單，也不把舊單送去新設備。管理員可明確建立新補印工作。
- 新工作快照保存 `printer_region`；舊工作缺此欄位只視為 JP，未知區域保持停送。fingerprint 計算不變，原本已包含 URL／USER／SN。worker 與查單比對模式、fingerprint、區域；跨區補印回 409。同區改設備或模擬轉正式的明確手動補印仍保留。
- 送單成功不等待列印。列印錯誤留在 PrintJob，訂單及庫存保留不會因供應商故障回滾。

核准單筆退單或取消整場時：尚未發送的原工作停止；若曾發送／可能有紙本，另存取消通知，含原單號及停止製作標示。原工作仍 sending 時先等其結果。取消通知本身也有狀態，結果不明時同樣需廚房人工處理，不能保證斷網期間即時出紙。

58mm 使用保守 32 顯示格（中文字占兩格），選項、500 字內備註、單價／數量／總額、台灣取餐日期時間及付款狀態皆有保留；不插入切紙指令。使用者的 `< > &` 換為全形並移除控制字元，避免印表機標籤注入。超過 5,000 UTF-8 bytes 整張標 failed，保留原單供人工處理，不默默截掉餐點。小票付款狀態於首次送印時封存；確認收款後原待送工作才會發送，已送原單不因重複付款通知再次出單。手動補印使用最新付款狀態。

## 設定與部署

環境範本在 `backend/.env.example`。不要提交真實 USER、UKEY、SN，亦不要貼進對話或前端。暴露過的 UKEY 必須先到飛鵝後台更換；本次沒有登入帳號或代為更換。

| 變數 | 預設／用途 |
| --- | --- |
| `PRINTER_ENABLED` | false：不建立新訂單原單、不處理舊工作、不查設備；既有單取消仍保存待處理通知 |
| `PRINTER_DRY_RUN` | true：只產生模擬單，完全不送飛鵝 |
| `PRINTER_WORKER_ENABLED` | true：隨 FastAPI 啟動處理程序；獨立 worker 時 web 可設 false |
| `FEIE_USER`、`FEIE_UKEY`、`FEIE_PRINTER_SN` | 後端秘密設定；Dry Run 可留空 |
| `FEIE_API_BASE_URL` | 預設 `https://api.jp.feieyun.com/Api/Open/`；中國站為 `https://api.feieyun.cn/Api/Open/`。只允許這兩個官方 HTTPS 前綴，附加固定三方法路徑、不轉址、不跨站重試 |
| `FEIE_UKEY_ROTATED` | false；確實更換曝光 UKEY 後才設 true |
| `FEIE_AP_ENDPOINT_VERIFIED` | false；只授權已確認的亞太帳號／設備，不授權中國站 |
| `FEIE_CN_ENDPOINT_VERIFIED` | false；只授權已確認的中國帳號／設備，不授權亞太站 |
| `PRINTER_TIMEOUT_SECONDS` | 10，每次 HTTP 上限 |
| `PRINTER_POLL_SECONDS` | 2，掃描待處理工作；commit 後另有即時喚醒 |
| `PRINTER_QUERY_SECONDS` | 15，雲端結果輪詢 |
| `PRINTER_LEASE_SECONDS` | 120，處理中斷的保守等待時間，需大於 HTTP 上限 |
| `PRINTER_MAX_ATTEMPTS` | 3，僅未建立連線的送印嘗試上限 |
| `PRINTER_LINE_WIDTH` | 32，實機字型不合時可減至 24 |

在後端執行 `alembic upgrade head`（既有 `python -m app.startup prepare` 亦會遷移），再依既有方式啟動 FastAPI。先設 `PRINTER_ENABLED=true`、`PRINTER_DRY_RUN=true`，從 LINE 測試便當下單，先確認「等待付款」且無發送；在隔離環境確認收款後，再到「後台 → 廚房 → 雲端出單」確認 simulated 與預覽內容。關閉功能期間的歷史訂單不會事後自動補排。

正式自動出單需要**常駐雲端程序**。Render web 若確定不休眠，可沿用內建 worker，不需店內電腦；若選獨立 Background Worker，使用相同程式碼／資料庫／秘密環境，啟動命令 `python -m app.printer_worker`，web 設 `PRINTER_WORKER_ENABLED=false`。這是雲端部署選項，本次沒有建立付費服務、變更帳單或部署。免費休眠服務與每十分鐘 GitHub 排程不能保證立即出單。

部署或重啟前若有 sending 工作，待租約到期後依 unknown 核對；不要批次把 unknown 改回 pending。停用旗標阻止下一次處理，已經在網路上傳送中的請求可能仍到達設備。

## API 與廚房操作

管理員 API 全部以現有登入憑證、`require_admin` 保護：

- `GET /v1/admin/printers/configuration`：不連外的設定檢查。只回傳各檢查布林值、缺少的環境變數名稱、`configured_region` 與 `live_config_ready`，完全不回傳 USER／UKEY／SN 或原始設定 URL。只檢查所選區域旗標；ready 僅代表本機設定齊全，不代表帳密已通過飛鵝驗證或設備已出紙。
- `GET /v1/admin/printers/status`：設備狀態；Dry Run 不對外查詢。
- `GET /v1/admin/printers/jobs?status=unknown&offset=0&limit=30`：工作紀錄與錯誤代號。
- `GET /v1/admin/printers/jobs/{id}`：原快照排版預覽。
- `POST .../{id}/query`：重新安排查詢，只有既有雲端 ID 可用；不發送新單。
- `POST .../{id}/reprint`：`request_id` UUID、`reason`、`acknowledge_duplicate`；submitted／unknown 必須明確接受可能重複列印。pending／sending 不接受補印；取消訂單只能補印取消通知。
- `POST .../{id}/resolve`：`printed` 與 `reason`；正式 submitted／unknown 可記錄人工紙本核對。確認已看到紙本才標 printed 並註明人工來源；「尚未看到」仍是 unknown，因雲端佇列可能之後才出紙，不能當成安全重送證據。

回應與錯誤紀錄不保存原始簽章表單或供應商錯誤本文。列印快照含姓名及餐點等必要資料，僅後端及管理員可讀，依既有資料庫備份權限管理。

## 到貨後逐步驗收

1. 已確認 **FP-V58-W** 在現場 Wi-Fi 可用，中國站同 SN 在線且一張繁中測試成功。後續採中國站；不再以搬到亞太站作為本次接線前提。
2. 更換已曝光開發者 UKEY。到貨後依該型號手冊接電、放紙、設定店內 Wi-Fi。Wi-Fi 頻段／配網步驟以實際型號手冊為準。
3. 中國站設備綁定及在線核對已完成。**設備 KEY 與開發者 UKEY 不同**；本程式不自動綁定設備。保留亞太舊工作，不做清空、解綁或跨區補印。
4. 使用 [2026-01-15 官方分流公告](https://help1.feieyun.com/docs/96C8PyrC) 的中國三路 URL，先完成雙區隔離程式發布，再切換 `FEIE_API_BASE_URL`。舊文件連結矛盾見 [API 查核](FEIE_API_RESEARCH.md)。
5. 由使用者將中國帳號 USER／輪替後 UKEY／本機 SN 直接填到 Render 後端秘密環境，完成儲存，不貼到對話。確認常駐程序及待印隔離，再設 `FEIE_UKEY_ROTATED=true`、`FEIE_CN_ENDPOINT_VERIFIED=true`、`FEIE_AP_ENDPOINT_VERIFIED=false`、`PRINTER_ENABLED=true`、`PRINTER_DRY_RUN=false`。每次 HTTP 前會重新驗證門禁。
6. 在廚房按「查詢設備」，應顯示在線；關機後離線可能延遲約兩分鐘。使用明確測試帳號建立**一筆**現場付款便當，先確認未付款不出紙，再由管理員確認實收；核對單號、長中文姓名、選項、備註、單價／總額、台灣日期時間、已付款，以及實際斷行。確認只有一張，狀態先 submitted 後 printed。
7. 在確認第一張原單後手動補印一次，填測試原因，確認紙上「補印」及「已付款」。核准取消測試訂單，確認獨立「取消通知」及停止製作字樣。
8. 關閉設備 Wi-Fi 後建立並確認收款的一筆測試單，確認網站仍成功、工作保留、不重送；恢復 Wi-Fi 後確認該筆只出一張。再以本機 MockTransport 測試 API timeout／程序中斷，unknown 時先核對，不用正式設備反覆盲試。
9. 中國站供應商後台至紙本的一次測試已通過；網站 API 狀態查詢、網站訂單及補印／取消等完整流程須另行記錄，未驗證部分不標完成。

## 規格來源

使用 [飛鵝亞太站官方文件](https://feieyun.com/open/AsiaPacific-cn.html) 與官方 Python SDK 查核，詳見 [FEIE_API_RESEARCH.md](FEIE_API_RESEARCH.md)。簽章為 SHA1(USER + UKEY + UNIX 秒)，UTF-8 form POST，`apiname=Open_printMsg`／`Open_queryOrderState`／`Open_queryPrinterStatus`。沒有使用未確認的 callback 參數或未驗簽 webhook。

## 2026-10-22 本機驗證紀錄

- 前次完整後端（加入設定檢查接口前）：1674 passed、4 skipped；跳過的是既有現場付款 PostgreSQL 併發測試，環境未提供 `TEST_POSTGRES_URL`，不代表已完成正式 PostgreSQL 壓測。
- 最新飛鵝及遷移測試：54 passed；包含設定檢查權限／保密、完整接口串接、簽章／區域、MockTransport、逾時分類、模擬與正式隔離、工作重啟、雙 worker 防重複、訂單去重、取消通知及補印稽核。
- 前端 Node：68 passed；相關隔離瀏覽器：27 passed，最後查詢按鈕能力檢查另 4 passed；390px／1280px 無水平溢出。
- TypeScript、Vite 正式建置及 `git diff --check` 通過。
- 沒有使用真實飛鵝憑證、呼叫真實列印或執行實機驗收。程式尚未提交、推送或部署。

### 2026-10-31 路徑修正回歸

新增 MockTransport 契約測試先出現 8 failed，修正後 8 passed；出單、完整 API 流程及遷移三檔合計 64 passed。保留原表單、簽章、UTF-8、轉址拒絕、Dry Run 零 HTTP、未知結果不重送、補印及取消通知規則；另確認未知方法及被改成其他主機的前綴均在傳送前拒絕。`git diff --check` 通過。這些是隔離測試，不能代替實際配網、雲端認證與紙本驗收。

在 backend 目錄可重跑 `python -m pytest tests/test_feie_printing.py tests/test_feie_migration.py -q`。Mock 使用測試憑證及 httpx MockTransport，不需要設備；完整 Dry Run 操作見上方設定步驟。

## 密鑰提供前的接口驗收

2026-10-22 已補齊不連外的設定檢查，並以真正 FeieClient 搭配 MockTransport 走完整流程：便當訂單 API → 同交易工作 → worker 簽章送印 → 離線狀態 → 查單 false → 查單 true → 後台列表／預覽 → 冪等補印 → 核准取消 → 獨立取消通知。測試禁止真實 HTTP transport，因此不依賴實體 USER／UKEY 或設備。

重跑命令（backend 目錄）：`python -m pytest tests/test_feie_api_flow.py tests/test_feie_printing.py tests/test_feie_migration.py -q`。

後續由使用者把中國帳號 USER、輪替後 UKEY 及已綁定設備 SN 填入後端環境，確認中國端點與常駐程序，再依上方流程驗收；**不需將密鑰貼到聊天，也不需硬編碼後再改程式**。設備 KEY 不進入本系統。
