# 十里方圓｜農產品與共同購買 App

「十里方圓」是為臺灣城鄉永續生活消費合作社設計的跨平台 App，涵蓋一般農產品購物、社員／非社員雙價格、共同購買投票、正式團購、訂單管理、綠界測試金流、電子發票與通知。

同一套 Expo 程式可在 iOS、Android 與 Web 執行；FastAPI 與 PostgreSQL 負責身分、價格、投票、團購、訂單、付款與發票狀態。

## 技術

- 前端：Expo SDK 57、React Native、TypeScript、Expo Router、TanStack Query
- 後端：Python 3.12、FastAPI、SQLAlchemy、Alembic
- 資料庫：PostgreSQL；本機可使用 SQLite
- 金流：綠界 AIO Stage，信用卡一次付清
- 發票：綠界 B2C 電子發票 Stage
- 通知：App 通知中心、SendGrid Email
- 部署：Render Static Site、Web Service、PostgreSQL

## 前端啟動

```bash
pnpm install
pnpm start
```

可按 `w` 開啟 Web。此專案使用 Expo SDK 57；目前 Android 裝置與模擬器可安裝對應版本的 Expo Go，iOS 模擬器亦可，但 App Store 版 Expo Go 只支援目前上架的 SDK，因此實體 iPhone 建議使用 Web 或另建 development build。

```bash
pnpm web
pnpm typecheck
pnpm test
pnpm export:web
```

未設定 `EXPO_PUBLIC_API_URL` 時，App 會使用內建展示資料；一旦設定 API，所有讀寫都以後端資料與錯誤狀態為準。

## 後端啟動

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
cp backend/.env.example backend/.env
cd backend
../.venv/bin/alembic upgrade head
../.venv/bin/python -m app.seed
../.venv/bin/uvicorn app.main:app --reload
```

API 文件：

- `http://localhost:8000/docs`
- `http://localhost:8000/health`

本機預設展示帳號：

| 身分 | Email | 密碼 |
| --- | --- | --- |
| 社員 | `member@shilifangyuan.tw` | `member123` |
| 非社員 | `customer@shilifangyuan.tw` | `customer123` |
| 管理員 | `admin@shilifangyuan.tw` | `admin123` |

內建資料模式可使用上表快速登入。部署連接後端時，請務必用 `DEMO_ADMIN_PASSWORD`、`DEMO_MEMBER_PASSWORD`、`DEMO_NONMEMBER_PASSWORD` 覆寫成不同的強密碼；公開 Web 不應沿用範例管理員密碼。

本機內建資料的重設確認碼為 `RESET`。Render Sandbox 必須另設至少 8 字元的 `DEMO_RESET_CONFIRMATION`；管理員在後台輸入正確確認碼後才能重設資料。

## 核心流程

### 一般購物

1. 訪客瀏覽商品，登入社員或非社員帳號。
2. 後端依社員資格重新計價。
3. 建立訂單與 15 分鐘庫存保留。
4. 前往綠界 Stage 付款。
5. 管理員推進備貨、可取貨、已取貨。
6. 完成取貨後開立 B2C 測試電子發票。

### 共同購買

1. 買家針對既有商品或團購套組發起投票。
2. 管理員審核後開放投票；預設 10 人、7 天。
3. 管理員可把達標投票轉成正式團購，也可直接開團。
4. 買家付款後才計入成團件數。
5. 達標後暫停新加入，等待管理員確認。
6. 確認成團後可募集到截止或滿額，最後公布取貨時間。
7. 未成團、拒絕或取消時，後端完成 Sandbox 退款狀態與庫存釋放。

## 專案結構

```text
app/                 Expo Router 頁面
src/                 元件、狀態、API client 與展示資料
backend/app/         FastAPI、領域模型、外部服務與背景工作
backend/alembic/     PostgreSQL migration
backend/tests/       後端測試
docs/                架構、部署與展示文件
render.yaml          Render Blueprint
.github/workflows/   CI 與每 10 分鐘 reconciliation
```

## Sandbox 限制

- 綠界 AIO Stage 不動真實款項。
- AIO Stage 沒有實際信用卡退款 API；退款完成只代表本系統狀態、庫存與通知已完成。
- 綠界發票 Stage 不會送財政部，也不會寄官方發票信；App 另外以 SendGrid 寄開立通知。
- Expo Go 的付款流程由使用者手動切回 App，再向後端查詢結果；正式安裝版已預留 `shilifangyuan://`。
- Expo SDK 57 目前無法直接由實體 iPhone 的 App Store 版 Expo Go 開啟；現場以 Web 為主，或事先準備 development build。
- 商品正式稅別、社員資料來源、配送與出貨規則仍需合作社確認。

部署及展示前檢查請見 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)，系統設計請見 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。
