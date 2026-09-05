# Shilifangyuan Phase 0 Audit 與開發待辦

## 最新驗收更新（2026-09-05）

同日已完成第一輪本機修復，**尚未提交、推送或部署**。初次問題與證據保留在 [全方位驗收報告](docs/ACCEPTANCE_AUDIT_2026-09-05.md)，目前結果見 [修復報告](docs/ACCEPTANCE_FIXES_2026-09-05.md)。下方 2026-09-04 盤點僅保留歷史脈絡。

- [x] P1／A01 本機部分：新增可啟用的常駐背景驅動；無瀏覽器流量仍能模擬完成開票與下一輪寄信。
- [ ] P1／A01 雲端部分：確認既有待辦後啟用定期工作、對齊 GitHub 預設分支與必要 Secrets，驗證實際處理紀錄，不接受空跑綠燈。
- [x] P1／A03：退款後可依既有發票快照查詢，保留待作廢狀態、正確管理提示與原因／稽核。
- [ ] A03 後續：完成作廢／折讓管理流程與供應商端到端驗收，目前仍是調整待辦。
- [x] P1／A04：拒絕已取消訂單新建付款及開啟舊平台付款連結，一般／團購／便當皆覆蓋。
- [x] P1／A05：持久化公平輪詢與查詢間隔；新增 0013，不動舊 migration。
- [x] P1／A08：401 才清除登入；503／暫時斷線保留未確認狀態並提供重試。
- [x] P2／A02：讀取供應商的舊展示 Email 不再造成 500；新增／修改仍驗證 Email。
- [ ] P2：訂單只顯示前 12 筆、失敗開票／Email 管理與送達追蹤、雲端部署版本落差。
- [ ] P2：PostgreSQL 併發、階梯壓力測試及另行授權的供應商實機驗收。

修復後：後端 **620 通過／0 失敗**、前端 **31 通過／0 失敗**；型別、build、175 路由契約通過。SQLite migration 往返與舊付款資料保留、PostgreSQL 離線 DDL 通過；尚未執行 PostgreSQL 實際升級／併發。驗收案例已納入本機標準測試入口，雲端 CI 要等推送才會執行。初次公開站 36 項檢查屬前一輪結果，本輪未對正式站交易、寄信或部署，仍不作正式營運放行。

---

更新日期：2026-09-04
Audit 基準：`codex/v2-social-commerce`，含 2026-08-26 readiness、資安與汎宇發票工作區修正
本輪範圍：保留交易核心，改為付款確認後排入汎宇開票，保存精確供應商請求，並補上退款後的作廢／折讓待辦；不啟用正式憑證、不送出正式發票，Sandbox 只允許低額驗收票

## 結論先行

專案不是從零開始。訂單、付款嘗試、退款、發票、Email Outbox、物流與管理端均已有可測試的骨架；雷門金流與汎宇發票已接在既有領域流程後方，沒有建立第二套平行訂單系統。

Render status 255 已由缺少 Alembic revision 定位並修復，發票也已確定在後端驗證付款成功後排入。汎宇 WEB API Sandbox 已完成 HMAC-SHA256、B2C `EG0478` Email 載具、NT$100 開票、銷貨單查回及 Gmail 通知驗收；雷門 Production 網址與憑證已通過正式查單 smoke test，並已確認沒有主動通知。端到端驗收目前卡在最新版尚未部署、排程 Secret 尚未驗證，以及正式低額付款／全額退款演練。

## 尚待外部確認的決策

1. 雷門已確認目前憑證與網址屬於 Production，且沒有主動通知；尚需確認非終態最晚收斂時間、建議查單頻率、正式對帳方式，以及是否另有 Sandbox。
2. 外部服務目標是否為「雷門金流＋汎宇發票＋保留綠界物流」？目前程式是綠界金流、發票、物流三者皆有。
3. 全額退款與部分退款的會計規則為何？需確認作廢／折讓、跨期與稅額拆分；系統目前只建立待辦，不自動送出。
4. 正式上線前需由汎宇提供正式環境專屬憑證並完成同一套驗收；Sandbox 成功不得視為 Production 已獲授權。
5. 正式／測試憑證只放 Render Secret／本機 `.env`，不要貼進聊天、Git、PDF 或 issue。

