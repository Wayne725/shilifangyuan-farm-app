# 後台原因選填與手動開票帶價

日期：2026-11-09
分支：`codex/admin-optional-reasons`
狀態：已實作，尚未提交或上線。

## 本次需求

- 全面將後台的原因、核對說明、審核過程及結果說明改為選填。
- 欄位名稱省略「選填／非必填」尾註，必要確認依既有規則保留。
- 手動開票選擇品名後自動帶入單價與稅別，數量預設 1，管理員可修改實際成交單價。
- 全域 formal-writing 規則涵蓋正式文書、程式及網站／App 介面。

## 已實作

| 範圍 | 調整 | 保留條件 |
| --- | --- | --- |
| 訂單取消與退款 | 一般及便當訂單的退單、退款及審核原因可空白 | 原訂單狀態、金額、退款及權限限制 |
| 現金收退款 | 收款、更正及退款說明可空白 | 原付款綁定、實收／退款金額與確認勾選 |
| 社員及社務 | 線下確認、會籍異動、股籍修正、補件／駁回、入社審核說明可空白 | 手機名冊核對、身分及線下確認、學生資格、測驗合格、正式決議依據 |
| 共同購買 | 取消、不成團、例外成團與處理提醒日調整原因可空白 | 例外確認勾選、已付款數量、版本與日期檢查 |
| 活動 | 駁回、整場取消、退款審核與出席更正原因可空白 | 退款預覽與核准、金額、名單狀態及版本 |
| 發票與出單 | 開立、查詢、作廢、補印、彙總紙本核對、工作重送原因可空白 | 開票／出單資格、防重送、重複列印確認與結果核對 |
| 其他後台 | 供應者審認、收款紀錄作廢、點數調整、物流測試狀態及平台設定說明可空白 | 各操作原有權限與資料驗證 |

管理員自由文字共 32 個直接宣告欄位，並涵蓋繼承這些 schema 的操作。顧客填寫規則另由原 schema 驗證。

空白原因統一存為空字串，操作人、時間、版本與紀錄關聯仍保存。通知、小票及歷史紀錄省略空白原因列。

## 手動開票價格

- 便當：帶入餐點目錄 `price`。
- 一般商品：帶入一般價 `nonmember_price`；手動開票未指定顧客會籍，管理員可依實收改價。
- 選擇品名時同時帶入目錄稅別；修改數量只更新實收總額。
- 切換分類清空品名／單價，數量重設 1；下一筆同品項恢復目錄價。
- 缺少或不合法的價格維持空白，交由管理員填寫；1 元商品照常帶入。
- 結果不明的重試仍使用第一次確認時的 payload 與 UUID，不以後續目錄資料改寫交易。

## 全域文案規則

已更新 `~/.codex/AGENTS.md` 與 formal-writing 的 `references/software-ui.md`；保留 `agents/openai.yaml` 的 `allow_implicit_invocation: true`。專案 `web/AGENTS.md` 同步記錄後台原因選填規則。

## 驗證紀錄

| 檢查 | 結果 |
| --- | --- |
| 21 個受影響後端模組 | 537 項通過，涵蓋省略／空白原因、退款、會員核對、版本及供應商契約 |
| 前端單元／契約測試 | 218 項通過，包含新增帶價邏輯 3 項 |
| TypeScript | 通過 |
| 正式前端建置 | 通過，使用 `vite build --configLoader runner` |
| `git diff --check` | 通過 |
| 瀏覽器首輪 | 212 項中 186 通過、26 失敗；尚未全數修正及重測 |
| 正式交易 | 未退款、授予會籍、開真票或送印 |

瀏覽器失敗包含原先仍要求原因必填的斷言、平台 fixture 缺版本、社務及入社流程的舊 fixture／權限狀態；部分已同步，剩餘案例仍需逐項定位，不能據此宣稱全數通過。當前測試日誌：`/private/tmp/admin-optional-reasons-browser.log`。

後續重跑瀏覽器的 shell 權限申請遭自動核准審核中止：額度限制使審核未能完成，工具明確回覆此為審核執行失敗，非不安全判定。沒有繞過審核重跑、提交或部署。

## 待接續

1. 恢復工具審核可用性後，定位並修正剩餘瀏覽器失敗，跑 Chromium／WebKit；新手動開票帶價需測手機及桌機、下一筆、切換分類和不明結果重試。
2. 完整 CI 通過後提交、合併並部署 API／Web，正式站以不送出真交易的操作驗收。
3. 原需求表「工作表2」第 33–34 列已新增，維持「進行中」。實際上線與驗收後再更新。
4. 完成後歸檔至 Obsidian「十里方圓農產品App」，同步既有 `_索引.md`。

## 後端欄位與外部契約盤點

### 連動解除

- AdminActionReason 的付款查詢、失敗工作重送、社員股數調整亦適用。
- 共同購買截止調整、拒絕／取消、低於門檻例外成團；例外勾選、已付款數量、版本與時間守門保留。
- 便當／一般訂單管理員取消、退單審核、整單退款、現場收款／更正／退款、場次取消；狀態、金額、原付款綁定保留。
- 會員線下確認、補件、核准／恢復、駁回、停權／退社／終止、轉正、股金返還；手機名冊、身分及線下確認、股金狀態保留。
- 完整入社審核、教育設定；正式決議依據 decision_reference、學生核驗、合格測驗、同意、版本保留。
- 活動審核／取消／完成、退款審核、出席更正、社員提案拒絕／結案；取消預覽、退款核准、expected_status 與 expected_version 保留。
- 供應者審核過程與結果、現場商品銷售說明、物流 sandbox 狀態、平台設定、點數調整、願望管理 admin_note。
- 發票開立／查詢／作廢、單筆訂單補印／重印／紙本核對、彙總紙本核對。

