# 雷門 Sandbox 驗收清單

更新日期：2026-09-04
依據：雷門支付整合＿線上多元支付 API 介接規格 v1.6.5（2024-09-25）

這份清單用來把「程式已依文件實作」推進到「雷門實際環境已驗收」。在全部完成前，不得將正式環境的 `RAYGATE_PAYMENT_CONTRACT_VERIFIED` 設為 `true`。

## 一、請雷門提供

### 目前已收到並離線驗證（2026-09-04）

- 合作社名稱與統一編號相符。
- 已收到本專案的 RG 商代、RG 端代、32 位十六進位 IV、64 位十六進位 Key 與店家識別碼。
- 以上值符合程式欄位限制，且 AES 加密／解密與 SHA-256 產生流程可正常往返。
- 這組值與 v1.6.5 規格書中的示例值不同。
- 表列開通支付：藍新金流、LINE Pay、橘子支付、悠遊付、台灣 Pay、全支付、全盈支付。
- 已安全匯入本機未追蹤的 `backend/.env`；檔案權限為僅限目前使用者讀寫，尚未啟用真實交易。
- 尚未將憑證寫入 Git、聊天內容或專案文件。

已確認這組憑證屬於 Production，正式根網址為 `https://iqrc.epay365.com.tw`。`X-Merchant-DeviceType` 保持空白，正式查單仍成功通過驗證。雷門另已確認目前沒有主動付款或退款通知，因此驗收改以回跳立即查單、關頁後排程補查與人工對帳為準。

### 實際驗收進度（2026-09-04）

| 檢查 | 結果 | 說明 |
|---|---|---|
| 五項憑證載入與格式 | 通過 | 未在輸出、文件或 Git 顯示實值 |
| 真實 Key／IV 加密、雜湊、解密 | 通過 | 付款參數與付款結果資料均完成離線往返 |
| Merchant／Terminal Header 格式 | 通過 | 僅驗證欄位，未送出網路請求 |
| 雷門回歸測試 | 通過 | 回跳查單、主動補查、晚到帳、退款與安全測試通過 |
| 資料庫升級 | 通過 | 本機資料庫副本由 `0010` 升至 `0012_raygate_payments`；原資料庫未修改 |
| 正式站 DNS／TLS／API 路徑 | 通過 | 付款路徑可達；查單與退款端點存在且只接受 POST |
| 正式憑證查單 | 通過 | 不存在訂單得到 `RG999922／查無此交易單號`，未扣款或改變交易 |
| 正式 NT$10 付款、查單與全額退款 | 通過 | 正式付款取得 `ErrorCode=0000`、`status=2`、金額 10；退款後再次查單取得 `return_code=0000`、`status=3` 且有退款關聯編號。正式 API 的成功 `Data` 實際為單一物件，程式已相容物件及規格測試使用的陣列格式 |
| 公開回跳與補查流程 | 阻塞 | 本機已完成；現行 Render 尚未部署最新版，正式小額付款前需先驗證回跳、登入補查 API 與排程 |
| 平台訂單付款／退款 | 阻塞 | 供應商層級已完成低額付款與全額退款；尚未透過平台訂單、付款嘗試、回跳、排程與資料庫狀態完成端到端驗收 |

本次向正式雷門查單 API 送出兩次不存在訂單的唯讀請求，均收到相同的 `RG999922／查無此交易單號`。沒有建立付款、扣款或退款。`RG999922` 未列於 v1.6.5 錯誤碼附錄，已列為實測供應商回應。

### 環境與憑證

- Production HTTPS 根網址與精確 hostname。（已收到並連線驗證）
- 若雷門另有 Sandbox，提供其 HTTPS 根網址、hostname 與測試憑證。
- 本專案專用的 Store Identifier。（已收到）
- 64 位十六進位 AES Key。（已收到）
- 32 位十六進位 AES IV。（已收到）
- Merchant ID 與 Terminal ID。（已收到）
- 是否需要 `X-Merchant-DeviceType`；若需要，提供正式值。
- Sandbox 商店、測試付款方式、測試帳號或測試操作方法，以及退款權限。