## Existing：目前已完成

### Frontend

- `web/` 是唯一正式前端，使用 Vite、React 19、TypeScript 與 route lazy loading。
- 已有公開商店、商品詳情、團購、便當、購物車、Checkout、訂單中心、社員認領、非社員註冊、社務及管理工作台。
- Checkout 已支援合作社取貨、宅配／超商物流、Email 會員載具、手機條碼與公司統編發票，並在送出前顯示完整摘要。
- API 路由契約由 `web/src/lib/api.routes.json` 驗證，目前記錄 172 個路由／方法項目。

### Backend / Authentication

- FastAPI 路由依領域拆分，SQLAlchemy AsyncSession 以 request scope 開啟並在結束時釋放。
- Email 驗證 token、到期時間、一次性使用、重寄、忘記密碼及密碼重設已完成。
- Access token 搭配 HttpOnly refresh cookie；refresh session 可輪替、撤銷，密碼變更會使既有 session 失效。
- Login、Register、Email Verification、Forgot Password 與證件上傳已有單機 rate limit。
- 既有社員採加密名冊核對、短效保留、Email 驗證後建立正式會籍，不會只因使用者勾選「社員」就授權。

### Order / Payment

- `orders` 已分開保存 `payment_status`、履約狀態與 `invoice_status`，不是單一 `paid=true/false`。
- `order_items` 保存品名、單位、數量、單價、小計與稅別快照，商品改名或下架不會直接改變歷史訂單。
- `payment_attempts` 保存平台交易編號、供應商交易編號、金額、狀態、到期時間與 provider response。
- 綠界 callback 已驗證 Merchant ID、CheckMacValue 與付款金額；付款成功以後端驗證結果為準。
- 雷門 adapter 已完成付款 URL、回跳立即查單、前端有限補查、背景補查、退款封裝與 provider 快照；正式網址與憑證已安全匯入本機並通過無副作用查單。
- `external_events` 對供應商事件建立唯一鍵，重複 callback 不會重複套用交易。
- 已有 server-to-server 交易查詢及逾期付款 reconciliation。
- `refunds`、退款原因與 Outbox 事件已存在。

### Invoice

- 保留綠界 adapter，另完成汎宇 `/openInvoice`、`/queryInvoice`、B2C／B2B、手機條碼與雲端 Email 欄位轉換；B2C 會員載具使用 `EG0478`，兩個載具 ID 均帶訂單客人 Email。
- 後端驗證付款成功後，付款狀態與 `invoice.issue_requested` 以同一交易保存；取貨／送達只保留舊資料補排功能。
- 每次開立前先依銷貨單號查詢，timeout 後可復原既有發票，不直接重開。
- `invoices` 已保存 provider、成功付款、買方、精確 `reqData`、金額、狀態與作廢快照；`invoice_items` 以六位小數保存實際送出品項，商品日後改名或改價不影響歷史。
- 管理端可填原因重查銷貨單發票，成功與供應商失敗均寫入 `admin_audits`。
- 汎宇回應會驗證發票號碼與狀態；錯誤訊息會遮罩密碼、APIKey 與 Base64 `auth`。
- 使用汎宇時由 `notifyEmail` 交付官方發票（B2B 含 PDF），平台只建立站內通知，不用 Resend 重寄相同發票信。

### Email / Background Work

- Resend 是主要寄信服務，MailerSend 可作備援；兩者共用 `EmailSender` 介面。
- Email 與發票使用資料庫 Outbox，失敗採指數退避，最多嘗試 8 次。
- Resend 使用固定 `Idempotency-Key`，worker 在供應商收信後中斷時可降低重複寄送風險。
- GitHub Actions 每 10 分鐘呼叫 reconciliation；Render 休眠錯過排程時，API request 會非同步補跑。

### Security / Deployment

- API 有 CORS、Trusted Host、CSP、HSTS、`nosniff`、frame deny、no-store 等基本防護。
- 私密個資使用版本化 AES-256-GCM；R2 使用私有 bucket、短效簽名網址與檔案內容驗證。
- 遠端環境會拒絕弱 JWT／reconcile secret、預設展示密碼、錯誤 stage 設定與缺少 Resend 的部署。
- CI 會執行前後端測試、正式 build、API 契約、migration upgrade／downgrade、`pip-audit` 與 Render YAML 驗證。

