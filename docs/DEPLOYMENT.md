# 十里方圓部署手冊

## 服務組成

- `shilifangyuan-web`：Vite React 靜態網站
- `shilifangyuan-api`：FastAPI 公開 HTTPS API
- `shilifangyuan-db`：PostgreSQL
- GitHub Actions：每 10 分鐘執行一次期限、付款、退款、發票與通知結算

根目錄的 `render.yaml` 已定義三個 Render 資源。免費 Web Service 閒置後會休眠，免費 PostgreSQL 會在建立 30 天後到期，因此免費方案只適合展示與測試；正式營運需使用持久化資料庫、備份與不中斷服務方案。

目前 Blueprint 預設使用 `APP_ENV=preview`：只建立虛擬展示資料，預設會明確拒絕建立或開啟任何線上付款頁，物流與真實證件上傳也維持停用。金流 provider 預設選用雷門，但 `RAYGATE_PAYMENT_*` 只提供空白的 `sync: false` 佔位，不含規格文件裡的範例憑證。唯一例外是人工設定 `RAYGATE_PAYMENT_ACCEPTANCE_ORDER_ID` 的正式 NT$10 單筆驗收；它只允許精確對應的合作社取貨訂單，且完成後必須立即清空。Preview 必須填妥 Resend，否則 API 會拒絕啟動；同時仍要求平台產生強 JWT／reconciliation 密鑰與三組非預設展示密碼。升級 Sandbox 或 Production 前必須改用獨立的 `PII_ENCRYPTION_KEYS_JSON`。

## 建立 Render Blueprint

1. 將專案推送到私人 GitHub repository。
2. 在 Render 選擇 **New → Blueprint**，連接該 repository。
3. 若 Render 因名稱已被使用而更改服務網址，將 API 的公開網址填入：
   - API 服務的 `APP_BASE_URL`
   - Web 服務的 `VITE_API_BASE_URL`
4. 將 Web 公開網址填入 API 服務的 `WEB_BASE_URL`。
5. 設定三組不同的強展示密碼；部署版不可沿用 README 的範例密碼。
6. 重新部署 API 與 Web。

Cloudflare R2、選定的金流憑證與綠界物流憑證只在升級成整合測試 Sandbox 時才需要設定；Resend 從 Preview 起就是必要設定。

API 啟動時會先執行 `python -m app.startup prepare`。Migration 與 Preview seed 遇到暫時性資料庫錯誤時會指數退避重試，日誌會以 `startup_stage=migration` 或 `startup_stage=preview_seed` 標示失敗階段。Web 與 API 設為 GitHub checks 通過後才自動部署；`codex/v2-social-commerce` 分支因此也納入 push CI。

`VITE_API_BASE_URL` 必須填 FastAPI 的公開 HTTPS 根網址，不含 `/v1`。新版 Web 在本機由 Vite 代理 `/v1`，正式靜態站則直接呼叫這個公開網址。

`APP_BASE_URL` 必須是金流與綠界物流都可連線的公開 HTTPS API 根網址，供付款瀏覽器 return URL、相容 callback 欄位與物流 `ClientReplyURL` 使用；`WEB_BASE_URL` 必須是公開 HTTPS Web 根網址，供後端完成驗證後導回訂單或社員頁。兩者都不可填入 localhost、內網網址或額外路徑。

## 必要環境變數

### FastAPI

- `DATABASE_URL`
- `APP_ENV`（只能是 `development`、`test`、`preview`、`sandbox`、`production`）
- `APP_BASE_URL`
- `WEB_BASE_URL`
- `PAYMENT_PROVIDER`（`raygate` 或 `ecpay`）
- `JWT_SECRET`
- `DEMO_ADMIN_PASSWORD`
- `DEMO_MEMBER_PASSWORD`
- `DEMO_NONMEMBER_PASSWORD`
- `DEMO_RESET_CONFIRMATION`
- `RECONCILE_SECRET`

資料庫連線與啟動參數已有保守預設值，可依 Render PostgreSQL 方案調整：

