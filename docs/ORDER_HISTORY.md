# 本人訂單歷史、搜尋與狀態更新

日期：2026-10-31。本批為待部署的 API／前端改版；本機測試不代表已在正式站發布。

## 讀取協定與範圍

回應維持 JSON array，不新增 total envelope。新 UI 每頁讀25筆：

```text
GET /v1/orders?limit=25&own_only=true&history_scope=general
GET /v1/meal-orders?limit=25
```

一般歷史包含本人的一般商品與共同購買，不混入餐點；餐點接口本來即限定本人。管理員的「我的歷史」也只看本人。一般單筆回讀必帶 `GET /v1/orders/{id}?own_only=true`，餐點單筆則沿用只允許本人的 `/v1/meal-orders/{id}`。後端驗證owner，前端不以角色標籤猜測資料所有人。

舊呼叫保持相容：無參數一般列表仍沿原 array／管理員範圍；一般單筆未帶own_only時，既有合法管理員查他人單能力保留。這是本人歷史與管理工具的範圍區分，不取消原管理權限。

## 搜尋與游標

兩接口接受 `q`、`payment_status`，以及成對的 `before_created_at`／`before_id`；後端limit上限100。新 UI的q經NFKC、Unicode小寫與多空白分詞，250ms debounce，輸入草稿立即保留。伺服器在分頁前做所有詞AND匹配，涵蓋全部歷史的單號、商品／餐點、取貨點，餐點另含場次名稱；不能只搜尋已載頁。

關鍵字或付款篩選改變即使用新的查詢與首頁游標。下頁cursor取上一個實際回應array的最後一筆created_at／id；恰25筆可繼續，不足25停止。以完整時間＋id處理相同時間資料，不用畫面去重後的末筆猜cursor。

UI使用InfiniteData pages，顯示時依id去重，標示「已載入N筆訂單」，不把它當成總筆數。載入更多失敗保留已載頁，使用相同cursor重試。單筆狀態／改量更新只替換命中紀錄，保留pageParams與實際頁長；狀態不再符合付款篩選時即從目前畫面移除。

## 付款返回與原單

`/orders` 保持預設餐點tab；URL帶order_id時先從一般頁處理返回。原單若已在已載頁，不增加單筆GET。原單較舊、不在頁中且目前需要顯示時，才回讀單筆；不為被搜尋／付款篩選隱藏的舊URL原單持續讀取或顯示假pending。

若一般單筆指出餐點通路，沿原餐點detail導向正確的`/meal-orders?order_id=...`。付款確認中的等待、登入恢復、回到原單與金流／物流交接沿既有流程；不因分頁建立新單、重新付款或清除購物車。

付款返回橫幅以已讀取的本人目前Order狀態為準。URL的paid／succeeded／resume／selected只表示核對意圖，不是入帳或建單證據；任意URL message不作結果文案。查詢中、讀取失敗、缺單或非本人單不宣稱付款完成／訂單已保存。真正paid、退款／晚到款／逾期及已知原單resume仍依目前資料顯示，物流selected必須有目前shipment確認狀態。

付款attempt若用於核對／重讀，必須同時符合attempt id、order id、目前order revision，且superseded明確為false。舊版缺少證據欄或舊revision的succeeded，不能覆蓋目前仍pending的Order；即使完全匹配，也須重讀Order確認入帳，不能只憑attempt稱目前新版單已付。本項只修結果判斷，不新增收款或改寫金流API。

結果提示與操作等待鎖分開判斷。本人pending單以payment=confirming返回時，讀取失敗、付款查詢503或缺少可核對證據，均不因提示改成「無法確認」而放行付款／改量入口。匹配目前單與版本、非superseded的failed／expired attempt可解除等待，沿後端available_actions正常續付；succeeded仍須等待Order真正paid。不能把所有pending單永久鎖到Order終態，因為已失敗的attempt可合法讓原pending單重試。

## 更新頻率

- 單頁含未終結訂單時保留5秒列表更新。只有終結歷史時停止週期讀取。
- 多頁或舊API超量array不每5秒重抓所有頁，只對目前展開的單筆立即回讀；該單仍未終結時每5秒更新，切換選中單即改讀新單。
- 未開啟的一般tab停止list與detail背景讀取；被篩選隱藏的返回單不另開週期查詢。
- 待付款、待取餐／配送、發票待處理、退款、取消審核及付款結果未知均視為未終結；不能僅因已付款就停止取餐／發票更新。餐點原有pickup-credential查詢與5秒更新保留，核銷後畫面不再顯示有效QR。
- 「更新訂單」可手動重讀已載頁；視窗focus沿原30秒stale policy更新已過期資料。終結後仍可手動或focus取得後續管理變更。

私人query key仍含本人user id，沿用既有登入／401續期／跨帳號回應守衛。共同購買、一般結帳與購物車不在此模組中改寫。

## 相容與部署

必須先部署支援分頁、本人scope與單筆own_only的後端，再發布新 UI。本項未新增資料庫欄位；整體版本其他migration仍依發布清單執行。

若舊API忽略limit而返回超過25筆，前端保留可讀內容並停止續頁cursor，避免重複讀同一批；這是舊array的讀取相容，不能代替後端先部署的owner保證或宣稱舊站已有完整分頁。舊無參數呼叫也不會自動取得本次效能改善。

本機證據與來源／dist雜湊見 `outputs/remediation-completion/order-history-ui-validation.md` 及其manifest。Playwright以合成API與隔離付款頁驗證client行為，外站中止；後端查詢／PG／owner控制由整合紀錄分別驗證。WebKit是Safari相近引擎，不能把這些結果當作原生Safari／iPhone、正式Cookie或真金流驗收。此文件不宣稱正式部署或實際營運容量。