## Partial：已有程式但不足以正式營運

### Server Stability

- SQLAlchemy 已使用 QueuePool 預設行為與 `pool_pre_ping`，但沒有明確的 `pool_size`、`max_overflow`、`pool_timeout`、`pool_recycle` 或連線使用量指標。
- `/health` 維持 liveness；已新增 `/ready` 檢查 DB 與 3 秒逾時，尚未加入 migration revision 與 Outbox 堆積指標。
- Preview migration 與 seed 已移至可辨識階段、具重試的 startup prepare；尚未記錄啟動階段記憶體。
- Reconciliation 是資料庫 Outbox 加 GitHub cron／request 補跑，不是獨立 worker；多實例時缺少分散式排程鎖與完整 queue 指標。

### Payment / Refund

- `payment_attempts` 已涵蓋核心資料，但尚未保存 gateway、payment method、currency、provider status、failed/refunded timestamps 等對帳欄位。
- 雷門付款已使用正式型 adapter 呼叫查單與全額退款；只有綠界付款 fallback 仍使用 `LocalSandboxRefundAdapter`，不得拿來處理真實退刷。
- 管理端可建立退款，但沒有「重新查詢交易」、交易時間線、provider 原始狀態或人工 reconciliation 介面。
- Payment 建立、狀態查詢及發票載具驗證尚未套 rate limit。

### Invoice

- 汎宇 Sandbox 已實際接受 HMAC-SHA256 envelope、B2C `EG0478`／Email 欄位、`/openInvoice` 與 `/queryInvoice`；NT$100 測試票已成功查回。`FANYU_INVOICE_SIGNATURE_VERIFIED=false` 仍預設拒絕送出，部署時只對已驗收的 Sandbox 憑證明確開啟；正式環境必須另行驗收。
- 已有作廢請求封裝、作廢欄位、折讓與折讓品項資料表；退款完成後會建立持久化調整待辦，全額標成 `void_pending`，但管理端送出、完成同步與會計政策尚未完成。
- 沒有汎宇 `/getNotificationFailList` 的錯誤回收與管理端顯示。

### Email

- 寄送 transport 已統一，但各事件的標題與文案仍散在 jobs／domain service，尚無集中式模板與版本。
- 寄信成功後沒有保存 Resend message ID、實際 provider、accepted time 或 delivery webhook 狀態。
- Outbox 失敗可自動重試，但管理端看不到失敗原因，也不能人工重送。
- `NotificationCommand.dedupe_key` 目前只寫入 JSON，資料庫沒有唯一約束，實作並未真正依 dedupe key 去重；同一商業事件若被重複 publish，可能建立重複站內通知與 Email Outbox。

### UX / Admin

- Checkout 已呈現個人／公司發票、統編、公司名稱、買方 Email 與雲端交付說明；付款方式分步與送出前的獨立最終確認頁仍待完成。
- 訂單頁只顯示摘要狀態，缺少付款、發票、退款、通知的可追蹤時間線。
- 管理端已有訂單履約與退款，但沒有 Payment／Invoice／Outbox 的診斷、重查、作廢、折讓與重送工具。

## Missing：尚未實作

- 部署雷門回跳與主動補查流程，確認 reconciliation 排程實際運作，再完成 Production 低額付款／查詢／全額退款端到端驗收。
- 汎宇單筆明細、錯誤通知清單、作廢／註銷與折讓完整管理流程。
- 發票作廢／折讓的管理端送出與完成同步，以及跨期／部分退款稅務規則。
- 結構化 request／order／payment／invoice correlation logging 與 APM 告警。
- Load test 程式、測試資料隔離、10／50／100／300／500 並發基線與資源圖表。
- `DATABASE_SCHEMA.md`、`LOAD_TEST.md`、`CHANGELOG.md`；雷門付款流程與 Sandbox 驗收清單已建立於 `docs/PAYMENT_FLOW.md`、`docs/RAYGATE_SANDBOX_ACCEPTANCE.md`，發票流程已建立於 `docs/INVOICE_FLOW.md`。

## Bug / Risk：已知或可由程式碼確認的風險

