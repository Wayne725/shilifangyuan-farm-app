# 共同購買送單與恢復協定

日期：2026-10-31。本批為本機驗證完成的待部署變更，正式站驗收另計。

## 報價與送單

新 UI 必須取得 quote 的 `confirmation_fingerprint`，送 join 時同時提供 UUID `client_request_id`、含運 `expected_total` 及 `expected_quote_fingerprint`。指紋由後端產生，涵蓋共同購買核心條件、會員／免稅、數量、發票買方類別與配送報價；前端不自行推算或以總額相同視為條件相同。

首次 POST 前，依帳號及 campaign 把 UUID、原數量、完整取貨／配送資料、發票與報價保存於該分頁的 sessionStorage。保存失敗時零 join POST，顯示凍結原摘要；允許儲存後可用原識別與內容重試。`saveCarrier` 是帳號偏好，不加入訂單內容或指紋。共同購買不清除一般購物車。

## 回應遺失與重新確認

- 未知結果、一般 409、503 或已有未知結果後的 422，保留凍結原稿與同 UUID；重整、關店、關團、售罄或 campaign 讀取失敗都仍能恢復原單，不自動另建。
- 相同帳號／UUID／內容回放，後端先查原單，再判斷可變的收單條件；異內容或跨訂單種類回 `409 / X-Error-Code: idempotency_conflict`，不能自動換 key。
- `409 / X-Error-Code: quote_changed` 保證該 key 查無既有單且未 insert；可清除原 snapshot、重讀報價，必須買家再次確認才產生新 UUID。單靠錯誤文字或未分類的 409 不足以作此判斷。
- 首次送單的明確 422 可回到原表單修正；已有未知結果的原 key 不因此丟棄。
- 已取回明確 order ID 後，若合約已變、付款結果未知、已付款／取消或已有新 revision，導向訂單中心，不新增付款或物流。未知付款優先顯示「付款結果仍待核對，請勿再次下單或付款」。已知原單的 snapshot 可清除；後續由訂單中心處理，不能暗中替換為新訂單。
- 每個 API 與外站交接都檢查權威 session 身分。A 的原稿不顯示／重送為 B，同帳號憑證更新仍可正常續行。

## 運費與歷史相容

quote 的 `amount_total` 包含運費；join 初始 Order 仍只含商品小計，物流選擇階段才加入運費。因此配送流程不能直接比較初始 Order 與含運 quote 判定錯價。恢復必須沿用原配送通路、溫層、姓名、電話、地址與發票。

0044 只新增 nullable `orders.group_contract_fingerprint`，不假造既有訂單的完整歷史條件。legacy NULL 僅依已保存的商品／組合 identity 保護，無法宣稱具有完整舊 core 證據。已有新 digest 時禁止有損降版。舊 API 未帶 UUID／expected 欄位仍相容，但不具本次完整去重／報價確認保證；前端也無法從沒有識別的舊未知請求推回可靠 UUID。

## 部署與驗證

先完成備份、0042→0043→0044 migration 與相容後端，再發布新前端；確認跨 origin 的 CORS 暴露 `X-Error-Code`。缺少新 quote 指紋時新 UI 會阻擋新 join，可明確重新取得報價；不得以猜測指紋繞過。回復舊前端會失去新請求的保護，不能當成同等安全版本。

本機 Node 22 定向 25 項通過；共同購買恢復 15 案 × Chromium／WebKit × 390／1440 共 60 項通過；獨立物流啟用 build 的 3 案 × 兩引擎共 6 項通過；既有 LINE／載具結帳 23 項通過。物流 build 只輸出暫存目錄，未更動正式功能旗標。永久測試 `tests/browser/group-recovery.mjs` 預設驗主站 15 案；物流需 `BROWSER_LOGISTICS_TESTS=true`、`--test-name-pattern=shipping` 及相應 `BROWSER_DIST_ROOT`。

瀏覽器 API／付款／物流均為隔離假資料，外站請求中止；這些測試證明 UI 與 client 協定，不代替後端 PG 併發、真供應商或原生 Safari 驗收。WebKit 是 Safari 相近引擎，本次新恢復流程沒有操作真實個人付款。原生 Safari 的歷史／pagecache 調查仍見 `LEGACY_SITE_RETIREMENT.md`；此修正不強制跨 origin 轉址。原始紅綠及執行命令見 `outputs/remediation-completion/group-checkout-ui-validation.md`。
