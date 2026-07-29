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
5. 設定三組不同的強密碼、管理員重設確認碼及所有外部服務密鑰；部署版不可沿用 README 的範例管理員密碼。
6. 重新部署 API 與 Web。

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

### SendGrid

- `SENDGRID_API_KEY`
- `SENDGRID_FROM_EMAIL`
- `SENDGRID_FROM_NAME=十里方圓`

寄件地址必須先完成 Single Sender Verification。綠界發票 Stage 不接受真實 Email；真實收件地址只傳給 SendGrid。

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
7. 執行管理員「重設展示資料」，恢復 9/10 投票及接近成團的團購。
8. 確認 SendGrid 寄件者仍為 verified。

## 手機展示相容性

- 現場主路徑使用 Render Web，付款後可自動回到訂單頁。
- Expo SDK 57 可搭配對應版本 Expo Go 測試 Android 裝置／模擬器及 iOS 模擬器。
- 目前實體 iPhone 無法側載舊版或指定 SDK 的 Expo Go；若一定要原生展示，請事先製作 development build。
- 手機原生付款以系統瀏覽器開啟，回到 App 後重新整理付款狀態。

## Sandbox 限制

- AIO Stage 不動真實款項。
- AIO Stage 沒有可實際測試的信用卡退款 API；App 的退款完成代表本系統 Sandbox 狀態、庫存及通知已完成。
- B2C 發票 Stage 不會送財政部，也不會寄綠界官方發票信。
- Expo Go 不保證付款後自動 deep link；手機回到 App 後會重新查詢付款狀態。
- 實體 iPhone 的 App Store 版 Expo Go 可能與 SDK 57 不相容，請改用 Web 或 development build。
- `ChoosePayment=Credit` 在部分 iOS 環境仍可能顯示 Apple Pay。