1. **P0 — Render status 255 已定位**：Render log 顯示正式資料庫停在 `0008_meal_customization_options`，部署分支曾缺少對應 revision，Alembic 因 `Can't locate revision` 結束；migration 已恢復，API `/ready` 持續回 200。仍需在搬移資料庫前備份。
2. **P0 — 多個管理列表無分頁**：訂單、入社申請、社員名冊、會籍、團購、餐點等 API 會一次讀取全部資料；資料量增加後會放大 RAM、response size 與 DB 時間。
3. **P0 — 免費方案仍將啟動工作與 web process 綁定**：已加 migration／preview seed 分階段日誌與重試；付費服務才適合再移至獨立 pre-deploy command。
4. **P1 — 退款狀態會先於真實退刷完成**：目前 Sandbox adapter 永遠回傳 completed，正式上線前必須禁止沿用。
5. **P1 — 通知 dedupe 名實不符**：repository 文件聲稱會 deduplicate，但目前沒有查詢或唯一索引。
6. **P1 — Email 供應商回執未落庫**：客服無法判斷 Resend 是否接受、由主用或備援寄出、message ID 為何。
7. **P2 — 汎宇正式環境尚未驗收**：Sandbox 的低額 B2C 開票、查回票號與 Gmail 通知已成功；正式環境仍需專屬憑證並重新完成相同驗收，不可直接沿用測試憑證。
8. **P2 — 發票營運規則未定**：schema 已能保存 B2B、精確送出明細、作廢與折讓待辦；尚缺跨期／部分退款的會計規則及正式環境驗收。
9. **P3 — 單機 rate limit／scheduler state**：多 instance 時每台各自計數，不能提供全域保護或保證只排程一次。

## Security：後續安全工作

- 外部憑證只經環境變數注入，禁止寫入 DB provider response、log、前端 bundle 或 Git。
- 汎宇 `auth` 雖經 Base64，仍等同可還原密碼；所有 request／exception logging 必須對 `auth`、`APIKey`、`signatureValue` 做遮罩。
- 汎宇 `createDateTime` 要求與台北時間相差正負 10 分鐘；伺服器必須維持時鐘同步，每次重試重新產生時間與簽章，不可重送過期 envelope。
- 汎宇 Sandbox 已實際接受現行 HMAC-SHA256 簽章實作；正式環境仍須使用獨立憑證重新驗收，不得把 Sandbox 成功直接當成正式契約保證。
- Payment webhook 必須同時驗證簽章、merchant、平台訂單、供應商交易 ID、金額、幣別與狀態；資料庫唯一鍵是最後一道冪等保護。
- 管理端作廢、折讓、退款、重送與人工更正都必須填原因並寫入 `admin_audits`。
- Rate limit 在 P1 先覆蓋 payment／invoice 高風險路由；多 instance 後再移至 Redis。

## Performance：目前瓶頸假設與驗證方式

以下是待量測假設，不是 Render crash 的既定結論：

- 未分頁列表可能造成大量 ORM object、select-in eager load 與大 response。
- 訂單常用查詢是 `user_id + created_at`；Outbox 常用查詢是 `status + available_at + created_at`。目前多為單欄索引，應先用 PostgreSQL `EXPLAIN (ANALYZE, BUFFERS)` 驗證，再決定複合索引。
- payment reconciliation、invoice query、Email retry 都可能呼叫外部服務；需分別記錄 latency、timeout 與 retry，不可只看整體 API response time。
- 目前 DB pool 沒有指標；壓測時至少記錄 checked-out connections、pool wait、PostgreSQL active／idle connections 與慢查詢。
- Render free tier 的休眠、CPU／RAM 與 PostgreSQL 到期限制會干擾測試；正式容量結論不能只用 free tier 數據。

## 優先級與驗收條件

### P0 — Server Stability

