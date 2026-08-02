# 十里方圓 Sandbox 部署手冊

## 服務組成

- `shilifangyuan-web`：Expo Web 靜態網站
- `shilifangyuan-api`：FastAPI 公開 HTTPS API
- `shilifangyuan-db`：PostgreSQL
- GitHub Actions：每 10 分鐘執行一次期限、付款、退款、發票與通知結算

根目錄的 `render.yaml` 已定義三個 Render 資源。免費 Web Service 閒置後會休眠，免費 PostgreSQL 會在建立 30 天後到期，因此此設定只適合展示與測試。

## 建立 Render Blueprint

1. 將專案推送到私人 GitHub repository。
2. 在 Render 選擇 **New → Blueprint**，連接該 repository。
3. 建立服務後，將 API 的公開網址填入：
   - API 服務的 `APP_BASE_URL`
   - Web 服務的 `EXPO_PUBLIC_API_URL`
4. 將 Web 公開網址填入 API 服務的 `WEB_BASE_URL`。
5. 建立不公開且未啟用 `r2.dev` 的 Cloudflare R2 Bucket。
6. 設定三組不同的強密碼、管理員重設確認碼及所有外部服務密鑰；部署版不可沿用 README 的範例管理員密碼。
7. 重新部署 API 與 Web。

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
      "http://localhost:8081"
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

### SendGrid

- `SENDGRID_API_KEY`
- `SENDGRID_FROM_EMAIL`
- `SENDGRID_FROM_NAME=十里方圓`

寄件地址必須先完成 Single Sender Verification。綠界發票 Stage 不接受真實 Email；真實收件地址只傳給 SendGrid。
Sandbox 會在啟動時檢查 API Key 與寄件地址，未設定時 Render 部署會直接失敗並列出缺少的變數。

## GitHub Actions

Repository Settings → Secrets and variables → Actions 新增：

- `RECONCILE_URL`：`https://<api-host>/internal/reconcile`
- `RECONCILE_SECRET`：與 Render API 使用相同值

`.github/workflows/reconcile.yml` 每 10 分鐘喚醒 API 並執行結算，也可由 Actions 頁面手動執行。

## 展示前檢查

1. 確認 PostgreSQL 尚未超過 30 天期限。
2. 若資料庫已重建，確認 Alembic migration 與 seed 執行成功。
3. 開啟 `/health` 暖機，避免教授等待 Render 冷啟動。
4. 登入社員、非社員與管理員帳號各一次。
5. 使用綠界測試卡完成一筆付款。
6. 確認 GitHub Actions 最近一次 reconciliation 成功。
7. 執行管理員「重設展示資料」，恢復補件申請、待付款入社、接近額滿活動、記名提案、便當待取與配送中訂單。
8. 確認 SendGrid 寄件者仍為 verified。
9. 使用測試檔驗證 R2 上傳、管理員短效查看 URL 及 Demo reset 刪除。
10. 以綠界物流 Stage 完成一次通路選擇與建單。

## 手機展示相容性

- 現場主路徑使用 Render Web，付款後可自動回到訂單頁。
- Expo SDK 57 可搭配對應版本 Expo Go 測試 Android 裝置／模擬器及 iOS 模擬器。
- 目前實體 iPhone 無法側載舊版或指定 SDK 的 Expo Go；若一定要原生展示，請事先製作 development build。
- 手機原生付款以系統瀏覽器開啟，回到 App 後重新整理付款狀態。

## Sandbox 限制

- AIO Stage 不動真實款項。
- AIO Stage 沒有可實際測試的信用卡退款 API；App 的退款完成代表本系統 Sandbox 狀態、庫存及通知已完成。
- B2C 發票 Stage 不會送財政部，也不會寄綠界官方發票信。
- 物流 Stage 不會自動模擬出貨後的貨態通知；後台手動推進只能用於 Sandbox。
- Sandbox 證件頁禁止上傳真實證件。
- Expo Go 不保證付款後自動 deep link；手機回到 App 後會重新查詢付款狀態。
- 實體 iPhone 的 App Store 版 Expo Go 可能與 SDK 57 不相容，請改用 Web 或 development build。
- `ChoosePayment=Credit` 在部分 iOS 環境仍可能顯示 Apple Pay。
