# 十里方圓｜生活消費與社務 App

「十里方圓」是為臺灣城鄉永續生活消費合作社設計的跨平台 Sandbox。App 以固定工作區切換器分成：

- `生活消費`：一般農產、共同購買、便當預購、訂單、現場取貨與物流。
- `社務系統`：入社申請、社員資料、自願公開名錄、社員活動與治理提案。

同一套 Expo 程式可在 iOS、Android 與 Web 執行；FastAPI 與 PostgreSQL 負責身分、有效會籍、價格、容量、訂單、付款、履約與稽核。

## 技術

- 前端：Expo SDK 57、React Native、TypeScript、Expo Router、TanStack Query
- 後端：Python 3.12、FastAPI、SQLAlchemy、Alembic
- 資料庫：PostgreSQL；本機可使用 SQLite
- 金流：綠界 AIO Stage，信用卡一次付清
- 發票：綠界 B2C 電子發票 Stage
- 物流：綠界全方位物流 Stage；宅配、7-ELEVEN、全家、萊爾富
- 私密證件：Cloudflare R2 私有 Bucket、短效簽名 URL
- 私密欄位：版本化 AES-256-GCM
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
| 補件申請人 | `supplement@shilifangyuan.tw` | `customer123` |
| 待付款申請人 | `pending@shilifangyuan.tw` | `customer123` |

內建資料模式可使用上表快速登入。部署連接後端時，請務必用 `DEMO_ADMIN_PASSWORD`、`DEMO_MEMBER_PASSWORD`、`DEMO_NONMEMBER_PASSWORD` 覆寫成不同的強密碼；公開 Web 不應沿用範例管理員密碼。

本機內建資料的重設確認碼為 `RESET`。Render Sandbox 必須另設至少 8 字元的 `DEMO_RESET_CONFIRMATION`；管理員在後台輸入正確確認碼後才能重設資料。

## 核心流程

### 入社與社員資格

1. 自行註冊，並用驗證信中的連結（`/verify-email?token=…`）完成 Email 驗證。
2. 填寫入社資料並上傳三份證件；App 會在本機算出 SHA-256，直接 PUT 到 R2 私有 Bucket 後才向後端確認。**Sandbox 禁止上傳真實證件**，請使用測試素材。
3. 管理員要求補件、核准或駁回。
4. 核准後分別繳交示範入社費 500 元與股金 1,000 元。
5. 兩筆綠界 Stage 付款皆成功後，產生 `SLF-YYYY-####` 社員編號。

API 的社員資格只由 `memberships.status=active` 推導；`users.membership_type` 僅保留舊資料相容，不參與授權。

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

### 社員活動與治理提案

- 有效社員可建立免費活動，管理員審核後發布；滿額後採 FIFO 候補與自動遞補。
- 社員治理提案與商品團購提案分離。表決採公開記名 `yes / no / abstain`，棄權計入最低投票數，贊成必須多於反對。
- 合作教育本版不實作，也不阻擋入社；待決事項集中於 `docs/UNRESOLVED.md`。

### 便當預購

- 可重用餐點搭配單次校園／攤位場次，每款便當有獨立容量。
- 建立付款頁才保留 15 分鐘容量；付款後可於 30 分鐘內、且不超過訂購截止自行取消。
- 產生六位取餐碼與 QR token，工作人員可重複安全地核銷；逾取餐時間標記未取且不退款。
- 便當固定活動取餐，不與農產或團購混單，也不使用物流。

### 物流

- 一般農產與確認成團的團購可選宅配或超商，全程預先付款、不代收。
- 同一訂單只接受單一溫層、單一地址與單一包裹。
- 流程為三步：建立訂單 → 綠界物流選擇頁（選門市或確認地址）→ 付款。未完成選擇的訂單無法付款。
- 選擇頁以一次性、30 分鐘到期的 token 開啟，不需 Bearer token；瀏覽器導頁無法帶 Authorization header，與付款頁 `/payments/{attempt_id}/checkout` 相同設計。
- 買家中途離開可在訂單頁按「繼續選擇物流」重新取得連結。
- 運費一律由後端 `shipping_rates` 費率表計算，App 不內建任何金額；展示費率為超商 70 元、常溫宅配 160 元、冷藏宅配 220 元，商品小計滿 1,500 元免運。
- 綠界物流 Stage 不會自動模擬後續貨態，因此管理後台提供有稽核紀錄的 Sandbox 貨態推進（依 已建立 → 配送中 → 已送達 逐級推進，不可跳級）。

## 專案結構

```text
app/                 Expo Router 頁面
src/                 元件、狀態、API client 與展示資料
backend/app/         FastAPI、領域模型、外部服務與背景工作
backend/alembic/     PostgreSQL migration
backend/tests/       後端測試
docs/                架構、部署與展示文件
CONTEXT.md           領域詞彙與邊界
render.yaml          Render Blueprint
.github/workflows/   CI 與每 10 分鐘 reconciliation
```

### API 契約檢查

`src/services/api.routes.json` 是由 FastAPI 的 OpenAPI schema 匯出的後端路由表，
`tests/api-routes.test.mjs` 會比對 App 呼叫的每一個路徑是否存在。改動後端路由後必須重新匯出：

```bash
cd backend && python -m scripts.export_openapi_paths ../src/services/api.routes.json
```

CI 的 `api-contract` job 會重新匯出並在檔案過期時失敗。

## Sandbox 限制

- 綠界 AIO Stage 不動真實款項。
- AIO Stage 沒有實際信用卡退款 API；退款完成只代表本系統狀態、庫存與通知已完成。
- 綠界發票 Stage 不會送財政部，也不會寄官方發票信；App 另外以 SendGrid 寄開立通知。
- 綠界物流 Stage 可選通路、建單及查詢，但不會自動推送後續配送狀態。
- 入社頁只接受測試素材；正式證件隱私告知、保存期限與刪除政策尚待合作社決定。
- 入社費、股金及股金返還只產生系統收據／Sandbox 紀錄，不開電子發票。
- Expo Go 的付款流程由使用者手動切回 App，再向後端查詢結果；正式安裝版已預留 `shilifangyuan://`。
- Expo SDK 57 目前無法直接由實體 iPhone 的 App Store 版 Expo Go 開啟；現場以 Web 為主，或事先準備 development build。
- 商品正式稅別、正式入社費／股金、物流合約與校園供餐資料仍需合作社確認。

部署及展示前檢查請見 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)，系統設計請見 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)，待決策項目請見 [docs/UNRESOLVED.md](docs/UNRESOLVED.md)。