- `DATABASE_POOL_SIZE=5`
- `DATABASE_MAX_OVERFLOW=5`
- `DATABASE_POOL_TIMEOUT_SECONDS=10`
- `DATABASE_POOL_RECYCLE_SECONDS=300`
- `DATABASE_CONNECT_TIMEOUT_SECONDS=10`
- `DATABASE_READINESS_TIMEOUT_SECONDS=3`
- `STARTUP_MIGRATION_ATTEMPTS=5`
- `STARTUP_MIGRATION_RETRY_SECONDS=2`

`JWT_SECRET` 與 `RECONCILE_SECRET` 至少使用 32 字元；`DEMO_RESET_CONFIRMATION` 至少 8 字元，並與三組登入密碼不同。
`.env.example` 中這兩個密鑰只是後端明確拒絕的本機開發預設值；Preview、Sandbox 與 Production 都必須改用各自獨立的隨機值。

### 雷門線上多元支付 Sandbox

先向雷門取得本專案專用的 Sandbox 資料，再設定：

- `PAYMENT_PROVIDER=raygate`
- `RAYGATE_PAYMENT_BASE_URL`（雷門提供的 Sandbox HTTPS 根網址，系統不猜測預設值）
- `RAYGATE_PAYMENT_ALLOWED_HOSTNAME`（與 Base URL 完全相同的主機名稱，不含 `https://`、port 或 path）
- `RAYGATE_PAYMENT_STORE_IDENTIFIER`
- `RAYGATE_PAYMENT_KEY_HEX`（64 位十六進位字元，解碼後 32 bytes）
- `RAYGATE_PAYMENT_IV_HEX`（32 位十六進位字元，解碼後 16 bytes）
- `RAYGATE_PAYMENT_MERCHANT_ID`
- `RAYGATE_PAYMENT_TERMINAL_ID`
- `RAYGATE_PAYMENT_DEVICE_TYPE`（選填；只有雷門明確核發設備代碼時才設定）
- `RAYGATE_PAYMENT_STAGE=true`
- `RAYGATE_PAYMENT_CONTRACT_VERIFIED=false`（Sandbox 驗收期間維持關閉）
- `RAYGATE_PAYMENT_RECONCILE_HOURS=24`（逾期後仍主動補查的期間）
- `RAYGATE_PAYMENT_ACCEPTANCE_ORDER_ID`（平時留空；僅限 Preview 的指定 NT$10 合作社取貨訂單驗收）

`RAYGATE_PAYMENT_STAGE` 只是防止把測試／正式設定放錯環境的安全閘門，不會自行切換 URL。Base URL 必須是公開 HTTPS 根網址，且 hostname 必須與 allowlist 設定完全一致。商店識別、Key、IV、Merchant ID、Terminal ID 與 Base URL 都必須來自雷門，禁止使用介接規格內的範例值。

需向雷門登記並測試的公開入口：

- 瀏覽器導回：`{APP_BASE_URL}/payments/raygate/result`
- 付款 callback 相容欄位：`{APP_BASE_URL}/webhooks/raygate/payment`（雷門目前不會主動呼叫，平台不依賴）

雷門規格將 `callback_url` 與 `return_url` 限制為 120 字元。目前導回網址會再加上 36 字元的 payment attempt ID，因此 `APP_BASE_URL` 不得超過 48 字元；正式登記 callback 前應以實際網域組出完整網址確認長度。

雷門已確認目前沒有主動通知。瀏覽器導回會立即觸發 server-to-server 查單，但不會直接相信前端結果；未回站或尚未取得最終結果時，由前端有限補查與排程繼續收斂。正式啟用前還必須確認非終態最晚收斂時間、建議查單頻率、全額退款、重複退款與正式對帳方式；詳見 `docs/PAYMENT_FLOW.md`。

Preview 單筆驗收只有在正式雷門設定完整、`RAYGATE_PAYMENT_STAGE=false`、指定訂單 UUID、訂單總額為 NT$10 且履約方式為合作社取貨時才會放行；會員款項與其他訂單仍會回覆拒絕付款。操作細節與收尾清單見 `docs/RAYGATE_SANDBOX_ACCEPTANCE.md`。

