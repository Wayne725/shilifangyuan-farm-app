# Web 合併與保留決策

## 最終結論

`farm` 是唯一正式專案與資料真相；`farm/web` 是唯一正式前端。`Downloads/--main` 沒有整站嵌入，也沒有保留第二套 Supabase 或 mock 資料層，只吸收適合 Web 的 Vite、React、TanStack 與 Radix 架構方向。舊 Expo 前端已在功能對等、測試與瀏覽器驗收完成後刪除。

## 取長補短

| 來源 | 保留 | 淘汰 |
| --- | --- | --- |
| 原 `farm` | FastAPI、PostgreSQL、會員／社員、商品、團購、便當、金流、物流、發票、社務、稽核、測試及素材 | Expo UI、React Native 導航、重複 client 與原生 deep link |
| `--main` | Vite Web 開發體驗、TanStack Router／Query、Radix 與響應式管理介面方向 | Supabase 直連、第二套登入、mock 商品、假錢包、假結帳與沒有正式 API 的 AI／預測功能 |

## 正式功能入口

| Web 入口 | 功能 |
| --- | --- |
| `/shop`、`/products/:id`、`/checkout` | 商品、詳情、購物車、後端試算、取貨與物流 |
| `/group-votes`、`/groups/:campaignId` | 需求投票與正式團購 |
| `/meals/:eventId`、`/meal-orders` | 便當預購、取餐碼與 QR |
| `/orders` | 訂單、付款續辦、物流、取消與履約狀態 |
| `/social`、`/account` | 社員活動、治理、願望、會議、積點與入社進度 |
| `/admin` | 管理總覽、訂單、物流、退款與運費 |
| `/admin/catalog` | 商品、供應者與取貨點 |
| `/admin/groups` | 團購提案與正式團購 |
| `/admin/meals` | 菜單、場次、名額與核銷 |
| `/admin/social` | 入社、活動與治理管理 |
| `/admin/finance` | 銷售占比、稅務、結餘試算與 CSV |

交易流程由 `web/src/lib/commerce.ts` 集中處理；建立訂單後若第三方服務暫時不可用，會回訂單中心續辦，不重複建立訂單。

## 刪除原則

只刪除已由新版 Web 覆蓋且無任何 import、路由、建置或測試引用的程式碼。`assets/` 因仍由 Vite 使用而保留；FastAPI 領域模型、API、migration 與測試均保留。
