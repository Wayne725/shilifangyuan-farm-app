# 十里方圓部署手冊

## 服務組成

- `shilifangyuan-web`：Vite React 靜態網站
- `shilifangyuan-api`：FastAPI 公開 HTTPS API
- `shilifangyuan-db`：PostgreSQL
- GitHub Actions：每 10 分鐘執行一次期限、付款、退款、發票與通知結算

根目錄的 `render.yaml` 已定義三個 Render 資源。免費 Web Service 閒置後會休眠，免費 PostgreSQL 會在建立 30 天後到期，因此免費方案只適合展示與測試；正式營運需使用持久化資料庫、備份與不中斷服務方案。

目前 Blueprint 預設使用 `APP_ENV=preview`：只建立虛擬展示資料，金流、物流、Email 與真實證件上傳維持停用。Preview 仍要求平台產生強 JWT／reconciliation 密鑰，個資展示欄位的加密金鑰會由 JWT 密鑰穩定衍生；升級 Sandbox 或 Production 前必須改用獨立的 `PII_ENCRYPTION_KEYS_JSON`。

## 建立 Render Blueprint

1. 將專案推送到私人 GitHub repository。
2. 在 Render 選擇 **New → Blueprint**，連接該 repository。
3. 若 Render 因名稱已被使用而更改服務網址，將 API 的公開網址填入：
   - API 服務的 `APP_BASE_URL`
   - Web 服務的 `VITE_API_BASE_URL`
4. 將 Web 公開網址填入 API 服務的 `WEB_BASE_URL`。
5. 設定三組不同的強展示密碼；部署版不可沿用 README 的範例密碼。
6. 重新部署 API 與 Web。

Cloudflare R2、Email 與綠界憑證只在升級成整合測試 Sandbox 時才需要設定。

`VITE_API_BASE_URL` 必須填 FastAPI 的公開 HTTPS 根網址，不含 `/v1`。新版 Web 在本機由 Vite 代理 `/v1`，正式靜態站則直接呼叫這個公開網址。

`APP_BASE_URL` 必須是綠界可連線的公開 HTTPS API 根網址，供付款 `ReturnURL`／`OrderResultURL` 與物流 `ClientReplyURL` 使用；`WEB_BASE_URL` 必須是公開 HTTPS Web 根網址，供後端完成驗證後導回訂單或社員頁。兩者都不可填入 localhost、內網網址或額外路徑。

## 必要環境變數

### FastAPI

- `DATABASE_URL`
- `APP_BASE_URL`
- `WEB_BASE_URL`
- `JWT_SECRET`
- `DEMO_ADMIN_PASSWORD`
- `DEMO_MEMBER_PASSWORD`
- `DEMO_NONMEMBER_PASSWORD`
- `DEMO_RESET_CONFIRMATION`
- `RECONCILE_SECRET`

`JWT_SECRET` 與 `RECONCILE_SECRET` 至少使用 32 字元；`DEMO_RESET_CONFIRMATION` 至少 8 字元，並與三組登入密碼不同。

### 綠界 AIO Stage

- `ECPAY_PAYMENT_MERCHANT_ID`
- `ECPAY_PAYMENT_HASH_KEY`
- `ECPAY_PAYMENT_HASH_IV`
- `ECPAY_PAYMENT_STAGE=true`

### 綠界 B2C 電子發票 Stage

- `ECPAY_INVOICE_MERCHANT_ID`
- `ECPAY_INVOICE_HASH_KEY`
- `ECPAY_INVOICE_HASH_IV`
- `ECPAY_INVOICE_STAGE=true`

金流與電子發票是不同的測試商店，MerchantID、HashKey、HashIV 不可混用。

### 綠界全方位物流 Stage

- `ECPAY_LOGISTICS_MERCHANT_ID`
- `ECPAY_LOGISTICS_HASH_KEY`
- `ECPAY_LOGISTICS_HASH_IV`
- `ECPAY_LOGISTICS_PLATFORM_ID`（非特店平台可留空）
- `ECPAY_LOGISTICS_STAGE=true`
- `ECPAY_LOGISTICS_SENDER_NAME`
- `ECPAY_LOGISTICS_SENDER_ZIP_CODE`
- `ECPAY_LOGISTICS_SENDER_ADDRESS`

物流 Stage 密鑰與 AIO／發票密鑰分開。正式寄件資料尚未決定，Sandbox 只能填合作社同意使用的測試資料。

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
- `Content-Length` 不會納入簽名 Header，避免瀏覽器無法手動設定而導致簽名失敗；後端仍會在確認上傳時比對實際大小、Content-Type 與 SHA-256 metadata。

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