### 綠界 AIO Stage

只有在保留或切回綠界付款時才設定：

- `PAYMENT_PROVIDER=ecpay`
- `ECPAY_PAYMENT_MERCHANT_ID`
- `ECPAY_PAYMENT_HASH_KEY`
- `ECPAY_PAYMENT_HASH_IV`
- `ECPAY_PAYMENT_STAGE=true`
- `ECPAY_PAYMENT_AIO_URL`
- `ECPAY_PAYMENT_QUERY_URL`

### 綠界 B2C 電子發票 Stage

- `ECPAY_INVOICE_MERCHANT_ID`
- `ECPAY_INVOICE_HASH_KEY`
- `ECPAY_INVOICE_HASH_IV`
- `ECPAY_INVOICE_STAGE=true`
- `ECPAY_INVOICE_ISSUE_URL`
- `ECPAY_INVOICE_QUERY_URL`
- `ECPAY_INVOICE_BARCODE_URL`

金流與電子發票是不同的測試商店，MerchantID、HashKey、HashIV 不可混用。

### 汎宇雲端電子發票 Sandbox

切換前先執行 `alembic upgrade head`，並確認至少已套用 `0011_invoice_provider_snapshot`，再設定：

- `INVOICE_PROVIDER=fanyu`
- `FANYU_INVOICE_COMPANY_ID`
- `FANYU_INVOICE_USER_ID`
- `FANYU_INVOICE_AUTH_PASSWORD`
- `FANYU_INVOICE_API_KEY`
- `FANYU_INVOICE_SELLER_ID`
- `FANYU_INVOICE_STAGE=true`
- `FANYU_INVOICE_BASE_URL=https://webtest.einvoice.com.tw/einv`
- `FANYU_INVOICE_SIGNATURE_VERIFIED=false`

最後一個旗標是安全閘門。2026-09-04 已用指定 WEB API Sandbox 憑證完成 HMAC-SHA256、NT$100 B2C 開票與銷貨單查回驗收；只有部署完全相同的 Sandbox 憑證與端點時才可明確改成 `true`，其他環境維持 `false`。憑證只能填在 Render Environment，禁止寫入 Git、log 或管理端畫面。

汎宇入口網站、WEB API 憑證、APIKey 與 POS 通報金鑰用途不同，不可互相代用；Render 只設定供應商指定並已通過驗收的 WEB API 憑證。

完整開立、銷貨單查詢補償、Email 分工與未啟用項目見 `docs/INVOICE_FLOW.md`。

### 綠界全方位物流 Stage

- `ECPAY_LOGISTICS_MERCHANT_ID`
- `ECPAY_LOGISTICS_HASH_KEY`
- `ECPAY_LOGISTICS_HASH_IV`
- `ECPAY_LOGISTICS_PLATFORM_ID`（非特店平台可留空）
- `ECPAY_LOGISTICS_STAGE=true`
- `ECPAY_LOGISTICS_SELECTION_URL`
- `ECPAY_LOGISTICS_UPDATE_TEMP_URL`
- `ECPAY_LOGISTICS_CREATE_URL`
- `ECPAY_LOGISTICS_QUERY_URL`
- `ECPAY_LOGISTICS_PRINT_URL`
- `ECPAY_LOGISTICS_SENDER_NAME`
- `ECPAY_LOGISTICS_SENDER_ZIP_CODE`
- `ECPAY_LOGISTICS_SENDER_ADDRESS`

物流 Stage 密鑰與 AIO／發票密鑰分開。正式寄件資料尚未決定，Sandbox 只能填合作社同意使用的測試資料。
上述 ECPay `*_URL` 在程式與 `.env.example` 中預設為 Stage；切換 Production 時必須逐項改為綠界提供的正式 HTTPS 端點。只將 `*_STAGE=false` 不會自動切換網址，且 API 會拒絕在 Production 使用 Stage 端點。

### Cloudflare R2 與 PII

