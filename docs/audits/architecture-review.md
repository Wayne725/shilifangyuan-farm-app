# 程式架構與資料契約審查

日期：2026-10-23
基準：正式網站分支 `codex/v2-social-commerce` 的 `5c061a7`。審查期間主審另修商品銷售範圍；下面的「基準發現」不等於修復後仍存在。
範圍：`web/src/lib`、登入／購物車狀態、一般商品／團購／便當交易入口、後端狀態與背景工作、API 契約、CI、部署文件。未讀正式憑證，未連接正式交易資料庫或外部金流。

## 優先處理

| 優先度 | 基準發現 | 使用者影響 | 狀態 |
|---|---|---|---|
| P1 | 銷售範圍與後台「開放接單」是兩套狀態 | 非便當商品直到建單才遭拒絕；管理員無法從設定頁辨識原因 | 本輪已補後端範圍與前端能力同步；2026-10-23 12:47 正式 API 與網站代理確認 pickup 生效 |
| P1 | 一般商品、團購建單沒有重試去重 | 網路逾時後重試會建立兩筆不同訂單 | 已隔離重現，待修 |
| P2 | 自動更新登入憑證未同步 React 使用者狀態 | 憑證失效後仍顯示已登入、身分異動未即時反映 | 程式確認，待修 |
| P2 | 結帳明細固定顯示社員單價 | 一般買家看見的單價與後端正式試算不一致 | 程式確認，待修 |
| P2 | 訂單全量輪詢且沒有分頁 | 歷史單越多，讀取、傳輸與畫面更新成本持續增加 | 程式確認，待修 |

### A1 — 銷售能力需要唯一且可見的來源（P1）

證據：`backend/app/sales_scope.py:23-37`、`backend/app/routers/orders.py:253-262`、`backend/app/routers/groups.py:484`；基準 `backend/app/routers/platform.py:19-32` 只回 `orders_open`，`web/src/pages/AdminSettingsPage.tsx:28` 只憑此顯示「開放接單」。

情境：管理員開啟平台接單，但環境仍是 `meals_only`。商品可瀏覽與試算，最後下單收到 409。這是兩個不同開關互相疊加，僅變更環境預設值不會覆蓋既有部署值。

建議：後端回傳有效銷售範圍及一般商品、團購、配送能力；前端在結帳前使用這些能力，後端仍保留最終檢查。管理端同頁呈現接單開關與銷售範圍。本輪採 `pickup` 範圍開放商品、團購自取，保留尚未開放的物流與社務付費限制；部署設定是否已生效由主審總報告說明。

### A2 — 一般商品與團購的建單重試無去重（P1）

證據：`web/src/lib/http.ts:21-47` 在 12 秒結束等待；`web/src/lib/commerce.ts:163-178`、`:252-272` 直接建立訂單；`backend/app/routers/orders.py:290-352`、`backend/app/routers/groups.py:534-596` 每次產生新單。對比便當已有 `backend/app/routers/meals.py:771-807` 的 `client_request_id` 與請求指紋。

情境：後端已 commit，但手機網路中斷、回應丟失。使用者仍停在結帳頁，重按後建立第二筆單；團購的未取消 pending 單還計入每人上限（`groups.py:165-194`）。按鈕的 pending 防重只處理同一次可見請求，無法處理回應丟失。

驗證：隔離 SQLite 記憶體資料庫，對一般商品及團購各連續提交相同有效 payload 兩次。兩組皆回 `[201, 201]`，每組的訂單 ID 不同，總計 4 筆單，未呼叫付款供應商。

建議：沿用便當的「使用者 + client_request_id + request_fingerprint」契約，重試保留原 key，內容改變才產生新 key；資料庫唯一約束擋併發。補「已提交但回應丟失後再送」及 PostgreSQL 同時送出的測試。

### A3 — Token 與使用者狀態會分離（P2）

證據：`web/src/lib/api.ts:27-33`、`:73-92`、`:125-134`；`web/src/context/AuthContext.tsx:40-84`。

`restoreSession()` 的成功／失敗只更新 module 內的 token，AuthProvider 僅在初次還原、手動登入與登出更新 `user`。已登入頁面遇到 refresh 401 後，請求失敗卻仍顯示原使用者；自動 refresh 回傳新的 membership/user 資訊也沒有同步。`LoginPage.tsx:19-27` 又依 `user` 判斷是否需登入。