`EMAIL_FROM_EMAIL` 必須屬於 Resend 已驗證的寄件網域；啟用備援時，`MAILERSEND_FROM_EMAIL` 也必須屬於 MailerSend 已驗證網域。若同時設定兩組 API Key，系統先使用 Resend，失敗時才改用 MailerSend；只設定舊 MailerSend 三個變數的部署仍可運作。綠界發票 Stage 不接受真實 Email；真實收件地址只傳給寄信供應商。
Sandbox 與 Production 都會在啟動時檢查至少一組供應商 API Key 與寄件地址，未設定時部署會直接失敗並列出缺少的變數。

## GitHub Actions

Repository Settings → Secrets and variables → Actions 新增：

- `RECONCILE_URL`：`https://<api-host>/internal/reconcile`
- `RECONCILE_SECRET`：與 Render API 使用相同值

`.github/workflows/reconcile.yml` 每 10 分鐘喚醒 API 並執行結算，也可由 Actions 頁面手動執行。

## 展示前檢查

1. 確認 PostgreSQL 尚未超過 30 天期限。
2. 確認 Alembic 已升級至最新 migration，既有 customer 已取得 `SLF-C` 且管理員沒有一般買家編號；若資料庫已重建，也確認 seed 執行成功。
3. 在社務管理的「社員名冊」確認正式既有社員已匯入，並以一筆測試名冊完成帳號認領與 Email 驗證。
4. 開啟 `/health` 暖機，避免教授等待 Render 冷啟動。
5. 登入正式社員、一般買家與管理員帳號各一次，並驗證兩款入社款項完成後會先成為實習社員。
6. 使用綠界測試卡完成一筆付款，確認 Web 回到正確訂單且顯示付款結果。
7. 確認 GitHub Actions 最近一次 reconciliation 成功。
8. 執行管理員「重設展示資料」，恢復補件申請、待付款入社、接近額滿活動、記名提案、便當待取與配送中訂單。
9. 確認 Resend 寄件網域仍為 verified；若啟用備援，也確認 MailerSend 網域狀態。
10. 使用測試檔驗證 R2 上傳、管理員短效查看 URL 及 Demo reset 刪除。
11. 以綠界物流 Stage 完成一次通路選擇與建單，確認選擇完成後可回訂單並繼續付款。
12. 以桌機與手機尺寸各完成一次付款與物流流程，確認後端會導回 Web 的訂單頁。

## 正式營運上線條件

1. PostgreSQL 使用付費持久化方案，設定每日備份、還原演練與資料保留期限。
2. `APP_ENV=production`，綠界金流、發票、物流三組 Stage 旗標改為 `false`，使用正式合約提供的三組獨立商店密鑰，並將所有 `ECPAY_*_URL` 改成綠界當期正式端點；預設值仍是 Stage 網址。
3. `APP_BASE_URL`、`WEB_BASE_URL`、`VITE_API_BASE_URL` 全部使用正式 HTTPS 網域；確認綠界可連入付款、物流與發票 callback。
4. 設定正式寄件人姓名、郵遞區號與地址，完成綠界物流測試單、列印託運單、貨態回傳與異常件處理。
5. 驗證 Email 寄件網域的 SPF、DKIM、DMARC，實測註冊驗證、密碼重設、訂單與社務通知。
6. R2 Bucket 維持私有，只允許正式 Web Origin PUT；設定證件保存、刪除、調閱權限與個資事件處理程序。
7. 將 JWT、PII、reconciliation 與第三方密鑰放在平台 Secret 管理中，完成輪替流程，不沿用任何展示密碼。
8. 啟用錯誤監控、服務存活監控、付款／物流 reconciliation 告警、管理員稽核紀錄保存與流量限制。
9. 以一筆低額真實訂單完成付款、發票、物流、Email、取消與退款的端到端驗收，再開放一般使用者。
10. 匯入正式社員名冊，抽樣核對加密欄位、重複社員編號、已認領狀態及 Email／手機異動處理後，再開放既有社員註冊。

正式上線不只是「架後端與資料庫」：前兩者讓資料可持久化；金流、物流、Email 還各自需要正式合約、驗證網域、公開 callback、密鑰與營運流程。

## 手機展示相容性

- 使用同一個 Render Web 網址，不需安裝 App。
- 上線前以 iPhone Safari、Android Chrome 及桌機瀏覽器驗證登入、結帳、物流返回與 QR 顯示。
- 付款與物流託管頁完成後由後端導回公開 `WEB_BASE_URL`，再重新查詢最終狀態。

## Sandbox 限制

- AIO Stage 不動真實款項。
- AIO Stage 沒有可實際測試的信用卡退款 API；退款完成只代表本系統 Sandbox 狀態、庫存及通知已完成。
- B2C 發票 Stage 不會送財政部，也不會寄綠界官方發票信。
- 物流 Stage 不會自動模擬出貨後的貨態通知；後台手動推進只能用於 Sandbox。
- Sandbox 證件頁禁止上傳真實證件。
- `ChoosePayment=Credit` 在部分 iOS 環境仍可能顯示 Apple Pay。