- `CLOUDFLARE_R2_ACCOUNT_ID`
- `CLOUDFLARE_R2_ACCESS_KEY_ID`
- `CLOUDFLARE_R2_SECRET_ACCESS_KEY`
- `CLOUDFLARE_R2_BUCKET`
- `PII_ENCRYPTION_KEYS_JSON`
- `PII_ENCRYPTION_CURRENT_VERSION=v1`

`PII_ENCRYPTION_KEYS_JSON` 格式：

```json
{"v1":"<Base64 編碼的 32-byte 金鑰>"}
```

輪替時新增版本並切換 `PII_ENCRYPTION_CURRENT_VERSION`，舊版本需保留到資料完成重加密。

#### R2 Bucket CORS

Web 版會從瀏覽器直接以簽名 URL 上傳測試證件。部署 Web 前，必須在 Cloudflare R2 Bucket 的 **Settings → CORS Policy** 加入下列設定，並把 Render 網址換成實際的 Web 網域：

```json
[
  {
    "AllowedOrigins": [
      "https://<your-render-static-site>.onrender.com",
      "http://127.0.0.1:4173"
    ],
    "AllowedMethods": ["PUT"],
    "AllowedHeaders": ["Content-Type", "x-amz-meta-sha256"],
    "ExposeHeaders": ["ETag"],
    "MaxAgeSeconds": 3600
  }
]
```

- 正式展示網址要完整符合 Origin（包含 `https`，不含路徑），正式環境不要使用 `*`。
- Bucket 必須維持私有，不可啟用公開網域或 `r2.dev`；檔案讀取只走後端產生的兩分鐘簽名 URL。
- `Content-Length` 會納入 PUT 簽章，由瀏覽器依檔案自動送出；後端確認時還會從私有 Bucket 讀回檔案，驗證實際大小、JPEG／PNG／PDF 檔頭與 SHA-256，不只相信瀏覽器提供的 metadata。
- 上傳先進入 `membership-documents/pending/`；驗證完成後，後端以 ETag 條件複製到 `membership-documents/verified/` 並刪除原檔，舊 PUT 網址無法覆寫正式證件。Bucket 應另設 lifecycle，至少每日清除超過 24 小時的 `pending/` 孤兒物件。
- Web CSP 已允許標準 `*.r2.cloudflarestorage.com` endpoint；若改用自訂 R2 endpoint，也要同步加入 `render.yaml` 的 `connect-src`。

### Email（Resend 主用、MailerSend 備援）

使用 Resend 作為主用供應商時設定：

- `RESEND_API_KEY`
- `EMAIL_FROM_EMAIL`
- `EMAIL_FROM_NAME=十里方圓`

`render.yaml` 已將三個欄位加入 API 服務；`RESEND_API_KEY` 與
`EMAIL_FROM_EMAIL` 使用 `sync: false`，建立或同步 Blueprint 時必須由
管理者在 Render 填入，不會進入 Git。

選配的 MailerSend 備援／舊部署相容變數：

- `MAILERSEND_API_TOKEN`
- `MAILERSEND_FROM_EMAIL`
- `MAILERSEND_FROM_NAME=十里方圓`

`EMAIL_FROM_EMAIL` 必須屬於 Resend 已驗證的寄件網域；啟用備援時，`MAILERSEND_FROM_EMAIL` 也必須屬於 MailerSend 已驗證網域。若同時設定兩組 API Key，系統先使用 Resend，失敗時才改用 MailerSend。Preview 與 Production 強制要求 Resend；MailerSend 只能作為備援，不能取代 Resend。綠界發票 Stage 不接受真實 Email；真實收件地址只傳給寄信供應商。
所有遠端環境都會在啟動時檢查寄信供應商與寄件地址，未設定時部署會直接失敗並列出缺少的變數。OTP 與密碼重設憑證在 Outbox 內以 AES-GCM 加密，寄送完成或永久失敗後即移除。

## GitHub Actions

Repository Settings → Secrets and variables → Actions 新增：

- `RECONCILE_URL`：`https://<api-host>/internal/reconcile`
- `RECONCILE_SECRET`：與 Render API 使用相同值