- [~] P0-01 crash log 已確認 Alembic 缺少 `0008` revision 並修復；仍需保存正式事故時間線與 Render instance metrics。
- [~] P0-02 已加入 `request_id`、request duration 與敏感 provider 錯誤遮罩；交易 log 尚未全面帶齊 payment／invoice ID。
- [~] P0-03 DB pool size、overflow、timeout、recycle 與 connect timeout 已環境化；尚缺 pool 使用量監測。
- [ ] P0-04 對訂單、社員、申請、團購、餐點與管理列表加入 cursor／page pagination 及 response 上限。
- [ ] P0-05 以實際 PostgreSQL 查詢計畫驗證 login、user、order、payment、invoice；只新增有證據的複合索引。
- [~] P0-06 已將 liveness 與 DB readiness 分開；待補 migration revision 與 Outbox 指標。
- [ ] P0-07 防止 cron、request 補跑與未來多 instance 重疊執行同一輪 reconciliation；評估 PostgreSQL advisory lock 或獨立 worker。
- [ ] P0-08 建立隔離的 load test，依序跑 10／50／100／300／500；每一階只在 error rate 與資源符合門檻時前進。

P0 完成條件：能說明 status 255 根因或以監控證據排除；50 並發先達成穩定基線；無連線洩漏；CI、migration round-trip 與既有測試全過。

### P1 — Transaction Core / Authentication / Email

- [x] P1-01 已建立可切換的 payment adapter 並保留既有訂單與 callback repository；Production 網址、TLS、正式憑證與查單端點已實測通過。
- [x] P1-02 Migration 已擴充付款 provider 與退款對帳欄位，未另建重複 `orders`。
- [~] P1-03 已實作雷門 create payment、回跳立即查單、登入者補查、排程補查與 refund adapter；正式查單 smoke test 已通過，尚缺線上部署、關頁付款、全額退款演練及部分退款政策。
- [x] P1-04 已覆蓋重複回跳／補查、錯誤金額／交易 ID、查單暫時失敗、late payment、物流後退款與不明退款只查單等冪等測試。
- [ ] P1-05 修正通知 dedupe：建立可索引的 dedupe column／唯一約束，讓站內通知與 Email Outbox 同一交易只建立一次。
- [ ] P1-06 建立集中式 Email template registry，涵蓋驗證、註冊、付款、訂單、交付、完成、退款、發票、作廢與折讓。
- [ ] P1-07 保存 Email provider、message ID、accepted／failed time 與最後錯誤；管理端可查看並重送失敗信件。
- [ ] P1-08 對 payment create／status 與敏感查詢加 rate limit；多 instance 前明確標示單機限制。
- [ ] P1-09 雷門補查期限結束時建立管理員人工對帳待辦，並監控排程連續失敗；完成前不可啟用正式收款。

P1 完成條件：所有付款狀態只由經後端驗證的供應商結果推進；重複回跳、前端補查與排程不重複扣庫存、寄信、開票或退款；Email 驗證既有流程維持全數通過。

### P2 — 汎宇 Electronic Invoice

- [x] P2-01 已取得 WEB API Sandbox 測試憑證，確認營業人及會員載具功能啟用，且實際通過 HMAC-SHA256 envelope 與 `/queryInvoice` 驗證。
- [x] P2-02 已建立汎宇 adapter、envelope、timeout、敏感欄位遮罩及 `EG0478` B2C Email 載具轉換；Sandbox 低額 `/openInvoice`、查回票號與 Gmail 通知均已通過。正式憑證驗收列為上線條件。
- [x] P2-03 Migration 擴充 invoice buyer／payment snapshot，建立 `invoice_items`、`invoice_allowances` 與精確 `provider_request`；發票明細改以六位小數保存。
- [x] P2-04 Checkout 支援個人雲端會員載具、手機條碼、公司統編／名稱／Email，以及公司統編搭配手機載具；前後端均驗證並顯示確認摘要。
- [x] P2-05 後端驗證付款成功後以 Outbox 排入；開票先查 `sales order number`，再呼叫 `/openInvoice`，timeout 後先 `/queryInvoice`，管理端也可填原因重查。
- [ ] P2-06 定期呼叫 `/getNotificationFailList`，保存最近七日未取回失敗並連回 order／invoice；管理端顯示與重查。
- [~] P2-07 退款協調已建立持久化作廢／折讓待辦並通知管理員；尚需實作管理端送出、完成同步、稽核，以及註銷／折讓作廢流程。
- [ ] P2-08 決定是否提供 B2C／B2B PDF；若提供，只由後端取得並做授權，不把汎宇憑證交給前端。

P2 完成條件：個人、公司、手機載具、timeout 補償、重複 job、作廢與折讓均有 adapter contract test、DB integration test 與 sandbox 低額端到端紀錄。

