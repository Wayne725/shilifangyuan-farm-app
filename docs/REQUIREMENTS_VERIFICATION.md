# 原始功能表逐項驗收追蹤

核對日期：2026-10-31。來源：原始「十里方圓需求及完成狀態」的 `工作表2`（gid `1170845822`），唯讀重新取得 `A1:AA36`、儲存格附註與留言。共有 28 項任務；本次沒有修改表格。表內 22 項標記已完成、3 項進行中、1 項尚未開始、2 項已停止；**28 項「完成驗收」都仍為 FALSE**。狀態欄與正式驗收不同，不能直接宣稱整案完成。附註／回覆是本表可見欄位；本次查無額外 Drive 留言或儲存格 note。

同日修復整合後再次取回同一範圍、note及留言：972格逐值對照原快照無差異，仍是上述28項與零完成驗收；沒有額外note或留言。複核原始資料與比較在`outputs/remediation-completion/requirements-sheet-final-recheck.json`及`requirements-sheet-recheck-comparison.json`，原表保持唯讀。

以下列出可追查的程式／測試及剩餘驗收，不以測試檔存在代替執行通過。測試執行及新發現缺陷追蹤見 `REMEDIATION_STATUS.md`。

| 表列 | 需求（含後續說明） | 程式／回歸證據 | 剩餘驗收或界線 |
|---|---|---|---|
| 2 | 付款前填餐具需求 | `test_meal_pickup_preferences.py`、便當訂單快照 | 正常購買回歸及實際出單核對 |
| 3 | 付款前選取餐地點 | `checkout-pickup-selection.mjs`、`test_catalog_pickup_sales_scope.py` | 不同入口及管理員停用取貨點的恢復 |
| 4 | 商品加一點擊回饋 | `product-add-feedback.mjs`、購物車數量控制 | 行動裝置與輔助操作 |
| 5 | 手機條碼與會員載具 | `saved-mobile-barcode.mjs`、`test_saved_mobile_barcode.py` | 正式供應商與通知交付；偏好儲存不影響單次載具 |
| 6 | LINE 綁定不用社員編號，改 Email／電話 | `test_member_line_intake.py`、`line-member-entry.mjs` | 真實新 LINE 身分及名冊認領驗收 |
| 7 | LINE 社員／非社員入口 icon | `LINE_LOGIN_SETUP.md`、`issues-line-entry.mjs` | LINE 官方帳號選單屬外部設定，需實機確認兩入口 |
| 8 | 管理端彈性設定便當加購 | 便當 offering／選項群組、`meal-order-flow.mjs` | 修改後新單採新值、舊單快照不變 |
| 9 | LINE 登入後進便當訂餐 | `line-login.mjs`、`line-returning-entry.mjs`、`login-navigation.ts` | Safari／LINE WebView 真實跨站返回 |
| 10 | 便當與一般訂單分類 | `OrdersPage.tsx`、`invoice-order-categories.mjs` | 所有付款／發票入口使用同一訂單分類 |
| 11 | 日常選品可選數量 | 購物車、`test_order_quantity_limits.py` | 大量並行、庫存變動及重送保護 |
| 12 | 管理端設定日常選品與取貨地點 | 商品／取貨點管理、`test_catalog_pickup_sales_scope.py` | 正式 sales scope 不因測試而擅自開放 |
| 13 | 每日各品項數量彙總 | `test_product_daily_sales_migration.py`、財務品項報表 | 退款／改量／時區跨日與真實帳務對照 |
| 14 | 消費者不得自行完成退單 | API 直接拒絕顧客退單，改致電客服由管理員處理；`admin-order-cancellation.mjs` | 歷史申請仍保留；不能繞過授權退款 |
| 15 | 出單機內容與列印 | `FEIE_PRINTING.md`、`FP_V58_W_ARRIVAL_SETUP.md`、`test_feie_region_isolation.py` | 已到貨且中國站供應商測試繁中出紙；雙區程式已發布。仍待本人 API 認證／憑證、網站訂單出紙及斷線補印驗收 |
| 16 | 共煮預定 | 已停止 | 不重新加入此功能 |
| 17 | 訂單頁分便當與一般 | 同第 10 列 | 同需求的補充說明，保留分類而不建立重複系統 |
| 18 | 使用者增減數量、刪除訂單 | `order-quantity-amendments.mjs`、`test_order_quantity_amendments.py` | 未付款可改量；取消／退款需客服及管理員處理，保留財務歷史，不硬刪交易或新增未要求的部分退款 |
| 19 | 本人／管理員查社員編號，與名冊一致 | 會籍／名冊認領、`test_member_line_intake.py` | 編號不是 LINE 綁定必填條件；名冊實際核對 |
| 20 | 首頁入社申請 icon | `HomePage.tsx`、`site-navigation.mjs` | 非社員登入後可用，入口不授予會籍 |
| 21 | 完整入社：填表、教材測驗、80 分、審核、核帳、發證 | `MEMBERSHIP_ADMISSION_STATUS.md`、`test_membership_admission.py` | 僅教育／測驗／正式審核階段完成；後續應收、轉帳核帳及發證仍待補，不擅自收款 |
| 22 | 個人資料改上傳 JPG／PNG | `test_profile_photos.py`、會員照片流程 | 不恢復原網站／FB 欄位；私有上傳與檔案驗證 |
| 23 | 勳章／點數延伸連結 | 已停止 | 不新增購物金用途；既有積點帳務仍須維持一致 |
| 24 | 各項會議及可編輯類別 | `meeting-categories.mjs`、`test_meeting_category_migration.py` | 保留歷史類別、會議及出席紀錄 |
| 25 | 推薦新商品，自訂需求後由管理員確認供應 | 共同購買提案、`test_proposal_direct_open.py` | 供應確認／開團權限與通知不可省略 |
| 26 | 現貨日常購買、自取；共同購買集單後後台決定 | `group-review-window.mjs`、`test_group_review_window.py` | 成單／取消通知；目標更動的舊待付款保護見 OPS04 |
| 27 | 訂單收款、對帳與會計匯出 | `accounting-exports.mjs`、`test_accounting_exports.py`、`FINANCIAL_STATEMENT_RECONCILIATION.md` | 匯出可測；真實入帳核對與 Windows 會計工具匯入仍需驗收 |
| 28 | 永續活動入口及收費 | `public-activities.mjs`、`activity-payments.mjs`、`test_activity_payments.py` | 已有收費實作及模擬；真實收退款、出席更正及帳務驗收 |
| 29 | 互動頁統一「共同購買」 | 前端標籤及頁面文字；本輪搜尋無硬編碼「團購」 | 歷史交易／使用者原文與 API 代碼保留；對外頁、報表仍須持續檢查 |

## 驗收規則

1. 本表的「程式具備」不能代替瀏覽器、資料庫併發或正式供應商驗收；最新整合版本須通過對應測試。
2. 原需求和後續解釋衝突時，以已確認的最新流程為準；不刪除歷史訂單、舊申請或受權限保護的兼容入口來假裝輕量化。
3. 不直接勾選使用者原表的驗收欄。每項保留證據及待辦，待真正完成再更新結論。