`.github/workflows/reconcile.yml` 每 10 分鐘喚醒 API 並執行結算，也可由 Actions 頁面手動執行。
兩個 GitHub Secrets 未設定時，workflow 會明確失敗；雷門沒有主動通知，因此不得關閉此排程或忽略失敗告警。

## 展示前檢查

1. 確認 PostgreSQL 尚未超過 30 天期限。
2. 確認 Alembic 已升級至最新 migration，既有 customer 已取得 `SLF-C` 且管理員沒有一般買家編號；若資料庫已重建，也確認 seed 執行成功。
3. 在社務管理的「社員名冊」確認正式既有社員已匯入，並以一筆測試名冊完成帳號認領與 Email 驗證。
4. 開啟 `/health` 暖機，並確認 `/ready` 回傳 200；`/ready` 會實際檢查資料庫連線。
5. 登入正式社員、一般買家與管理員帳號各一次，並驗證兩款入社款項完成後會先成為實習社員。
6. 使用所選金流的測試資料完成一筆付款，確認 Web 回到正確訂單且 server-to-server 查單後顯示付款結果；使用雷門時再完成一次「付款後關閉瀏覽器」，確認排程仍會入帳。
7. 確認 GitHub Actions 最近一次實際執行 `Reconcile deadlines, payments, invoices, and notifications`，並成功取得 API 回應；不可只確認 workflow 綠燈或略過步驟。
8. 若使用本機 Development 或 Sandbox，執行管理員「重設展示資料」；公開 Preview 與 Production 不提供重設。
9. 確認 Resend 寄件網域仍為 verified；若啟用備援，也確認 MailerSend 網域狀態。
10. 使用測試檔驗證 R2 上傳、管理員短效查看 URL 及 Demo reset 刪除。
11. 以綠界物流 Stage 完成一次通路選擇與建單，確認選擇完成後可回訂單並繼續付款。
12. 以桌機與手機尺寸各完成一次付款與物流流程，確認後端會導回 Web 的訂單頁。

## Render 失敗排查

`/health` 是 liveness，只確認 FastAPI process 已能服務；`/ready` 另以短逾時檢查 PostgreSQL。Render health check 使用 `/ready`，因此資料庫不可用時會直接反映為服務未就緒；排查時仍應分別請求兩個端點，以區分 process 與資料庫問題。

收到 `Exited with status 255` 時：

1. 在 Render API 服務的 **Logs** 依事故時間向前後各保留 100–200 行，先找最後一個 `startup_stage`。
2. 若停在 `migration`，檢查 PostgreSQL 是否仍存在、`DATABASE_URL` 是否有效、連線數是否已滿；不要反覆手動重跑可能具副作用的 seed。
3. 若停在 `preview_seed`，保留例外與 migration revision；seed 採固定自然鍵，可安全重新部署，但仍應先確認資料庫約束錯誤。
4. 若 migration 與 seed 都完成，再找 `request_failed request_id=...` 或 Uvicorn shutdown／OOM／SIGTERM 訊息。
5. 分別請求 `/health` 與 `/ready`：前者失敗代表 process／平台問題；只有後者 503 代表 API 活著但資料庫不可用。
6. 使用回應的 `X-Request-ID` 對照 Render log，追查同一筆使用者請求。不要把 JWT、Cookie、付款密鑰或完整個資貼進 issue。

前端靜態圖檔與商品資料是兩條路徑。若 `/products/rice.jpg` 正常但商品清單空白，先檢查 API 與 `/ready`；前端會在 API 12 秒無回應後顯示「重新連線」，不再永久停在載入狀態。

## 正式營運上線條件

