# 金流與發票驗收第一輪修復

日期：2026-09-05（Asia/Taipei）
基準：`codex/v2-social-commerce`／`dcf8bf04cdd0cd3a85ec63bb9bd0e61746624bf9` 加本機工作區修改

## 結論與範圍

第一輪本機程式修復與隔離測試完成，**尚未提交、推送、部署或作正式營運放行**。這輪沒有連正式資料庫修改業務資料，沒有新扣款、退款、開票、寄信，也沒有改 Render 方案或 Secret。

以 [初次驗收](ACCEPTANCE_AUDIT_2026-09-05.md) 的五項 P1 為範圍，依診斷與 TDD 流程先保留失敗重現，再修程式及回歸測試；API 文件用來限制修改邊界，不改造供應商協定。A01 雲端排程、實際供應商收斂與送達仍未驗收；不能把本機綠燈當成客人已收到發票。

## 修復內容

| 編號 | 本機修復 | 邊界／仍待完成 |
| --- | --- | --- |
| A01 | 增加 FastAPI lifespan 定期驅動，重用 reconciliation、Outbox、原有單 process 防重疊；完成及失敗類型寫入日誌 | 預設關閉。Render 要啟用並驗證；GitHub 舊 main／Secrets 的空跑問題仍待處理。不是獨立 worker，也不取代監控告警 |
| A03 | 已退款但已有發票者可填原因重查；使用歷史銷貨單號／買方類型，不重建或發送開票。保留待作廢狀態及原作廢時間 | 只開放查詢及狀態同步；未付款且無發票仍拒絕。既有供應商綁定、管理權限、原因與稽核保留。自動作廢／折讓未完成 |
| A04 | 一般、團購、便當的取消訂單均不能新建付款；已發出的平台 checkout 連結也檢查取消狀態 | 無法撤回已在供應商開啟的付款頁，故晚到帳與退款保護繼續保留 |
| A05 | 增加 `next_reconcile_at` 公平輪詢與查詢間隔；失敗也保留間隔，批次長時間執行依實際取件時間計算 | 保留原補查期限、provider 選擇及金額／交易驗證。PostgreSQL 多程序併發與大量負載另測 |
| A08 | refresh 401 才清 session；503／斷線不假裝登出，介面提供重試；共用同一次 refresh 避免同頁重複換發 | 不放 token 到 localStorage、不改 HttpOnly cookie；本機模擬不能排除雲端代理設定、Safari 或跨分頁 session 的其他問題 |
| A02 | 額外修正舊供應商展示 Email 使讀取回應 500 的問題 | 只放寬讀取 schema 為字串，JSON 欄位名稱不變；新增／修改依然驗證 Email，不重寫歷史資料 |

## 供應商格式及相容性

核對來源：

- 雷門《線上多元支付 API 介接規格 v1.6.5》，本機檔案 `Downloads/雷門支付整合_線上多元支付 API介接規格_v1.6.5(2).pdf`：付款頁、查單、退款、Header、加密／Hash 與狀態對照。
- 汎宇《電子發票 API 說明文件》及《API 範例 V4.1》：envelope、開立、銷貨單查詢、買方類型及載具欄位；並保留先前供應商已確認的 EG0478 Email 會員載具與實測結果。

本輪沒有修改 `raygate.py` 或 `fanyu_invoice.py` 的協定轉換，也沒有改變它們的 URL、Header、AES／Hash／HMAC、金額、銷貨單號、統編、載具及 `notifyEmail` 格式。既有 B2C／B2B、EG0478／3J0002、逾時先查後開、重複事件與退款不明只查單等測試仍通過。

金流與發票繼續分離：付款結果仍由後端向金流確認；查回成功才排開票；發票交付仍交給汎宇。雷門現有退款格式沒有金額參數，仍不開放推測性的部分退款。測試憑證與正式憑證不混用，舊 provider 的交易不因全域設定改變而交給新供應商處理。

更正文件中的寄信描述：現行程式在開票後還會排一封平台號碼通知，不是汎宇官方 PDF，也不是確認汎宇寄信失敗才觸發。**付款通知、平台開票通知、汎宇官方發票信不能互相作為送達證據。** 本輪修正文檔，不改供應商寄信流程。

## Migration

新增 `0013_payment_poll_schedule`，接在 `0012_raygate_payments` 後方，只在 `payment_attempts` 新增 nullable `next_reconcile_at` 與索引。舊 0001～0012 沒有改名、刪除或修改；既有付款金額、狀態、外部交易資訊不回填重寫。

隔離 SQLite 測試先保存舊付款，再執行 0012 → head → 0012，逐欄核對原資料及 provider payload 未變；升級後 Alembic metadata check 無差異。PostgreSQL 離線 DDL 已產生 `TIMESTAMP WITH TIME ZONE` 及索引，但本機沒有 PostgreSQL server，因此**尚未實際執行 PostgreSQL migration／併發**。舊 CI 的 PostgreSQL 成功紀錄不算這份新 migration 的證據。

## 驗證結果

| 檢查 | 結果 |
| --- | --- |
| 後端既有、權限、交易及新回歸案例 | 620 通過、0 失敗 |
| 前端標準與登入還原案例 | 31 通過、0 失敗 |
| TypeScript／Vite 正式 build | 通過 |
| API route manifest | 175 項重新匯出與已保存版本完全相同 |
| SQLite migration 往返／資料保留／metadata | 通過 |
| PostgreSQL 離線 DDL | 通過；非實際資料庫升級 |
| 無瀏覽器請求的 lifespan 模擬 | 自動開票、保存票號及下一輪平台寄信通過；HTTP／Email 供應商使用假端點 |
| Chromium 1366px、390px | 503 重試、恢復登入、重新整理、退款後填原因查票通過；每個寬度 page error 0 |
| `git diff --check` | 通過 |

UI 診斷使用全新隔離瀏覽器及本機 build，所有 `/v1/` 請求攔截為假資料，外部網路請求阻擋。這不是正式 API、真實 refresh cookie 輪替或汎宇寄信驗收；尚未重跑 Safari／手機實機。

驗收案例已加入本機標準測試入口：後端 `testpaths` 包含 `audits`，前端 `pnpm test` 包含 `tests/audits/*.test.mjs`。尚未推送，所以雲端 CI 沒有執行這批修改。

可重跑：

```sh
DATABASE_URL=sqlite+aiosqlite:///:memory: PYTHONPATH=backend .venv312/bin/python -m pytest -c backend/pyproject.toml backend/tests backend/audits -q
pnpm test
pnpm typecheck
pnpm build
```

## 下一個驗收關卡

1. 準備資料庫備份，在隔離 PostgreSQL 驗證新 migration 與併發，再核對部署分支／版本。
2. 確認既有待辦與供應商環境後部署，啟用常駐處理；同時修正 GitHub 預設分支及 Secrets，追蹤實際執行紀錄。啟用會處理既有退款／開票等待辦，不是唯讀操作。
3. 依核准範圍重走一次付款，付款後關閉瀏覽器，核對訂單、雷門交易、汎宇票號、銷貨單查回及官方收信。Email 會員載具／手機條碼／B2B 分別驗證。
4. 退款測試分別核對เงินจริง退款與發票調整待辦；作廢／折讓完成流程及收信失敗追蹤仍是後續工作，不能只看退款按鈕成功。

先暫緩的物流不在下一輪真實驗收範圍。其餘待辦含訂單歷史分頁、失敗工作管理、正式通知送達追蹤、負載與多實例安全性，見 [TODO](../TODO.md)。部署旗標及回復注意事項見 [部署手冊](DEPLOYMENT.md)。