Key、IV、Merchant ID、Terminal ID 只能放在本機未追蹤的 `backend/.env` 或部署平台 Secret，不貼入聊天、Issue、文件、截圖或 Git。

### 需書面確認的契約

1. `status=0`、`1`、`4`、`5` 的精確生命週期，以及最晚多久一定會成為最終狀態。
2. 是否存在非 `0000` 但最終成功的回傳碼。
3. 建議查單頻率、流量限制、交易可查詢期限與正式對帳方式。
4. 退款是否只支援全額，且 `refund_type` 是否永遠使用原付款的 `pay_type`。
5. 退款請求逾時後的官方查詢與重試方式，以及重複退款請求的冪等行為。
6. `associated_order_id` 在退款與查單回應中的完整定義。
7. 雷門 OMS 手動退款不會通知平台時的正式對帳方式。
8. v1.6.5 的付款、查單與退款端點是否仍為目前有效版本。

## 二、平台設定

Sandbox 應設定：

```text
PAYMENT_PROVIDER=raygate
RAYGATE_PAYMENT_BASE_URL=<雷門 Sandbox HTTPS 根網址>
RAYGATE_PAYMENT_ALLOWED_HOSTNAME=<同一網址的精確 hostname>
RAYGATE_PAYMENT_STORE_IDENTIFIER=<本專案 Sandbox 商店識別>
RAYGATE_PAYMENT_KEY_HEX=<64 位十六進位字元>
RAYGATE_PAYMENT_IV_HEX=<32 位十六進位字元>
RAYGATE_PAYMENT_MERCHANT_ID=<Sandbox Merchant ID>
RAYGATE_PAYMENT_TERMINAL_ID=<Sandbox Terminal ID>
RAYGATE_PAYMENT_DEVICE_TYPE=<雷門明確要求時才設定>
RAYGATE_PAYMENT_STAGE=true
RAYGATE_PAYMENT_CONTRACT_VERIFIED=false
RAYGATE_PAYMENT_RECONCILE_HOURS=24
```

應向雷門登記的平台網址：

- 瀏覽器導回：`{APP_BASE_URL}/payments/raygate/result`
- Callback 相容欄位：`{APP_BASE_URL}/webhooks/raygate/payment`（雷門目前不會主動呼叫，平台不依賴此路徑）

## 三、Sandbox 端到端驗收

每個案例都要保存平台付款嘗試編號、雷門交易編號、測試時間、最終狀態與遮蔽敏感資訊後的回應證據。

| 案例 | 操作 | 通過條件 |
|---|---|---|
| 建立付款 | 從訂單建立雷門付款頁 | 金額只能來自後端訂單；成功導向雷門 HTTPS hostname；網址不被站內快取或帶入 referrer |
| 付款成功並回跳 | 完成一筆付款並回到平台 | 回跳立即觸發後端查單；只有 `status=2` 且 `return_code=0000` 才標記已付款 |
| 付款後關閉瀏覽器 | 完成付款但不回到平台 | 排程仍能查到付款，不依賴瀏覽器或 Callback |
| 使用者取消 | 在雷門頁取消付款 | 訂單維持未付款，庫存或名額依既有取消規則釋放 |
| 付款處理中 | 取得 `status=1` 或 `status=5` | 維持確認中，不提前扣定交易或排入開票 |
| 延遲付款 | 付款嘗試過期或訂單取消後才成功 | 補查仍能發現；可重新取得庫存才完成，否則進入全額退款，不重複推進履約 |
| 重複回跳／補查 | 對同一付款重複回跳、前端重查與排程補查 | 不重複扣庫存、加點數、寄信、開票或建立退款 |
| 查單資料不一致 | 測試錯誤金額、單號、交易號或付款方式 | 拒絕更新交易；不得把訂單標記已付款 |
| 查單暫時失敗 | 回跳或排程時模擬查單逾時 | 不提前標記失敗或開票；後續補查可收斂到正確狀態 |
| 查無交易 | 先在期限內、再於逾期後取得 `RG999922` | 期限內維持確認中；逾期後釋放保留，補查期限內仍可發現晚到帳 |
| 排程未設定 | 移除或填錯 reconciliation Secret | 排程明確失敗並告警，不得顯示成功略過 |
| 補查期限結束 | 建立超過 `RAYGATE_PAYMENT_RECONCILE_HOURS` 仍無終態的付款 | 正式上線前須有管理員人工對帳清單；目前尚未實作，視為阻塞項 |
| 全額退款 | 對已付款交易退款 | 退款前先查單；完成後保存退款單號並排入發票調整待辦 |
| 重複退款 | 對同一交易重複觸發退款流程 | 不重複呼叫不可判定結果的退款；同一商業退款只完成一次 |
| 退款逾時 | 退款送出後模擬斷線或不明回應 | 後續只查單、不再次送退款；耗盡重試後通知管理員人工核對 |
| OMS 手動退款 | 從雷門後台退款 | 在正式對帳流程完成前禁止操作；不得假設平台會收到通知 |