1. PostgreSQL 使用付費持久化方案，設定每日備份、還原演練與資料保留期限。
2. `APP_ENV=production`。使用雷門付款時設定 `PAYMENT_PROVIDER=raygate`、正式 `RAYGATE_PAYMENT_BASE_URL`、一致的 `RAYGATE_PAYMENT_ALLOWED_HOSTNAME` 與正式商店憑證，將 `RAYGATE_PAYMENT_STAGE=false`，並且只在完成回跳查單、定時補查、狀態與退款契約驗收後設定 `RAYGATE_PAYMENT_CONTRACT_VERIFIED=true`；未確認時 API 會拒絕啟動。使用綠界付款則將 `ECPAY_PAYMENT_STAGE=false` 並更換兩個 `ECPAY_PAYMENT_*_URL`。綠界物流一律設定 `ECPAY_LOGISTICS_STAGE=false` 並更換五個 `ECPAY_LOGISTICS_*_URL`。發票若使用綠界，設定 `ECPAY_INVOICE_STAGE=false` 並更換三個 `ECPAY_INVOICE_*_URL`；若使用汎宇，設定 `INVOICE_PROVIDER=fanyu`、`FANYU_INVOICE_STAGE=false`、`FANYU_INVOICE_BASE_URL=https://web.einvoice.com.tw/einv`，取得正式環境專屬憑證並重新完成低額開票、查回與通知驗收後，才設 `FANYU_INVOICE_SIGNATURE_VERIFIED=true`。
3. `APP_BASE_URL`、`WEB_BASE_URL`、`VITE_API_BASE_URL` 全部使用正式 HTTPS 網域；確認瀏覽器能回到平台，並確認需要主動通知的綠界物流與發票服務可連入各自 callback。雷門另須確認狀態、補查頻率、退款與正式對帳契約。
   Web 與 API 最好使用同一自有主網域下的子網域，並實測瀏覽器未封鎖 HttpOnly refresh Cookie。
   Refresh token 由資料庫 session 管理且每次換發即撤銷舊 token；部署 migration 前不可先啟動新版 API。
4. 設定正式寄件人姓名、郵遞區號與地址，完成綠界物流測試單、列印託運單、貨態回傳與異常件處理。
5. 驗證 Email 寄件網域的 SPF、DKIM、DMARC，實測註冊驗證、密碼重設、訂單與社務通知。
6. R2 Bucket 維持私有，只允許正式 Web Origin PUT；設定證件保存、刪除、調閱權限與個資事件處理程序。
7. 將 JWT、PII、reconciliation 與第三方密鑰放在平台 Secret 管理中，完成輪替流程，不沿用任何展示密碼。
8. 啟用錯誤監控、服務存活監控、付款／物流 reconciliation 告警、管理員稽核紀錄保存與流量限制。
9. 以一筆低額真實訂單完成付款、發票、物流、Email、取消與退款的端到端驗收，再開放一般使用者。
10. 匯入正式社員名冊，抽樣核對加密欄位、重複社員編號、已認領狀態及 Email／手機異動處理後，再開放既有社員註冊。
11. 確認 Production 啟動時不執行 Demo seed，且資料庫不存在展示帳號；CI 的 `pip-audit`、migration 往返與前後端測試均通過。

正式上線不只是「架後端與資料庫」：前兩者讓資料可持久化；金流、物流、Email 還各自需要正式合約、驗證網域、必要的公開入口、密鑰與營運流程。

## 手機展示相容性

- 使用同一個 Render Web 網址，不需安裝 App。
- 上線前以 iPhone Safari、Android Chrome 及桌機瀏覽器驗證登入、結帳、物流返回與 QR 顯示。
- 付款與物流託管頁完成後由後端導回公開 `WEB_BASE_URL`，再重新查詢最終狀態。

## Sandbox 限制

- 雷門沒有主動通知，且 v1.6.5 未完整定義所有非終態的最晚收斂時間、建議查單頻率與退款 idempotency；完成低額實測與正式對帳流程前不可轉正式收款。
- AIO Stage 不動真實款項。
- AIO Stage 沒有可實際測試的信用卡退款 API；退款完成只代表本系統 Sandbox 狀態、庫存及通知已完成。
- B2C 發票 Stage 不會送財政部，也不會寄綠界官方發票信。
- 物流 Stage 不會自動模擬出貨後的貨態通知；後台手動推進只能用於 Sandbox。
- Sandbox 證件頁禁止上傳真實證件。
- `ChoosePayment=Credit` 在部分 iOS 環境仍可能顯示 Apple Pay。
