# 汎宇雲端電子發票流程

## 2026-10-01：全額退款自動作廢

新的汎宇全額退款確認成功後，由背景工作自動查原票、送出一次作廢，再查回確認。送出不明只補查，8 次未確認通知管理員。既有無旗標待辦與部分退款保留人工流程。下方早期日期段落為歷史紀錄，現行退款行為以「退款後的發票補償」為準。

更新日期：2026-09-06（本輪管理作廢等修改仍在本機，尚未部署）

## 測試環境通知信觀察

2026-09-06 使用者回報：晚上開立的多筆汎宇測試發票（包含自行 NT$10 真實付款後產生的測試票），皆於隔天約 08:00 收到汎宇通知信。這是使用者實測觀察，**不是汎宇已確認的固定寄信時程，也不能證明伺服器晚上休眠**；批次寄送或郵件延遲僅是可能原因。

- 開票完成依 `/openInvoice`、`/queryInvoice` 回傳的票號與狀態判定；寄送／送達是獨立結果。`notifyEmail` 已提交不等於收件人已收到。
- 已有票號但尚未收到信，不得為此重開發票、重排已完成的開票工作，或用平台付款信冒充汎宇官方發票信。
- 平台仍寄付款成功通知；官方發票通知仍交由汎宇寄送，不改 API 欄位、載具或簽章。
- 正式環境的憑證、通知時效與未送達處理方式仍需供應商確認。真實金流扣款不會將汎宇測試票變成正式票，也不代表已符合正式營運開票條件。

## 已確定需求

- 全站只提供雲端電子發票，不寄送紙本發票。
- 個人發票預設使用 `EG0478` Email 會員載具，也可改用手機條碼載具。
- 公司發票保存統一編號、公司名稱與買方 Email；預設同樣使用 `EG0478` Email 會員載具，汎宇依 `notifyEmail` 寄送含 PDF 的通知，買方可自行列印。
- 中獎提示只引導消費者至全家 FamiPort，不提供 7-ELEVEN 列印流程。
- 平台實作汎宇 `/openInvoice` 與 `/queryInvoice`。
- 銷項媒體申報檔由汎宇加值中心下載，平台不重造申報檔。

## 系統邊界

```text
Checkout
  -> Order + OrderItem snapshot
  -> Payment gateway callback（支援時）/ server query
  -> Backend verifies signature, order, transaction and amount
  -> Payment + Order = paid
  -> invoice.issue_requested outbox（與付款狀態同一交易提交）
  -> queryInvoice(orderID)
       -> found: sync invoice number/status
       -> not found: openInvoice(order snapshot)
  -> Invoice + exact provider request + decimal InvoiceItem snapshot
  -> in-app notification（平台不重複寄汎宇發票信）
```

開票時點已確定為「後端驗證付款成功後」。前端導回成功頁不具付款效力；只有金流 callback（供應商支援時）或 server-to-server 查詢通過後才會排入開票。雷門沒有主動通知，因此由回跳立即查單與背景補查確認。取貨與物流完成時保留相容性補排機制，僅用於舊資料或舊事件；同一訂單若已排入、已開立或待作廢，不會重複排入。

## Checkout 與資料快照

Checkout 會把下列資料直接保存到訂單，後續不再依使用者帳號或商品現值回推：

- `invoice_buyer_type`
- `invoice_buyer_tax_id`
- `invoice_buyer_name`
- `invoice_buyer_email`
- `invoice_carrier_type`
- `invoice_carrier_value`
- 品名、數量、單位、單價、小計與稅別

一般商品、團購與便當預購共用同一組發票元件與 payload 轉換：

- 發票 Email 預填登入帳號，可由買家在結帳時修改。
- Email 雲端交付時，買方 Email 同時作為會員載具識別與通知信箱，缺少、格式錯誤或超過 64 字元都不能送出。
- 公司發票必填有效統編、公司名稱與發票 Email；若改用手機條碼，通知 Email 上限為 80 字元。
- 三種結帳的訂單摘要都會顯示發票類型、Email，以及適用的統編、抬頭或手機條碼，供買家送出前確認。

汎宇載具欄位依 2026-09-04 加值中心回覆轉換：

- B2C 與 B2B 的 Email 雲端發票都使用 `carrierType=EG0478`，`carrierID1` 與 `carrierID2` 均帶入訂單買方 Email。
- 手機條碼使用 `carrierType=3J0002`，兩個載具 ID 都帶入正規化後的手機條碼。
- Email 會員載具不得為空且不可超過汎宇欄位上限 64 字元；程式拒絕靜默截斷。
- `notifyEmail` 另外控制是否寄送汎宇官方開票通知，不能只靠載具 ID 判斷已寄信。