付款成功時，訂單付款狀態與 `invoice.issue_requested` 必須在同一資料庫交易保存；發票供應商失敗不得回滾已確認的雷門付款。

## 四、正式環境小額驗收

Sandbox 全部通過並取得契約書面確認後：

1. 改用正式 Base URL、hostname 與正式憑證。
2. 設定 `RAYGATE_PAYMENT_STAGE=false`。
3. 最後才設定 `RAYGATE_PAYMENT_CONTRACT_VERIFIED=true`。
4. 先完成一筆最低可行金額的正式付款。
5. 核對平台訂單、雷門交易、入帳金額、通知與發票排程。
6. 對同一筆交易完成全額退款，核對雷門退款、平台狀態與發票調整待辦。
7. 確認反向代理、CDN、APM 與雷門端不長期保存完整付款 query string。

### Preview 單筆 NT$10 驗收操作

目前正式網站仍是 `APP_ENV=preview`，不得直接解除整站付款限制。操作順序如下：

1. 部署含單筆驗收安全閘門的版本，先讓 `RAYGATE_PAYMENT_ACCEPTANCE_ORDER_ID` 保持空白。
2. 設定 `PAYMENT_PROVIDER=raygate`、正式雷門憑證、正式 Base URL 與 hostname，並設 `RAYGATE_PAYMENT_STAGE=false`；`RAYGATE_PAYMENT_CONTRACT_VERIFIED` 仍維持 `false`。
3. 由網站建立一筆總額恰為 NT$10、履約方式為合作社取貨的專用測試訂單。此時建立付款會被 Preview 阻擋，但訂單應保留。
4. 將該筆訂單 UUID 填入 `RAYGATE_PAYMENT_ACCEPTANCE_ORDER_ID` 並重新部署，再從訂單中心續接付款。
5. 核對雷門付款、平台訂單、汎宇測試發票、查回票號與 Email；再由管理端執行全額退款並核對發票作廢流程。
6. 驗收完成或中止後立即清空 `RAYGATE_PAYMENT_ACCEPTANCE_ORDER_ID`，確認原測試商品不再對外販售。

這個例外不放行其他金額、其他訂單、宅配訂單或社員款項。不得把既有 NT$600 訂單填入 allowlist。

## 五、簽核

| 項目 | 負責人 | 日期 | 結果／證據位置 |
|---|---|---|---|
| 雷門契約書面確認 |  |  |  |
| Sandbox 付款驗收 |  |  |  |
| Sandbox 退款驗收 |  |  |  |
| 回跳與關頁補查驗收 |  |  |  |
| 排程失效告警驗收 |  |  |  |
| 對帳與人工處理演練 |  |  |  |
| 正式小額付款／退款 |  |  |  |
