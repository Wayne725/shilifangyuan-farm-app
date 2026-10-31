# 受限資源下的完整訂單負載驗證

日期：2026-10-31。固定乾淨提交 `4be705a3b5bdffed0088dc6025082319526222a9`；前景與背景混合各獨立執行一次，均通過。兩個 API 程序與 supervisor **合計限制為0.5 CPU／512MiB、swap0**，PostgreSQL及產生負載的程序使用各自獨立資源。這是合成環境的交易與資源驗證，尚未部署，也不是正式站容量承諾。

本頁基底測試的PG限制為1 CPU／512MiB；後續唯讀Render UI顯示正式API為0.5c-512mb、PG18為0.1c-256mb。因此本頁不能改稱現行DB形狀容量證明。更低PG CPU／RAM的對照必須另列獨立run、helper SHA及kernel限額／OOM證據；即使限制相同，managed參數、磁碟與實際worker仍需另外核對。

## 工作量與隔離

每次包含一般商品及共同購買：兩通路各自以1／5／20／50條流程並行、每階100筆，共800條；再以每秒2／5／10條、各30秒預排到達，共510條。每條主要流程包含試算、建單或加入共同購買、建立付款保留；不代表1,310筆都已付款。各closed階段實際峰值均等於指定並行數，每階兩API各承接150個業務HTTP。

另外各有20次真實「上游201已commit、下游TCP才中斷」控制，使用原帳號／原UUID／凍結body到另一API重送，逐次核對回到同一訂單。最後庫存50搶1、相同／異body、跨通路／campaign、回呼重放、退款與legacy無key相容控制另列，不算進上述1,310條。

背景混合在三個固定到達階段前各建立100封真Email意圖，透過原request middleware／reconciliation／Outbox處理。付款、發票、寄信及退款只替換最外層供應商transport，固定100ms延遲；真業務與資料庫交易保留。lifespan與列印停用，沒有外網、正式訂單或實際寄信／扣款。

bootstrap／PG使用network=none，API／generator只加入自有PG的network namespace，沒有host port、Docker socket或家目錄掛載。runtime的source／依賴唯讀；bootstrap僅將經離線hash核驗的依賴裝入自有可寫deps。執行期間沒有並行跑本批其他CPU測試。背景重疊發生於固定到達階段，closed階段未額外加入Email backlog；不是全程固定背景壓力或長時間soak。

## 實際結果

| 指標 | 前景 | 背景混合 |
|---|---:|---:|
| 主要流程 | 800＋510 | 800＋510 |
| 記錄的業務HTTP | 4,550 | 4,556（含6次reconcile） |
| HTTP 200／201／預期409 | 1,617／2,830／103 | 1,623／2,830／103 |
| 非預期5xx／逾時／pool timeout／OOM | 0／0／0／0 | 0／0／0／0 |
| 已commit後計畫斷線 | 20 | 20 |
| 訂單＝logical submissions | 1,462 | 1,462 |
| payment attempts＝保留列 | 1,336 | 1,336 |
| API cgroup記憶體峰值 | 328.89MiB | 333.98MiB |
| launcher總生命週期（含setup／cleanup） | 143.35秒 | 173.29秒 |

103個409包含兩通路各50搶1的98個合法拒絕及明列的5個衝突控制，不能當非預期服務錯誤；20次刻意斷線亦分開計數。health／identity／metrics探測不含於業務HTTP分母。

兩次各保留20筆已付款、20筆退款責任；合成收到4,000元＝保留已付款2,000元＋退款2,000元。前景保留待退款意圖，背景完成退款、待退款責任歸零。各40個發票意圖與40個去重付款事件一致。背景供應商ledger為寄信380、發票查詢80、開票40、退款20、作廢20；非query操作按業務key接受次數皆為1，不把它擴稱真供應商恰好一次。

背景三個到達階段分別有10／11／11個實際pending或processing重疊sample。每階100封來自合法NotificationService，另外80封來自業務通知；沒有用假backlog或關閉worker湊結果。

## 延遲與瓶頸徵象

下表為每個100筆closed階段的完整流程p95，單位毫秒。流程時間從取得client semaphore後開始，含各HTTP往返；它不是單一API延遲，也不是50個DB交易同時執行。

| 並行流程 | 前景一般 | 前景共同購買 | 背景設定一般 | 背景設定共同購買 |
|---|---:|---:|---:|---:|
| 1 | 61.738 | 61.576 | 61.220 | 60.793 |
| 5 | 282.060 | 199.470 | 284.496 | 204.299 |
| 20 | 1,111.218 | 977.220 | 1,282.933 | 1,087.961 |
| 50 | 2,595.530 | 2,066.717 | 2,678.227 | 2,196.502 |

固定到達的scheduled-to-complete p95含產生器排程延遲：

| offered流程／秒（各30秒） | 前景p95 ms | 背景混合p95 ms |
|---|---:|---:|
| 2 | 61.892 | 58.752 |
| 5 | 55.726 | 52.903 |
| 10 | 35.748 | 43.222 |

順序會暖化連線／ORM／cache，不能從此表推論提高rate會讓網站更快。10/s只量30秒；不外推成整日可持續容量。

50並行時各API連線池峰值都到10；前景一般訂單兩worker的pool acquisition p95分別852.994／648.364ms，最長2,016.687ms。此指標含取得或建立連線，不是純SQL耗時。API cgroup確有CPU節流，增加並行數沒有等比例增加吞吐；這些是本次排隊與CPU上限的徵象，尚未證明正式站的單一根因。

目前已實作的保護是固定送單識別、DB唯一鍵與庫存鎖、付款未知結果保留、可重試503及背景失敗隔離。尖峰時仍須看完整流程p95、連線取得、CPU節流及待辦年齡；若要擴容，應用同一profile先驗增加CPU或分離背景工作，再評估pool／worker，而不是直接放大連線數。舊無client_request_id呼叫保留原本四送可建四單的相容性，沒有宣稱所有舊客戶端都自動去重。

## 來源、清理與限制

前景run `limited_3bc4993d59ea424aa481045ce65974a8`；背景run `limited_5e452a2891cc4ad4ba4e13eaca86a3cc`。原始證據位於owned checkout的`outputs/container-load-candidate/`，摘要為根工作樹`outputs/remediation-completion/auth-load-full-{foreground,background}-summary.json`；兩次來源檢查前後均為同一4be705a且tracked clean。

新版helper三SHA：launcher `2f3f0a7ffdf4cc84f9469a4fc1f7c8ce15cefab70115dc54cc41c1dab4fee6ed`、driver `5ca0a678a4f9455ad1244702744a9f69633c7c043667fa375de8f5061edd906b`、native `5abf1641a93f4c9361fbc1965f6f881436420518611736e51631eab79895be0a`。144項pure guards及一次新版40＋4 smoke先通過，才執行兩次full；原先setup ENOSPC及所有護欄紅保留。

兩次schema均移除、剩餘load schema為零；八個已知自有容器full ID另行唯讀inspect確認全不存在，兩API children皆正常或受控TERM退出，既有共用PG仍運行。沒有global prune、停VM或清理未知schema。full工作deadline540秒、host wait600秒，setup與cleanup另有上限；未發生逾時。

尚未涵蓋最新版便當完整負載、真第三方網路／故障、正式DB資料規模、索引query plan、備援、長時間記憶體穩態及正式流量。原生Safari舊畫面調查與登入相容性另見`LEGACY_SITE_RETIREMENT.md`、`CLIENT_REFRESH_COORDINATION.md`；不能用API壓測替代實機與正式驗收。