第一次處理發票時，系統建立 `invoices` 與 `invoice_items`，綁定成功付款嘗試，並將實際要送給供應商的完整 `reqData` 保存為 `provider_request`。B2B 未稅單價與金額以六位小數欄位保存，不再用整數近似。既有發票一旦綁定 provider，不允許從綠界直接切成汎宇重送，避免同一訂單跨供應商重複開票。

## 開立與逾時補償

`issue_paid_order_invoice` 會鎖定訂單後執行：

1. 確認訂單已由後端標記付款成功。
2. 建立或讀取發票、實際供應商請求與商品快照。
3. 先用相同 `orderID` 呼叫 `/queryInvoice`。
4. 若查到發票，只同步號碼、日期、隨機碼與供應商狀態，不再次開立。
5. 若汎宇回覆文件記載的 `statusCode=3`，或 Sandbox 實際回覆 `statusCode=2` 且訊息明確為「尚未用此銷貨單號碼開立發票」，才呼叫 `/openInvoice`；其他代碼仍視為錯誤。
6. 保存回應並把訂單與發票狀態一起提交。

因此，即使 `/openInvoice` 已在汎宇完成、平台卻因網路逾時沒收到回應，下次 worker 也會先查詢並復原，不會直接重開。

常駐 API 可透過 `RECONCILIATION_ENABLED=true` 定期處理 Outbox，預設每輪結束後等待 60 秒；同一 process 沿用既有單輪執行限制。程式預設關閉；依 [09-05 部署紀錄](ACCEPTANCE_DEPLOYMENT_2026-09-05.md)，當日已授權啟用雲端背景驅動。這是歷史部署證據，下一次部署仍須核對設定及無瀏覽器流量時的實際日誌。部署步驟見 [部署手冊](DEPLOYMENT.md)。

## 退款後的發票補償

- 後端確認退款完成時，若存在已開立或開票結果未明的發票紀錄，系統建立 `invoice.adjustment_required`；取消訂單或退款尚未確認成功不會提早作廢。缺票號時先依原銷貨單查回，查無原票則留待核對，不補開新發票。
- 全額退款把發票與訂單標成 `void_pending`，記錄原因與來源。新建立的汎宇全額退款待辦帶有 `payload.auto_void=true`，背景工作先查詢原票，再於符合原票與帳戶隔離等條件時呼叫 `/cancelInvoice`。
- `/cancelInvoice` 回覆成功仍須由 `/queryInvoice` 查回作廢，才將發票與訂單同步為 `voided`。若送出逾時或結果不明，後續背景與人工處理只補查，不重複送出作廢；重試 8 次仍未確認完成會通知管理員核對。
- 帳戶來源不明、不一致，或遇到需會計核對的情況時，不繞過原有保護。管理端保留填寫原因後人工作廢／核對結果的入口，供歷史待辦及異常處理使用。
- 自動與人工入口共用 `void_refunded_order_invoice`，先鎖定並刷新訂單與發票，再判斷持久化送出標記，避免背景與管理員同時操作造成重送。自動稽核保存 `trigger=automatic_refund`、原退款編號及退款發起者，原始完整退款原因保留於退款紀錄，送往汎宇的原因依既有欄位上限取前 20 字。
- 既有 `invoice.adjustment_required` 缺少 `auto_void=true` 時，仍沿用原本的管理員通知流程，不因部署而批次作廢歷史發票。非汎宇發票也維持原待辦處理方式。
- 部分退款保留已開立狀態，建立 `allowance` 待辦並通知管理員，等待會計依品項、稅別與跨期規則人工處理；不呼叫自動折讓 API，也不新增人工折讓登錄流程。
- 自動流程啟用需部署本版 API，並維持既有背景 reconciliation 啟用；供應商真實退款與作廢端到端驗收另行記錄。

## 管理端銷貨單查詢

管理員可呼叫：

```http
POST /v1/admin/orders/{order_id}/invoice/query
Content-Type: application/json

{"reason":"客服核對逾時訂單"}
```

規則：

- 只允許管理員。已有發票的訂單，即使已退款或取消，仍可查詢該張發票；尚無發票則仍要求已付款。
- 既有發票使用保存的銷貨單號及買方類型查詢，不依現在的訂單資料重建開票請求，也不呼叫開立 API。
- 已有 provider 綁定不可跨供應商查詢／重開；沒有查到發票時保持原狀。
- 供應商仍回報已開立時，不清除本機 `void_pending` 待辦或把 `voided` 改回已開立。查得已作廢才同步完成狀態。
- 原因必填，不另外顯示確認視窗。
- 成功與供應商失敗都寫入 `admin_audits`。
- API 只回傳標準化後的發票號碼、日期與狀態，不回傳原始 provider payload、`auth`、APIKey 或簽章。

## 汎宇 API 契約

測試與正式根網址：

