# 十里方圓全方位驗收報告

日期：2026-09-05（Asia/Taipei）

後續：使用者同日授權本機修復，結果見 [修復報告](ACCEPTANCE_FIXES_2026-09-05.md)。本文保留初次驗收快照、當時失敗案例及雲端證據；不代表修復後測試仍失敗，也不代表修復已部署。

## 結論

**目前不建議作為已完成正式驗收的版本放行。** 商店及核心交易程式不是整體方向錯誤；付款、發票、通知已分開處理，而且多數既有測試通過。但排程實際未執行、取消後仍能建立付款、退款後發票查詢被封鎖，以及登入還原錯誤處理，都會影響真實交易的完成與追蹤。

本次是驗收，不是修復或重新部署：沒有修改應用程式、migration、雲端設定或手動更動正式業務資料，沒有主動建立新的付款、退款、發票或寄信要求。新增的檔案是驗收測試、唯讀檢查工具及本報告。網站既有的 `/v1/` 請求補跑機制仍可能在一般瀏覽時自行處理既有待辦。

「通過」只代表下列已執行的檢查，不代表所有情境、所有瀏覽器或正式供應商環境都已通過。

## 基準與證據

- 程式碼：`codex/v2-social-commerce`，`dcf8bf04cdd0cd3a85ec63bb9bd0e61746624bf9`；開始時工作區乾淨，與該遠端分支一致。
- GitHub 預設分支：`main`，仍在 `0082ba079645ea8e0fc2f7d8095b892b24b37682`。不能把目前開發分支的工作流程當成排程實際執行版本。
- 前端：<https://shilifangyuan-web.onrender.com>；API：<https://shilifangyuan-api.onrender.com>。
- 雲端 `/health` 回報 `environment=preview`；這次沒有逐項讀取正式 Secret，也沒有用 `/health` 證明雲端 commit 與本機完全一致。
- 本機 Python 3.12、Node 26；後端隔離測試使用 SQLite，不連正式資料庫。
- [本版 CI 執行紀錄](https://github.com/Wayne725/shilifangyuan-farm-app/actions/runs/33886006157)：前端、後端、API 契約通過；CI 的 PostgreSQL 16 migration 升級、降級、再升級通過。
- [實際排程執行紀錄](https://github.com/Wayne725/shilifangyuan-farm-app/actions/runs/33945086074)：工作流程成功，但部署參數為空而跳過 reconciliation。

### 測試結果

| 檢查 | 結果 | 限制 |
| --- | --- | --- |
| 既有後端測試 | 348 通過 | 主要使用 SQLite 測試 fixture |
| 新增匿名／角色權限測試 | 245 通過 | 路由權限邊界，不等於所有業務狀態完整覆蓋 |
| 新增他人訂單／付款存取測試 | 4 通過 | 查訂單、查付款、補查付款、取消他人訂單均被拒絕 |
| 新增完整交易模擬整合 | 1 通過 | 雷門／汎宇 HTTP 與寄信端替換為模擬，不會產生真實交易 |
| 新增後端問題重現 | 4 失敗 | 下文 A02、A03、A04、A05；保留為修復驗收條件 |
| 合併後端測試執行 | **598 通過、4 失敗，44.15 秒** | 602 個案例，無略過 |
| 既有前端測試 | 26 通過 | 含契約與原始碼檢查，不是 26 條完整瀏覽器交易流程 |
| 新增前端登入還原測試 | **1 失敗** | A08：503 被當成沒有登入 |
| TypeScript、正式 build、API 路由契約 | 通過 | Build 成功不代表雲端業務流程完成 |
| `pip-audit`、pnpm 正式／完整依賴掃描 | 未查到已知漏洞 | 套件資料庫掃描，不等於沒有未知漏洞或完整滲透測試；Python 掃描的是本機環境 |
| 公開站低併發檢查 | 36 項通過 | 最多 3 個並行請求，不是負載測試 |

既有後端 statement coverage 約 **72%**。重點檔案：汎宇 adapter 86%、發票 service 81%、付款 service 81%、雷門 adapter 86%、背景工作 67%。部分路由低於 50%（例如 orders、groups、proposals、meals）；這不是分支覆蓋率，也不能靠通過數量推論不存在業務漏洞。

## 必須優先修復

### A01／P1：排程顯示成功，實際沒有查付款或處理發票

**證據：雲端工作流程紀錄＋程式碼。** 2026-09-05 04:38 UTC 的 scheduled run 中，`RECONCILE_URL` 與 `RECONCILE_SECRET` 為空；執行的是「Skip until deployment secrets are configured」，最後仍回報成功。沒有執行呼叫後端的步驟。

目前開發分支的 `.github/workflows/reconcile.yml` 已會在缺設定時失敗，但排程跑的是舊 `main`。GitHub 排程只使用預設分支，而且排程可能延遲，不能當成精準每十分鐘的保證。[GitHub 官方 schedule 說明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

`backend/app/main.py:159` 只在部分 API request 後補跑；`/health`、`/ready` 不會驅動發票工作。`backend/app/jobs.py:884` 每輪先取當時的待辦 ID，新產生的寄信事件要等下一輪。新增完整交易測試已驗證：第一輪保存發票並排入寄信，第二輪才寄。

**影響：** 客人付款離站後，付款補查、開票或通知可能不能持續前進。Render 不冷啟動並不會補上缺失的排程。這是「付款通知有收到、發票沒有」的具體系統風險；但本次沒有讀取使用者那一張訂單的正式 Outbox／供應商紀錄，不能斷言該訂單就是此原因。

**修復驗收：** 對齊預設分支工作流程及部署版本，安全設定排程參數；沒有任何瀏覽器流量時仍能完成查付款→開票→通知。缺參數、待辦堆積、長時間未執行必須報警。單純讓工作流程變綠不算通過。

### A03／P1：退款後無法查回發票作廢狀態

**證據：隔離測試失敗＋管理頁。** `backend/app/integrations/invoice_service.py:439` 拒絕所有付款狀態不是 `PAID` 的發票查詢。已退款且已有發票號碼、狀態為 `VOID_PENDING` 的訂單，即使供應商已作廢，也無法透過這個功能查回。

`web/src/pages/AdminPage.tsx:277` 又對非 paid 訂單直接顯示「等待買家付款」，使退款訂單看不到發票查詢操作。管理頁實際可見已退款訂單仍是「等待作廢」，並出現上述不正確提示。

現有退款流程只排入發票調整待辦；adapter 有作廢／折讓封裝，不代表後台已有可完成這些操作的完整流程。

**修復驗收：** 「已有發票的查詢」與「能否新開票」分開判斷。退款後仍可查詢、同步作廢結果；新增必要的原因、權限與稽核；明確區分退款完成、待作廢／折讓、已完成作廢／折讓。作廢或折讓如何選擇仍依合作社確認的會計規則，不可直接一律作廢。

### A04／P1：已取消的訂單仍可建立新的付款

**證據：隔離測試失敗。** 真實取消路徑會把訂單設為 `payment_status=EXPIRED`、`fulfillment_status=CANCELLED` 並填入 `cancelled_at`。`backend/app/integrations/payment_service.py:510` 的付款入口只排除已付款與部分退款狀態，未排除取消狀態；相同資料狀態的測試仍成功建立付款嘗試。

**影響：** 取消後仍可能重新取得付款流程、產生新的扣款風險與退款處理成本。既有「取消後收到舊交易付款結果再退款」保護，不等於可以允許取消後新建付款。

**修復驗收：** API 明確拒絕已取消／已終止訂單；一般、團購、便當各渠道都測試。UI 隱藏按鈕只能作輔助，不能代替後端檢查。

### A05／P1：金流補查重複處理前幾筆，後面的交易可能一直輪不到

**證據：隔離測試失敗。** `backend/app/jobs.py:301` 每次以待付款優先、到期時間排序取固定數量，未記錄公平輪詢用的最後查詢／下次查詢時間。兩筆符合條件的交易、每批限制一筆、連續三次執行，實際只查第一筆。

**影響：** 正常批次上限為 100；當前面持續符合條件的交易達到上限時，後面的交易可能延誤甚至錯過補查時窗。本次沒有證據說正式環境現在已有 100 筆堆積；已確認的是演算法的飢餓情境。

**修復驗收：** 使用公平分頁或持久化下次查詢時間與退避；加入超過批次大小、非終態、失敗、晚到成功等測試。不可只增加 limit 掩蓋問題。

### A08／P1：暫時連線問題被當成登出

**證據：直接執行實際前端模組的隔離測試失敗。** `web/src/lib/api.ts:73` 的 `restoreSession()` 對所有非成功回應（含 503）以及網路例外都清除記憶體 session、回傳 null。上層無法分辨「憑證失效」與「服務暫時不可用」，因此會要求重新登入。

瀏覽驗收也曾在管理頁重新載入／切換後看到登入提示，部分時候又能看到已登入畫面；未取得該次 refresh 請求的完整網路時序，所以不把所有現場登出都歸因於同一原因。

**修復驗收：** 真正的未授權才清除登入；暫時性失敗顯示可恢復的錯誤並可重試。補測重新整理、付款跨站返回、慢網路、503、失效 cookie、多分頁。後端 refresh token 輪替的多分頁／中斷競態仍需額外測試，不應直接取消 token 輪替安全性。

## 其他確認問題與缺口

### A02／P2：供應商清單因展示 Email 造成 500

`backend/app/routers/operations.py:78` 建立 `SupplierRead` 時，解密的 `eggs@example.test` 被輸出模型的 `EmailStr` 拒絕，導致整個清單無法回傳。隔離測試與 Render 實際 ValidationError 一致，管理頁也顯示供應商載入失敗。

應修正展示資料及輸出資料相容策略，避免一筆壞資料拖垮整張清單；不要為了範例資料全面關閉使用者 Email 驗證，也不要覆蓋合作社真實資料。

### A06／P2：後台只能操作前 12 張訂單

`web/src/pages/AdminPage.tsx:212` 將資料切成前 12 筆，沒有歷史分頁／搜尋。另一方面 `backend/app/routers/orders.py:303` 讀取所有訂單與關聯後才交給前端截斷。

結果是資料愈多 API 愈重，舊訂單的客服、退款及發票操作卻反而找不到。需要後端分頁／篩選和前端歷史訂單操作入口。

### A07／P2：失敗待辦與信件送達狀態無法完整追蹤

- `backend/app/jobs.py:927` 超過八次失敗就停止；終止告警目前只針對退款，未完整涵蓋開票／Email。
- 管理端沒有完整的失敗待辦清單、原因、下一次執行時間與受控重送。發票「重新查詢」不是重新排入失敗的開票工作。
- `backend/app/jobs.py:1390` 丟棄寄送結果，未保存供應商 message ID；無法區分寄信服務接受、實際送達、退信。
- 最新程式在開票成功後一律另外排入平台的 `invoice_issued` 通知信；它不是「先確認汎宇寄送失敗才補寄」，也不是官方 PDF 附件。`docs/INVOICE_FLOW.md` 和舊 TODO 仍寫不使用 Resend 重寄，與程式不符。
- 汎宇 `/getNotificationFailList` 是處理／上傳錯誤相關介面，不能當成 Email 已送達或退信的證據。

若要正式營運，必須能沿同一張訂單追到付款嘗試、開票請求、票號、寄信待辦及結果；建立人工重送時要保留原因、權限及防重複措施。

### A09／P2：部署設定與文件存在落差

- 實際公開 API 仍是 preview；已上雲與已完成正式營運設定是不同件事。
- `render.yaml` 仍寫 `plan: free`；使用者表示已升級付費方案，本輪沒有核對帳單。再次同步 Blueprint 前必須確認不會將實際方案改回。
- Blueprint 的受限雷門真實付款與汎宇測試開票設定，不等於全功能正式商家設定。本輪沒有以新增交易驗證所有線上 Secret。
- 舊 TODO 的「未部署」、「沒有明確 connection pool」、「沒有付款欄位」、「不重寄汎宇通知」等文字已落後於現況；本報告優先於舊盤點描述。
- 後端 requirements 使用範圍版本，未完整鎖定解析結果；本機、CI 與雲端的實際套件版本仍可能不同。
- 歷史對話中曾提供測試 API 密碼／金鑰；不要把這些直接當正式 Secret，應輪替測試憑證並使用獨立正式憑證。本報告不保存明文。

### A10／P2：PostgreSQL 併發與承載量尚未被驗收

目前有明確的連線池設定：預設 pool 5、額外上限 5、等待 10 秒，並有 `pool_pre_ping`、連線逾時與 request session 釋放。`/ready` 有資料庫探測及 3 秒逾時；不能沿用舊文件說這些都沒做。

但大部分 HTTP／業務測試 fixture 固定使用 SQLite，即使 CI 啟動 PostgreSQL 服務也沒有讓這些 fixture 自動變成 PostgreSQL。行鎖、防超賣、同時付款與同時退款的競態仍缺真實 PostgreSQL 整合驗證。

登入路徑直接在 async handler 執行同步 Argon2 密碼驗證。隔離微基準中，1／5／10 次驗證約 30／136／282ms，對測量中的 event loop 心跳最大延遲約 20／126／273ms。這證明有阻塞工作，不是正式站容量測試，也不能用來認定先前 status 255 是多人登入引起。

未對正式網站執行 10／50／100／300／500 人壓測，未取得完整 CPU、RAM、DB connections、query time 曲線；應在隔離的 PostgreSQL staging 測量，而不是拿正式訂單做壓測。

## 功能覆蓋盤點

| 功能 | 已驗證 | 未通過／待補 |
| --- | --- | --- |
| 商品與圖片 | 公開商品 API、27 張商品卡渲染、19 個去重圖片資源可取；桌機及 390px 商店無水平溢出 | 並非整站每一張圖片都已逐張查驗；部分 JPEG 約 315–493KB，共用商品卡未設 lazy loading |
| 帳號／會員 | 現有驗證、token、重寄／重設、名冊認領測試；註冊分流、社員認領與非社員表單均可渲染且欄位有區分；角色越權測試 | A08；本輪未重新寄驗證信或新增正式會員 |
| 一般訂單／Checkout | 價格快照、會員價、訂單權限、付款與發票模擬流程 | A04；所有支付方式的供應商實機流程未重跑 |
| 團購 | 既有上限、截止、運費、成團／退款等測試 | 未在雲端新增／取消真實團購；PostgreSQL 併發待測 |
| 便當 | 選項加價、快照、容量、取消、QR 核銷、晚到付款等測試；管理頁餐點／場次渲染 | 未在雲端新增或核銷真實訂單；QR 多人同時核銷未做 PostgreSQL 驗證 |
| 社務 | 提案、活動、投票、會籍、報名／候補、審核等既有測試；匿名入口與權限拒絕 | 非所有角色的完整瀏覽器操作均已走完；部分路由覆蓋率偏低 |
| 雷門金流 | 既有 50 個雷門案例、14 個回歸案例及本次整合；防重複、錯誤金額、晚到、退款不確定性等 | A01、A04、A05；沒有新刷卡或新退款，也沒核對銀行實際入帳 |
| 汎宇發票 | 原始 PDF 與程式欄位核對；13 個 adapter 案例；付款後開票與防重複整合 | A01、A03、A07；沒有重新開官方測試票或正式票 |
| Email | 既有寄信服務、認證信與冪等 key 測試；整合測試信件只寄一次 | A01、A07；本輪沒有以新信驗證 Gmail 實收 |
| 物流 | 既有 AES／callback／狀態驗證與 router 測試通過 | 依先前方向暫不做真實宅配、建物流單或超商寄送 |
| 管理／財務 | 權限邊界通過；訂單頁與便當頁曾成功載入；既有財務／合作社報表測試 | A02、A03、A06；登入反覆中斷，未完成全部管理頁人工操作；未核對真實帳務平衡 |
| 安全／部署 | 惡意 CORS 被拒、匿名敏感 API 401、安全 headers、基本套件掃描、CI、migration 單一 head | 非完整滲透測試；未完整掃描 Git 歷史 secret、實際存取權限／帳號復原及備份還原演練 |

公開站 36 項 smoke 包含 `/health`、`/ready`、商品／團購／便當／取貨／運費 API、前端 HTML 入口、同源 API 代理、四個匿名拒絕、19 個圖片及惡意來源 CORS。HTML 200 只證明入口可取得，不當成動態頁面功能成功。觀測到的單次 API 時間約 73–578ms、同源商品 API 約 744ms，不能外推成 SLA 或多人承載量。

## 發票架構再確認

依使用者提供的《汎宇電子發票 API 說明文件》及《API 範例 V4.1》對照：目前「雷門驗證付款→保存訂單付款狀態→排入汎宇開票→查回／保存票號→通知」的責任分離合理，問題主要在排程、退款後續與可觀測性，不需要推倒重寫成供應商資料庫格式。

- `/openInvoice`、`/queryInvoice` 與 envelope／reqData 的欄位轉換已存在；資料庫保存交易快照，供應商 adapter 負責轉換。
- 手機條碼使用 `3J0002`；會員 Email 載具 `EG0478` 及兩個 ID 帶客人 Email，另有供應商前次對話確認，不能宣稱只靠 PDF 已驗證所有會員載具設定。
- B2B 保存統編、抬頭、Email；B2C 與 B2B 金額／稅額欄位按各自文件格式處理，不將 B2C 的 API `taxAmount=0` 誤判成商品一律免稅。
- 原始說明文件第 6 頁的 `notifyEmail` 備註明示：B2B 通知含 PDF，B2C 是通知。平台付款通知或額外的「已開票」Email 不等同汎宇官方發票交付。
- 查單後再決定是否開立、保存票號及發票品項快照已有程式與測試；模擬通過不等同這次已重新接收供應商信件。
- 作廢 API 對 B2C 買方代碼的欄位規定與開票不同，不能只因零的位數不同就改成相同；本輪已核對原表。舊範例部分紙本／欄位值與目前雲端需求不同，不能直接照抄。

來源檔案：

- `/Users/liuweiwei/Downloads/汎宇電子發票API說明文件.pdf`，重點第 3–7、15–16 頁。
- `/Users/liuweiwei/Downloads/汎宇電子發票API範例V4.1.pdf`，重點第 1–3 頁。

## 重跑方式與測試產物

從 repository 根目錄執行；使用隔離 SQLite，不填正式 `DATABASE_URL`。四個後端 red tests 和一個前端 red test 是這輪發現的未修復問題，不是刻意略過、預期綠燈或修復完成的宣告。

```sh
DATABASE_URL=sqlite+aiosqlite:///:memory: PYTHONPATH=backend .venv312/bin/python -m pytest -c backend/pyproject.toml backend/tests backend/audits -q --tb=short
node --test tests/audits/web-session.test.mjs
pnpm test
pnpm typecheck
pnpm build
pnpm audit --prod
pnpm audit
.venv312/bin/python -m pip_audit --local
.venv312/bin/python scripts/acceptance_public_smoke.py --web https://shilifangyuan-web.onrender.com --api https://shilifangyuan-api.onrender.com
```

- `backend/audits/test_acceptance_findings.py`：四個問題重現。
- `backend/audits/test_access_boundaries.py`：245 個權限邊界案例。
- `backend/audits/test_financial_pipeline.py`：四個 IDOR 案例與完整交易模擬。
- `tests/audits/web-session.test.mjs`：前端登入還原問題重現。
- `scripts/acceptance_public_smoke.py`：不提交交易的低併發檢查。
- 暫存 JUnit：`/private/tmp/farm-acceptance-final.xml`；baseline coverage：`/private/tmp/farm-acceptance-coverage.json`。暫存檔可能被系統清除，持久結論以本報告與可重跑測試為準。

新增 audit 測試尚未加入既有 CI 的測試路徑；因此目前 GitHub 綠燈不包含這些新發現。修復後應將相關 regression 納入常規 CI，不應刪掉 red test 讓結果變綠。

## 下一輪放行條件

1. 修復 A01、A03、A04、A05、A08，將本次 5 個 red tests 變成通過，補上排程實際呼叫的證據。
2. 修復供應商清單、歷史訂單查找，以及失敗開票／寄信的追蹤與人工處理入口。
3. 用 PostgreSQL 隔離環境驗證併發、防重複、超賣、worker 中斷復原，再做有監控的階梯負載測試。
4. 在另行授權與確認測試範圍後，完成三種發票方式：B2C Email 載具、B2C 手機條碼、B2B 公司；每一筆核對訂單、金流交易、票號、汎宇平台與實際通知信。特別測試付款後關閉瀏覽器仍自動完成。
5. 退款再確認發票調整結果，而不是只確認錢退了。正式環境憑證、賣方身分與實際入帳／對帳要獨立確認。

本輪採用 diagnosing-bugs 的可重現案例方式，不以猜測直接改程式；使用 PDF 技能核對汎宇原始欄位表，避免把接口格式差異誤判成架構錯誤。
