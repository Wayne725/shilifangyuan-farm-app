# 通知去重與郵件交付邊界

日期：2026-10-31

## 新版的本地保證

NotificationService 的相同 `(user_id, event_type, dedupe_key)` 回放或並行只保留一筆 canonical 站內通知，以及最多一筆對應的 send_email outbox 意圖。原始 key 保留在通知 JSON；SHA-256 hash 用於資料庫 unique index。`None` key 保留原本每次新增的行為；不同使用者或事件類別互不影響。重播不覆寫第一筆通知內容或已排定的郵件。

通知、email 意圖與 caller 其他變更共享外層交易。去重使用 INSERT ON CONFLICT，不 rollback 整個 session。domain worker 將通知效應與 source outbox 的 completed 狀態一起提交；中途例外可整體 rollback。若資料已提交但來源仍需重播，資料庫唯一鍵也會阻止新增相同本地意圖。

這項保證只適用走新版 NotificationService 且明確提供 key 的通知。原本未帶 key 的通知、原始 domain outbox 事件來源去重與實際外部郵件交付，分別有各自邊界。

## 0043 與歷史資料

0043 為相同 user/event/raw key 的舊通知，依 `(created_at, id)` 選最早一筆填入 dedupe_hash；其他重複歷史不刪除、不改寫內容或已讀狀態。回填以 500 筆分批讀取，不將全部通知 JSON 留在記憶體。

舊通知 delivery_version=0；新版 repository 明確寫入 1。舊 send_email 沒有可靠的通知關聯，因此 notification_id 保持 NULL；不依收件人、標題、時間或 aggregate_id 猜配對。回放命中 legacy canonical 時只沿用站內通知，不自動新增 email。這樣可以避免不確定的重寄，但不能保證歷史漏信會自動補寄。確定需要補寄時，應由管理員確認後使用明確的新操作 key，不能刪掉舊去重資料來重試。

新版 email intent 以 nullable notification_id 外鍵及 unique index 關聯 canonical 通知。其他 outbox 類型及既有歷史 NULL 值保持原行為。有新版 delivery_version 或已關聯 email 意圖時，降版會拒絕移除防重複保護。

## 切換順序

1. 先停止並 drain 舊通知 worker 與其他會發布通知的舊程式實例／交易，包含直接發布通知的 API 流程。保留佇列及所有歷史資料。
2. 備份並執行 0043；確認舊通知與 outbox 筆數未減、legacy email 未被猜測配對、canonical unique index 與 email intent unique index 存在。
3. 讓所有通知 writer／worker 都使用新版，再恢復通知處理。舊版程式仍可忽略新增欄位，因此混跑時無法保證舊 writer 不另造 NULL-key 通知；不能在混跑期間宣稱完整去重。
4. 先驗證同 key 回放與正常新 key，再檢查 legacy 個案。若需回復，不直接撤掉已有新資料的索引或欄位。

這是部署前置要求，本次修復未操作正式 worker、未執行正式 migration。

## 外部郵件的未知結果處理

外部「接受／排入」不等於收件人已收到。worker 仍使用穩定的 `outbox-{event.id}`；不更換意圖 id 來自動重寄。

- 完整明確的 4xx 拒絕（不含 408/409），以及以結構化例外鏈證明的 DNS、連線被拒或 TLS 憑證驗證前失敗，可以備援或重試。不能依錯誤文字猜測尚未送出。
- 逾時、回覆斷線、5xx、409、成功回覆壞 JSON 或缺少必要 message id，都屬結果未知。這不是宣稱供應商已接受，而是無法證明未接受；立即停止自動備援／重寄。
- MailerSend 的 202 ALL_SUPPRESSED 且無 message id 是已知未寄出，明確失敗並停止備援，不能用另一家突破 suppression；正常 202 加 message id（包含 sending paused）是已接受排入。
- 未知沿用 Outbox FAILED，payload.email_delivery.state=unknown；不改既有 enum。待處理問題會顯示「寄送結果未知」、供應商與可取得的供應商編號，供管理員查詢。
- send_email、Email 驗證信、密碼重設信的 stale PROCESSING 一律保守轉 unknown，不自動設回 PENDING。其他背景工作原規則維持。
- 舊 PENDING mail 曾嘗試卻沒有明確 rejected／人工授權階段者，會在外發前隔離為 unknown；舊 FAILED mail 曾嘗試且沒有明確 rejected 階段者，列表與 API 直接要求核對，不依舊 last_error 文字猜測。fresh attempts=0、新版明確 rejected 可照常處理。
- 付款／退款通知仍須符合原訂單狀態門禁。未知工作重試還須明確勾選「已查詢供應商確認未寄出，並了解重新排入仍可能重寄」；server 強制確認並留下 audit／前次 receipt。授權在 claim 時被消耗；該次中斷後須重新核對，不能永久沿用確認。
- 驗證或密碼重設信未知時，顯示由本人重新申請，不直接重送舊憑證。正常新申請與正常郵件不受阻擋。

供應商已接受後，若最終 DB commit 未成功，工作會轉 unknown；若 DB 完全不可用，先保留已持久化的 PROCESSING，恢復後 stale 保護阻止重寄。若 commit 其實已成功、只是回報錯誤，重新讀到 COMPLETED 就保留完成狀態，不降回可重送。

## 官方契約與驗證界線

Resend 的同 key／同內容可在 24 小時內取得同回覆；409 可能代表另一請求仍在進行或相同 key 曾配其他內容。[官方 idempotency](https://resend.com/docs/dashboard/emails/idempotency-keys)

Resend 對 500／503 建議稍後再試，但沒有在該錯誤契約承諾無副作用；本程式採保守 unknown，避免跨供應商重送。明確的 client validation／授權／限流拒絕另行處理。[官方錯誤契約](https://resend.com/docs/api-reference/errors)

MailerSend 的 202＋x-message-id 是非同步排入；ALL_SUPPRESSED 無 id 表示未寄。本次查閱的 send API 未載明寄送 idempotency，現有 adapter 也不送這類去重鍵，不假設其有保證。[官方寄信契約](https://developers.mailersend.com/api/v1/email)｜[官方錯誤及限流](https://developers.mailersend.com/general)

舊版雙供應商接受紅保留於 `outputs/remediation-completion/email-acceptance-window-repro.py/.json`。修復後對照 `email-acceptance-window-green.py/.json` 證明首次未知即停止備援；全部使用真 adapter、全假 transport，0 真實網路。完整 commit／stale／API 確認／正常流程證據見 `email-delivery-unknown-validation.md`。

這些保護防止在未查明時自動重寄，不宣稱網路、供應商與收件端能提供端到端 exactly-once delivery。