- `https://webtest.einvoice.com.tw/einv`
- `https://web.einvoice.com.tw/einv`

所有請求為 HTTPS JSON，外層包含：

- `companyID`
- `userID`
- Base64 編碼的 `auth`
- 台北時間 `createDateTime`（`YYYYMMDDHHMMSS`，與主機相差需在正負 10 分鐘內）
- `signatureValue`
- `reqData`

`auth` 只是 Base64，並不是加密。程式不得把 envelope、密碼、APIKey 或簽章寫入 log、稽核資料或前端。

汎宇狀態對應：

| 汎宇查詢狀態 | 平台狀態 |
|---|---|
| `0` 或空值 | `issued`；若原為 `void_pending`／`voided` 則保留原狀 |
| `1` | `voided` |
| `3` | `failed`（汎宇退回） |
| 查無銷貨單 | 維持目前狀態，不視為已開立 |

## Email 分工

- 汎宇：依 `notifyEmail` 寄發官方發票通知；B2B 通知包含 PDF。
- Resend：負責註冊、付款、訂單、退款等平台通知。
- 使用汎宇時，只建立站內「發票已開立」，不再建立同事件的 Resend／MailerSend Email。汎宇開票請求仍攜帶訂單買方 `notifyEmail`，由汎宇交付官方發票通知；既有綠界通知行為保持相容。
- 平台「付款成功通知」在後端驗證付款後排入 Email Outbox，不等待開票，不以開票事件代替付款事件。補開舊發票不會因此重寄付款通知；已寄出的舊平台開票信不重送或更名。
- 付款通知與汎宇官方發票通知分開驗收。平台開票成功與站內票號不代表官方信已送達；官方送達回執、失敗清單回收與送達追蹤仍待實作。

## 安全開關與環境變數

```text
INVOICE_PROVIDER=fanyu
FANYU_INVOICE_COMPANY_ID=
FANYU_INVOICE_USER_ID=
FANYU_INVOICE_AUTH_PASSWORD=
FANYU_INVOICE_API_KEY=
FANYU_INVOICE_SELLER_ID=
FANYU_INVOICE_STAGE=true
FANYU_INVOICE_BASE_URL=https://webtest.einvoice.com.tw/einv
FANYU_INVOICE_SIGNATURE_VERIFIED=false
```

2026-09-03 已使用汎宇 WEB API 測試憑證實際驗證 HMAC-SHA256 簽章與 `/queryInvoice`，供應商接受驗證並回傳業務層結果。2026-09-04 汎宇另確認測試帳號已開通會員載具、`EG0478` 為正確代碼，且兩個載具 ID 均應帶入客人 Email。同日以 NT$100 完成 B2C Sandbox 驗收：銷貨單 `SLFT260904102721` 先查無資料、成功開票，再以相同銷貨單號查回發票 `LP06796250`，收件者亦確認收到汎宇官方 Gmail 通知。其後 B2B 亦完成兩種端到端驗收：`EG0478` Email 會員載具與 `3J0002` 手機條碼均成功開票、查回相同票號，且兩封含 PDF 的汎宇通知信皆成功送達。正式環境維持拒絕送出，直到取得正式憑證並重新驗收。

Production 另會檢查：

- `FANYU_INVOICE_STAGE=false`
- 根網址必須是 `https://web.einvoice.com.tw/einv`
- 所有必要憑證皆存在

## 尚未啟用

- `/cancelInvoice`：新汎宇全額退款已有背景自動作廢與查回，管理端保留人工核對；詳見退款流程。
- 折讓：資料表與不可變商品快照已建立；部分退款會建立 `allowance` 待辦，尚未決定跨期與稅額拆分規則，因此不呼叫 `/openAllowance`。
- `/getNotificationFailList`：尚未建立定期回收與管理介面。
- B2C／B2B PDF 下載：B2B 先由汎宇 Email 交付；平台不保存或公開 PDF。

以上項目在正式收款前必須由合作社會計確認，並完成 Sandbox 端到端驗收。

## 驗證基線

- 汎宇 adapter：開立欄位、B2C／B2B Email 會員載具、手機載具、查無資料、未知狀態、秘密遮罩皆有契約測試。
- 發票服務：查詢後開立、查詢復原、禁止跨 provider、運費快照皆有整合測試。
- 管理端：原因驗證、查詢同步與稽核紀錄皆有路由測試。
- Migration：`0011_invoice_provider_snapshot` 建立精確請求與小數快照；部署使用 `alembic upgrade head`，並以完整 downgrade／upgrade 測試核對目前 ORM schema。
- Frontend：一般商品、團購與便當的 Email 載具 payload 與摘要有契約測試；1280px 與 390px 實際瀏覽器檢查通過，公司統編、抬頭與買方 Email 在手機版改為單欄且沒有水平溢位。