建議：session module 提供狀態訂閱，或由 AuthProvider 統一掌握還原結果；401 明確失效時同步清使用者及私有 Query cache，503 保留原狀態。成功更新資格時同步 user。現有 `tests/audits/web-session.test.mjs` 驗證 token 及網路錯誤，但沒有涵蓋畫面狀態同步。

### A4 — 購物車資料快照與正式試算混用（P2）

證據：`web/src/context/CommerceContext.tsx:22-37` 保存完整 Product 到 localStorage；`CheckoutPage.tsx:203-209` 固定渲染 `product.member_price`；正式價格則來自 `backend/app/routers/orders.py:170-197`。配送資格也使用儲存的舊 Product（`web/src/lib/commerce.ts:32-64`）。

一般買家與價格異動後的舊購物車，明細單價會和總計不同。後端仍重新計價，因此這是顯示與操作錯誤，不能推論後端收錯款。

建議：購物車只保存商品 ID、數量及最低限度顯示快照；結帳明細優先採 quote 的名稱、單價、數量，配送能力採最新 catalog/quote。團購 `GroupCampaignPage.tsx:240` 附近的「社員單價」也應依正式計價身分或改成「本次單價」。

### A5 — 全歷史訂單每 5 秒重取（P2）

證據：`web/src/pages/OrdersPage.tsx:57-61`；`backend/app/routers/orders.py:358-378`。

前端固定 5 秒讀取所有歷史訂單，後端沒有 cursor/limit，並 eager load 明細、發票、履約、物流；管理員登入時會返回所有使用者的單。即使已付款、已完成、沒有回跳確認，也持續全量輪詢。

建議：列表分頁且一般顧客入口只列自己的單；付款回跳只輪詢目前那張單，狀態終結即停止，列表在操作完成／重新聚焦後更新。這是隨資料量增長的確定性成本，未在正式站做壓力測試，不能宣稱已造成線上壅塞。

## 驗證與維護缺口

| 優先度 | 證據 | 問題與建議 |
|---|---|---|
| P2 | `.github/workflows/ci.yml:13-25`；`package.json:16-18`；`tests/web-feature-contracts.test.mjs:26-36` | CI 跑 typecheck、node tests、build，未跑既有 browser suite。「便當端到端」的這支 node test 僅找 QR/路由字串。保留結構契約測試，但加入至少商品自取、便當、登入恢復三條隔離瀏覽器煙霧測試；不要把 source presence 當成端到端通過。 |
| P2 | `web/src/lib/api.ts:6,73-75`；`backend/app/routers/auth.py:623-669` | refreshPromise 只合併同分頁；不同分頁使用相同單次 refresh cookie，第一個更新後第二個會因 revoked 回 401。應用 Web Locks／跨分頁協調，並在真實 Cookie 的本機瀏覽器測同時重整；現有 browser mocks 不能證明這項。此項為程式可推導競態，尚未實機重現。 |
| P2 | `backend/app/routers/platform.py:32`；`web/src/pages/AdminSettingsPage.tsx:29` | 自動出單狀態固定 `False`／「尚未連接設備」，沒有使用新增 CloudPrintPanel 的真實能力與設備狀態。接好 FEIE 後這頁也不會改變。應共用列印狀態來源，不從品牌文字推導連線。未檢查正式印表機。 |
| P2 | `docs/UNRESOLVED.md:16,31,36`；`docs/ARCHITECTURE.md`、`render.yaml`、`backend/app/integrations/invoice_void.py` | 待辦文件仍寫發票作廢未啟用、Render 免費設定；現行程式已有作廢流程、Blueprint 已不指定 free。文件混合早期 Sandbox、後續正式發佈與未驗收政策，不能直接當現行事實。維護一張標註日期／owner／已完成／待驗收的現行狀態表，歷史發佈稿保留為歷史。 |
| P3 | `backend/scripts/export_openapi_paths.py:20-30`；`web/src/lib/types.ts`；`web/src/lib/api.ts:137` | API manifest 只保存 method/path，手寫 TS DTO 與 `as Promise<T>` 不驗證 request/response schema。端點存在仍可能欄位、nullable 或 enum 漂移。優先從 OpenAPI 產生交易 DTO 或加入 response contract fixtures；不用對每個 API 任意加重型 runtime validator。 |