### P3 — Scaling / Monitoring

- [ ] P3-01 依壓測結果決定 Redis；用途分開評估 rate limit、短效 token、cache、queue 與 idempotency，不把 Redis 當交易真相來源。
- [ ] P3-02 把 Outbox consumer 移到獨立 worker，支援多 worker claim、dead-letter、人工 retry 與 queue depth 告警。
- [ ] P3-03 接入 error／performance monitoring，針對 payment webhook、invoice API、DB timeout、failed job 與 Email failure 告警。
- [ ] P3-04 建立交易 reconciliation dashboard 與日常對帳報表。
- [ ] P3-05 資料庫備份與還原依先前決定暫緩，正式收款前需重新排入並完成演練。

### P4 — UX / Admin

完整計畫另列 P4，因此不併入 P3：

- [ ] P4-01 Checkout 分成配送、付款、發票、最終確認，保留目前設計語言。
- [ ] P4-02 訂單詳情新增 Payment／Invoice／Refund／Delivery 時間線與可採取動作。
- [~] P4-03 Admin 已顯示發票狀態並可填原因重查；provider transaction、作廢、折讓與 Email 重送仍待完成。
- [ ] P4-04 所有狀態使用一致中文 label，錯誤訊息不暴露 provider secret 或 stack trace。

## 汎宇文件核對摘要

- 測試機：`https://webtest.einvoice.com.tw/einv/{URL Path}`；正式機：`https://web.einvoice.com.tw/einv/{URL Path}`。
- Request envelope：`companyID`、`userID`、Base64 `auth`、台北時間 `createDateTime`、`signatureValue`、`reqData`。
- `createDateTime` 必須與主機台北時間相差正負 10 分鐘內。
- `/openInvoice` 的 `sellerID + orderID` 必須唯一；可直接作為供應商側冪等鍵。
- `/queryInvoice` 可依 orderID 查回 `invNo / invDate / invTime / randomNumber / status`，是 timeout 後的主要補償機制。
- `/getNotificationFailList` 只回最近七日尚未取回的發票／折讓處理失敗；系統需定期擷取，不能等客服手動發現。
- 文件支援 `/cancelInvoice`、`/voidInvoice`、`/openAllowance`、`/cancelAllowance`、`/voidAllowance`；實際使用時機仍需會計與汎宇確認。
- B2C 會員載具代碼為 `EG0478`，`carrierID1`、`carrierID2` 均使用訂單客人 Email；`notifyEmail` 另負責官方開票通知。
- 手機條碼載具代碼為 `3J0002`，現有 `/XXXXXXX` 格式檢查可保留，但送出欄位需改由汎宇 adapter 轉換。

## Phase Gate 與文件交付

每個 Phase 都必須依序完成：閱讀現況、確認決策、migration、實作、contract／integration test、migration round-trip、regression、文件更新。前一 Phase 未達完成條件，不進下一 Phase。

| 文件 | 現況 | 建議建立時點 |
|---|---|---|
| `TODO.md` | 本文件已建立 | Phase 0 |
| `docs/PAYMENT_FLOW.md` | 已建立 | P1 持續更新 |
| `docs/INVOICE_FLOW.md` | 已建立 | P2 持續更新 |
| `docs/DATABASE_SCHEMA.md` | 缺少 | P1 migration 定稿後 |
| `docs/DEPLOYMENT.md` | 已存在，需持續更新 | 每個 Phase |
| `docs/LOAD_TEST.md` | 缺少 | P0 首輪壓測後 |
| `CHANGELOG.md` | 缺少 | 從第一個實作 Phase 開始 |

## 2026-09-04 驗證基線

- Backend：325 passed。
- Frontend／契約：23 passed。
- TypeScript：通過。
- Vite production build：通過。
- Migration：全新資料庫 `upgrade head`、`downgrade 0008`、再 `upgrade head`，以及完整 `downgrade base -> upgrade head` 均通過。
- UI：電子發票 Checkout 已以 1280px 桌面與 390px 手機寬度實測；公司欄位正常換行，頁面無水平溢位。
- 本基線只能證明現有自動測試涵蓋的行為正常，不能取代 Render crash logs、provider sandbox 或 load test。
