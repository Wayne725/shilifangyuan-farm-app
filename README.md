# 十里方圓｜生活消費與社務系統

「十里方圓」是為臺灣城鄉永續生活消費合作社設計的響應式網站，介面分成兩個工作區：

- `生活消費`：農產、共同購買、便當預購、訂單、現場取貨、宅配與超商物流。
- `社務系統`：入社申請、社員資料、名錄、活動、積點、願望、會議與治理提案。

目前只有一套正式前端：`web/`。所有真實資料、資格、價格、容量、付款、履約與稽核均以 FastAPI 與 PostgreSQL 為準。

## 技術

- 前端：Vite、React 19、TypeScript、TanStack Router／Query、Radix UI
- 後端：Python 3.12、FastAPI、SQLAlchemy、Alembic
- 資料庫：PostgreSQL；本機測試可使用 SQLite
- 金流／發票／物流：綠界 Stage
- Email：Resend，MailerSend 備援
- 私密證件：Cloudflare R2 私有 Bucket與短效簽名 URL
- 部署：Render Static Site、Web Service、PostgreSQL

## 本機啟動

明天展示時，在專案根目錄執行：

```bash
pnpm demo
```

開啟 `http://127.0.0.1:4173`；按 `Control-C` 可同時關閉前後端。

若需要分開啟動，前端：

```bash
pnpm install
pnpm dev
```

開啟 `http://127.0.0.1:4173`。Vite 會把 `/v1` 代理到 `http://127.0.0.1:8000`。

後端：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
cp backend/.env.example backend/.env
cd backend
../.venv/bin/alembic upgrade head
../.venv/bin/python -m app.seed
../.venv/bin/uvicorn app.main:app --reload
```

API 文件位於 `http://127.0.0.1:8000/docs`，健康檢查位於 `/health`。

## 展示帳號

| 身分 | Email | 密碼 |
| --- | --- | --- |
| 正式社員 | `member@shilifangyuan.tw` | `member123` |
| 一般買家 | `customer@shilifangyuan.tw` | `customer123` |
| 管理員 | `admin@shilifangyuan.tw` | `admin123` |
| 補件申請人 | `supplement@shilifangyuan.tw` | `customer123` |
| 待付款申請人 | `pending@shilifangyuan.tw` | `customer123` |

這些只供本機 Sandbox。公開部署必須以環境變數改成不同的強密碼。

## 已整合流程

- 商品與供應者管理、取貨點、庫存、稅別及配送能力
- 一般購物、社員價、購物車、後端試算與訂單中心
- 宅配與 7-ELEVEN／全家／萊爾富選擇、運費表、物流建單與貨態
- 商品需求投票、團購提案、正式開團、成團確認與退款
- 便當菜單、場次、容量、預購、六位取餐碼、QR 與工作人員核銷
- 入社、會籍、社員名錄、活動、治理提案、積點、願望、會議與結餘
- 管理總覽、三類身分銷售比例、訂單履約、退款與財務報表匯出
- 綠界付款／發票／物流 Stage、Email Outbox 與 reconciliation

## 專案結構

```text
web/                 唯一正式 React Web 前端
assets/              新版 Web 使用的商品與活動素材
backend/app/         FastAPI、領域模型、外部服務與背景工作
backend/alembic/     PostgreSQL migrations
backend/tests/       後端測試
tests/               Web API 與功能契約測試
docs/                架構、資料對照、部署與待決策文件
render.yaml          Render Blueprint
.github/workflows/   CI 與定期 reconciliation
```

## 驗證

```bash
pnpm typecheck
pnpm test
pnpm build
cd backend && ../.venv/bin/pytest -q
```

API 路由表存於 `web/src/lib/api.routes.json`。後端路由變更後執行：

```bash
cd backend
../.venv/bin/python -m scripts.export_openapi_paths ../web/src/lib/api.routes.json
```

## 正式上線前

目前是 Sandbox：不動真實款項、退款尚未呼叫正式金流、物流 Stage 不會自動推送後續貨態，且禁止上傳真實證件。正式營運仍需完成綠界正式合約、Email 網域驗證、R2 與個資政策、資料庫備份、監控及真實低額端到端驗收。

詳見 [部署手冊](docs/DEPLOYMENT.md)、[系統架構](docs/ARCHITECTURE.md)、[真實資料對照](docs/REAL_DATA_MAPPING.md) 與 [待確認事項](docs/UNRESOLVED.md)。