### 外部介面

- RayGate 退款請求實際 payload 無 reason，僅解除未使用的非空條件；idempotency key、金額與付款類型驗證保留。
- LocalSandboxRefundAdapter 同樣接受空原因，保留金額與 idempotency key。
- 汎宇 cancelReason 必填：只在 adapter 送出時依實際觸發來源使用「管理員作廢」或「全額退款自動作廢」，原 Refund.reason / Invoice.void_reason / audit.reason 仍空白；worker 不再先填入系統文案。

### 顧客邊界

- 新 AdminMealCancellationRequest 用於管理員取消；管理員 MealCancellationReview 繼承它。原顧客 MealCancellationRequestCreate 維持必填與去空白驗證。
- 新 AdminMembershipApplicationReview 用於後台會員操作；顧客撤回仍使用原 MembershipApplicationReview。
- CancelRequest、ActivityCancelRegistration、AdmissionWithdraw 原顧客契約未改。
- 商品名稱、正式決議依據與其他實質業務內容仍依原規則驗證。

### 通知及紙單

- 便當退單通知仍呈現核准／未核准結果；空白原因不產生「審核說明：」。
- 活動取消通知不留下空白冒號。
- 取消小票仍印「請停止製作／核對原單」，空白原因略過原因列。
- 出席更正點數紀錄的空原因不補假說明，仍保留交易與 audit 關聯。

### Schema 清單

- `backend/app/membership_admission_schemas.py:90` AdmissionReviewInput.reason: `Field(default='', max_length=2000)`
- `backend/app/membership_education_settings.py:31` MembershipEducationSettingsUpdate.reason: `Field(default='', max_length=1000)`
- `backend/app/routers/admin_users.py:31` OfflineMembershipConfirmation.reason: `Field(default='', max_length=1000)`
- `backend/app/routers/community.py:82` ActivityAttendanceUpdate.reason: `Field(default='', max_length=1000)`
- `backend/app/routers/cooperative.py:562` PointAdjustment.reason: `Field(default='', max_length=240)`
- `backend/app/routers/cooperative.py:637` WishStatusUpdate.admin_note: `Field(default='')`
- `backend/app/routers/invoices.py:40` AdminInvoiceIssueRequest.reason: `Field(default='', max_length=500)`
- `backend/app/routers/invoices.py:109` AdminInvoiceQueryRequest.reason: `Field(default='', max_length=1000)`
- `backend/app/routers/invoices.py:123` AdminInvoiceVoidRequest.reason: `Field(default='', max_length=20)`
- `backend/app/routers/logistics.py:142` SandboxShipmentStatusUpdate.reason: `Field(default='', max_length=1000)`
- `backend/app/routers/member_reports.py:244` ReceiptVoid.reason: `Field(default='', max_length=1000)`
- `backend/app/routers/order_summary.py:156` SummaryResolveRequest.reason: `Field(default='', max_length=200)`
- `backend/app/routers/platform.py:20` PlatformUpdate.reason: `Field(default='', max_length=1000)`
- `backend/app/routers/printers.py:99` ReprintRequest.reason: `Field(default='', max_length=200)`
- `backend/app/routers/printers.py:107` ResolveRequest.reason: `Field(default='', max_length=200)`
- `backend/app/routers/walk_in_sales.py:79` WalkInSaleCreate.reason: `Field(default='', max_length=500)`
- `backend/app/routers/walk_in_sales.py:153` WalkInRefundRequest.reason: `Field(default='', max_length=500)`
- `backend/app/schemas.py:64` AdminActionReason.reason: `Field(default='', max_length=1000)`
- `backend/app/schemas.py:380` ProposalReject.reason: `Field(default='', max_length=1000)`
- `backend/app/schemas.py:529` CampaignReviewDeadlineUpdate.reason: `Field(default='', max_length=1000)`
- `backend/app/schemas.py:543` CampaignDecision.reason: `Field(default='', max_length=1000)`
- `backend/app/schemas.py:554` CampaignReject.reason: `Field(default='', max_length=1000)`
- `backend/app/schemas.py:898` RefundRequest.reason: `Field(default='', max_length=1000)`
- `backend/app/schemas.py:906` CashConfirmationRequest.reason: `Field(default='', max_length=1000)`
- `backend/app/schemas.py:969` AdminMembershipApplicationReview.reason: `Field(default='', max_length=2000)`
- `backend/app/schemas.py:1187` ActivityReview.reason: `Field(default='', max_length=2000)`
- `backend/app/schemas.py:1211` ActivityRefundReview.reason: `Field(default='', max_length=2000)`
- `backend/app/schemas.py:1788` AdminMealCancellationRequest.reason: `Field(default='', max_length=2000)`
- `backend/app/schemas.py:1933` MealEventActionRequest.reason: `Field(default='', max_length=2000)`
- `backend/app/schemas.py:1937` MealEventCancelRequest.reason: `Field(default='', max_length=2000)`
- `backend/app/schemas.py:2095` SupplierAccreditationCreate.process_notes: `Field(default='', max_length=10000)`
- `backend/app/schemas.py:2097` SupplierAccreditationCreate.result_notes: `Field(default='', max_length=10000)`