## 值得整理的重複邏輯

1. **交易後續導向**：`web/src/lib/commerce.ts:180-214` 與 `:273-295` 都處理物流／付款／失敗續辦；一般商品檢查空 URL，團購直接回傳。建議抽出小 Interface `continue_order(order, fulfillment)`，由一般／團購／便當入口提供差異資料。這是已存在的兩個 Adapter，不需為假想新通路再擴充層數。
2. **庫存保留釋放**：`backend/app/routers/orders.py:394-440` 與 `backend/app/integrations/payment_service.py:2064-2105` 都處理商品、團購與便當的 ACTIVE reservation 釋放；退款另處理 CONSUMED。兩處鎖定順序已有差別。抽成同一 reservation Module，明確區分 release-active 與 return-consumed，集中鎖順序及冪等不變量；保留 PostgreSQL 併發測試。未因重複本身宣稱已有庫存錯帳。
3. **資格晉升的位置**：`payment_service.py:1210-1216` 動態 import `routers.membership.start_traineeship_if_fully_paid`。付款 Implementation 依賴 HTTP router，促使內層知道路由細節。將入社款結清後的資格晉升移至 membership Module，HTTP 與付款 Adapter 共用同一 Interface。
4. **背景工作故障隔離**：`backend/app/jobs.py:141-166` 依序處理所有領域，前段非預期錯誤會跳過後續通知／發票工作。每段有獨立業務語意，應保留單一排程入口但拆出可測的領域工作與獨立錯誤結果，避免一次廣泛重寫金流。此為故障傳播風險，未宣稱正在發生。

## 已有且值得保留的設計

- 現版只有 Vite `web/`，沒有前端 mock/demo 業務邏輯與正式 API 兩套真相；本機展示是後端 seed，不應再套用舊 Expo 版本結論。
- 付款 provider 快照、server-to-server 驗證、Outbox、資料庫鎖及 PostgreSQL 併發測試已具備，不需要全面重做交易架構。
- 訂單保存價格／稅別／身分快照，後端 `available_actions` 驅動履約；這個方向正確，應持續減少前端推測業務規則。
- CI 有 migration upgrade／downgrade／re-upgrade 與 API route manifest 檢查，現有真實行為單元測試也應保留，補上缺的流程驗收即可。

## 本輪驗證紀錄與限制

- `node --test tests/audits/web-session.test.mjs tests/web-api-resilience.test.mjs tests/web-api-routes.test.mjs`：11 passed。
- 隔離後端 `test_group_campaign_shipping.py`、`test_order_cancellation_review.py`、`test_login_preferences.py`：13 passed。
- 相同一般商品／團購 payload 重送的記憶體 DB probe：兩次皆 201 且不同訂單 ID，重試去重缺口重現。
- 以上既有測試通過不代表已修 A2–A5；本報告另外指出測試未覆蓋的情境。完整前後端與 UI 驗證結果以本輪總報告為準。
- 未進行正式付款、退款、開票、寄信、證件讀取、正式資料庫量測或多分頁實機測試；外部供應商條件不能由靜態程式審查推定完成。

## 本輪已完成的直接修復

一般商品與團購結帳現在讀取平台能力，平台停止接單／品類停用會在建單前顯示原因；自取範圍隱藏配送入口，已選配送後若能力被收回也會阻止送單。後台顯示實際銷售範圍。舊 API 缺能力欄位或首輪資料尚未抵達時維持原行為，便當限定模式的受控驗收商品仍只允許白名單單品一件自取，後端繼續做完整驗證。

修復後驗證：Node 22 前端測試 73 passed；TypeScript 檢查與 `git diff --check` 通過。以本機 `VITE_LOGISTICS_ENABLED=true`、所有 API 攔截成假資料的 Chromium 執行 17 項瀏覽器測試通過，包含 7 項新銷售範圍測試、8 項自取／字級／缺取貨點測試、2 項既有驗收品流程。沒有進行正式建單或金流。
