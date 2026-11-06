# 憑證入口共用配額

日期：2026-10-31。七個既有憑證入口改用同一資料庫的滑動視窗，避免不同 API worker 各自取得一份額度。這是登入防護的正確性修復，沒有新增原本不存在的 gate。

| 入口 | 沿用額度／key | 沿用成功重設 |
|---|---|---|
| login | 10／300秒，IP＋email | 只清本帳號；IP 計數保留 |
| register | 5／3600秒，IP | 不清 |
| register-existing-member | 5／3600秒，IP＋社員編號 | 不清 |
| verify-email | 5／3600秒，IP＋email | IP＋本帳號皆清，保留同 NAT 驗證流程 |
| resend-verification | 5／3600秒，IP＋email | 不清 |
| forgot-password | 5／3600秒，IP＋email | 不清 |
| line-start | 10／300秒，IP | 不清 |

refresh、reset-password、LINE callback／登出沒有新增配額。管理、付款、發票及上傳等其他限流仍使用原本的程序記憶體；不能以本修正宣稱全站已共用配額。

## 儲存與交易

Migration `0047_credential_rate_limits` 下接 `0046_reconciliation_retention`，只新增 `credential_rate_limit_buckets` 與 expiry/key 索引。key 是帶固定 domain 的 HMAC-SHA256，使用既有 JWT_SECRET；表內不保存明文 IP、email 或社員編號。每桶只保留視窗內最多 5／10 個計數時間。

PG 依 HMAC key 固定順序 upsert／持鎖，取得全部桶後讀 DB 時鐘；兩桶全部可用才一起計數。任一桶滿額則整筆 rollback，不計入另一桶。SQLite file 使用短 `BEGIN IMMEDIATE` 交易。配額由獨立短 connection 提交，錯誤帳密及後續業務 rollback 不會退還已使用配額。

成功登入／Email 驗證先 flush 原業務異動，再於原 auth commit 前重設指定桶。reset 故障會 rollback 同筆 User／驗證 token／RefreshSession，不先送出成功 Cookie。LINE start 先解析原 bearer，通過配額才執行原有使用者查詢及連結規則。

配額 I/O 合計預算 2 秒，consume 後只將剩餘預算交給 reset；密碼雜湊等業務時間不扣在其中。PG consume 使用 transaction-local 250ms lock timeout／1秒 statement timeout。暫時故障回 `503`、`Retry-After: 2`、`X-Error-Code: credential_quota_unavailable`，沒有退回各程序獨立計數。原 `429` 保守 Retry-After 契約保留。

COMMIT 回應遺失或取消後已排隊的 SQLite 操作，仍可能計入一次 HTTP 嘗試；不承諾所有 503 都零計次，也不在 helper 內盲重送。

## 測試記憶體庫與清理界線

private SQLite `:memory:`＋StaticPool 會讓不同 Session 共用物理 connection，不能直接以新 Session commit／close，否則可能提交或回滾呼叫者資料。此路徑只使用已鎖定 aiosqlite 0.22.1 的 worker queue：整個配額動作在一個 callback 完成；已有物理交易時回 503，不動呼叫者交易。成功 reset 在既有 auth DML 交易內更新，不自行 commit。能力或版本不符時回 503；依賴升級須重新驗證此窄介面，不能據此宣稱其他記憶體庫業務 Session 已全面隔離。

背景 reconciliation 在既有遙測保留之後，以獨立短交易最多清理 128 個過期桶／1秒；PG 跳過已鎖列。只刪新配額表，不刪會員、token 或交易資料。故障只延後清理並記錄錯誤種類。JWT_SECRET 輪替會使舊桶失效；舊列仍按到期時間清理。

## 驗證與發布

- 真 PG、真 auth router、兩個獨立程序各送 11 次：修前合計 20 次進入帳密判斷；修後精確 `10×401＋12×429`。單程序正控制為 `10×401＋1×429`，使用不同 IP 及帳號配額。
- `test_credential_rate_limit.py`、`test_credential_rate_limit_routes.py` 與 PG concurrency 測試涵蓋兩 key 原子性、視窗邊界、成功重設、同 NAT 驗證、鎖故障恢復、業務 rollback、排隊取消及有界清理。
- 真 PG reset 持鎖使登入／驗證回 503 且不 Set-Cookie，User／token 異動回滾；解除後同合法憑證可成功。
- 0047 已納入 SQLite／PG migration 測試與 release invariant；完整整合數及未完成項以 [修復追蹤](REMEDIATION_STATUS.md) 為準，不將定向測試冒稱正式站驗收。

部署先完成 migration 再上新版 API，Web 待 API 就緒才發布。不能只因新表可保留，就把舊 API artifact 視為安全回復：目前正式 c529 的 migration graph 只到0040，而 startup.prepare 仍會執行 Alembic upgrade；DB已到0047後，該 artifact 可能無法識別 revision。必須先驗證保有完整已用 graph 且相容新資料狀態的回復版本，或以前向修復處理；Web單獨回復、API／DB維持相容新版是另一條驗收路徑。

單獨downgrade0047只移除此配額表並重置計數，不代表0041–0046可以任意降版；其歷史資料保護仍適用。舊程式也會恢復各程序分開計數的限制，不能視為相同安全效果。跨分頁 Cookie 恢復另見 [前端續期協調](CLIENT_REFRESH_COORDINATION.md)。
