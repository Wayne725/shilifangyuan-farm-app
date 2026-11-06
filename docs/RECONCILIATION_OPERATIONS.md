# 背景結算的隔離、監測與驗證

本文件對應0045_reconciliation_runs、0046_reconciliation_retention及OPS01。背景巡檢成功表示本輪階段沒有未處理例外，**不表示每筆付款、發票、郵件或列印已完成**；待辦／失敗／未知結果仍須分別查核。列印是獨立worker，不由此runner處理。

## 階段與交易

付款補查、活動費用、現場付款保留、共同購買提案／結算、社員提案、活動、便當狀態／排程及Outbox依原順序執行。每段成功後結束本段transaction；未處理錯誤rollback本段未提交部分，再繼續後段。原helper為claim、寄送及外部交付所做的中途commit保留，因此不宣稱整個階段為單一全有或全無交易。

沒有外層大transaction或以SAVEPOINT包住會自行commit的helper。前段已提交進度不會被後段rollback清掉，失敗段未提交寫入亦不能被後段偷渡提交。原商務的版本、父層鎖、庫存／名額、通知去重及外部unknown規則保持。

正常`/internal/reconcile`仍回原九個int欄位；若有未處理階段或可恢復的監測寫入錯誤，先完成後段再回安全503。背景驅動記錄安全階段／錯誤類別，不記exception body、SQL parameters、token、收件者或供應商payload。`CancelledError`仍向上傳遞，先rollback當前段並盡力保存cancelled；程序被強制終止／DB無法寫入時可能留下running，不能將它當成可安全重送的業務工作。

## 持久執行記錄

`reconciliation_runs`每輪一列，記DB時間、來源、running／succeeded／partial_failure／cancelled、目前階段、固定十個業務階段（加可選monitoring錯誤）、duration、原九項數量及安全錯誤類別。每個worker只更新自己的id；後台最近輪次、最近完整巡檢皆按遞增id選取，較早worker晚完成不會把較新輪次蓋掉。

監測使用來自傳入session bind的獨立短session，不偷用globalSessionLocal。正常clean worker先持久化開始；每段商務commit／rollback、商務鎖結束後，才一次更新「前段完成＋下段開始」。正常輪次有11次監測寫入／commit，沒有逐業務項目增加監測查詢。

開始監測的第一次INSERT也可能失敗。此時只記安全monitoring錯誤，不替caller commit／rollback，十個商務階段仍照原邊界執行；既有advance在首個可寫邊界重試新增run，保存partial_failure與安全摘要，最後internal入口回503。故障到首次成功補記之前沒有持久心跳；若DB始終不可寫，也不能宣稱已保存run。第一次INSERT的真SQL紅／綠與正常控制見[起點故障驗證](../outputs/remediation-completion/ops01-start-insert-validation.md)。

SQLite caller若已flush而持有實體transaction（包含file DB與memory StaticPool），第二session可能共享connection或受write lock阻擋。此時不提前替caller commit／rollback，而延後至第一個原業務交易邊界，保存`deferred_reason=sqlite_existing_transaction`；首段期間沒有獨立持久心跳，開始時間在邊界補記。未flush的caller ORM物件亦不會被監測session偷flush。SQLite補記時間使用DB相同精度，避免同秒日期文字CHECK誤判。

來源明確區分：

- `periodic`：內建定期driver。
- `request`：既有網站請求補跑，包括驗證信快速驅動。
- `internal`：帶secret的內部結算入口；可能是排程或人工呼叫，不能單憑它證明GitHub排程已跑。
- `job`：直接結算工作呼叫。

讀監測endpoint本身（含trailing-slash redirect）不會喚醒request補跑；其他既有網站請求仍有原fallback。監測頁還可能伴隨登入／一般API，因此判定「無流量仍有驅動」必須看periodic或已核實排程紀錄，不能把request來源當成外部cron證據。

## 後台與待辦

`GET /v1/admin/reconciliation-status`僅admin可讀，no-store；不回工作payload、收件者、聯絡資料或secret。`/health`維持程序存活、`/ready`維持DB可用檢查，兩者不冒充巡檢健康。

問題回報頁預設顯示最近執行、完整巡檢及其來源、失敗階段與安全類別、待辦數。僅該admin頁每30秒更新；視窗隱藏不poll、離頁停止並可取消讀取，讀取失敗不清掉原重試表單，也不宣稱新資料已取得。

Outbox分開顯示到期pending、未到重試時間、processing／10分鐘stale、failed、包含舊資料的寄送未知。付款與便當的候選SQL由worker與監測共用；活動及社員提案依到期clock。全部為聚合SQL，待辦增至101筆不逐筆讀payload。數字有包含／子集合關係，**不能相加當成總工作數**；例如stale屬processing、unknown可能屬failed。保留原未知郵件的人工確認及auth重新申請限制。

## 遷移、回復與保留

0045接0044，只新增監測表與索引，不改交易資料。部署前先依既有startup/migration程序完成升級，再接新API／後台。API前端若暫不同步，監測讀取錯誤不應阻擋原訂單或重試操作。

有監測歷史時0045降版拒絕刪表；回復舊程式可保留新表，不需為回復code而刪歷史。正式舊資料、金流、寄信與設備均未因本次本機回歸而驗收。

0046只新增完成時間／id的終態partial index，升降版均不刪資料。保留profile已另批實作；新版部署後，在任何internal／job／request／periodic輪次的全部商務交易結束後共同觸發，不依賴預設關閉的periodic。不是每個HTTP掃描；每個engine先做process monotonic gate，最短一小時才嘗試DB lease，跨worker亦以DB時間／私有控制列序列化。

預設只刪本表finished_at早於30天的終態，排除最大id、最近succeeded／partial_failure／cancelled、傳入的目前輪次及所有running。每批最多100筆、每輪最多5批，各批獨立短session與commit；每批重驗owner／lease及保護條件，舊worker不能刪除或覆蓋新worker的清理狀態。候選與DELETE都有限制及索引，不讀phases／業務payload。清理只改本表與專用SystemSetting鍵`reconciliation_runs_retention`的lease／安全狀態，平台設定API不列出也不覆寫該鍵；交易、稽核、通知、付款與社員歷史全部不在刪除範圍。

可選環境設定如下，未設定即採安全預設；與正式環境目前是否已部署或設值是不同事實：

| 設定 | 預設 | 範圍 |
|---|---:|---|
| RECONCILIATION_RETENTION_ENABLED | true | 明確停用後會累積，後台顯示停用 |
| RECONCILIATION_RETENTION_DAYS | 30 | 1–365天 |
| RECONCILIATION_RETENTION_BATCH_SIZE | 100 | 1–100筆 |
| RECONCILIATION_RETENTION_MAX_BATCHES | 5 | 1–5批 |
| RECONCILIATION_RETENTION_INTERVAL_SECONDS | 3600 | 3600–86400秒 |
| RECONCILIATION_RETENTION_LEASE_SECONDS | 300 | 30–1800秒 |

清理錯誤只保存安全類別與已提交筆數，不能rollback已完成商務或改九int成功回應；CancelledError仍向上傳。中途失敗保留已提交小批，下一次DB時間允許後續作。API另提供最後開始／完成／成功、清理狀態、已刪數及索引LIMIT1取得的最舊可清時間；不回lease owner或資料payload。舊API沒有cleanup欄位時，原後台仍可用。清理失敗與商務巡檢成功是可同時存在的兩個狀態。

30天是分批清理門檻，並非任何負載下都保證資料最長只留30天。500筆／小時未必追上任意worker數的寫入；後台保留最舊待清時間以觀察積壓。最新各終態及全部running有意保留，程序反覆在開始後崩潰時，running仍可能累積，需核對原因，不自動刪除未結束證據。完全沒有巡檢驅動或明確停用時也不會清理。正式部署前仍須完成這個profile的整合驗證，不能把本機測綠說成正式TTL已生效；見[保留驗證](../outputs/remediation-completion/retention-validation.md)。

## 本機驗證與成本

正式紅、SQLite caller八格、partial503／cancel／安全摘要、PG多worker與caller未提交交易、SQLite／PG migration及兩瀏覽器證據集中於[驗證報告](../outputs/remediation-completion/ops01-validation.md)。不把各次重複回歸加總成新的完整suite數。

相同商務helper、只替換f35基準runner的合成成本對照：每組三次中位數，本機PG空輪32.9→43.3ms、25筆假工作74.4→86.9ms；SELECT完全相同、固定多11 SQL。實體commit空輪8→22、25筆58→71，除11監測commit外亦包含真正拆開原相鄰讀交易的成本。每列phases/counts約802B；單worker每分鐘一輪約0.5M列／年，故需要前述保留profile。沒有真網路寄送，不把此數字延伸為正式PG容量、網路延遲或磁碟佔用保證。

保留profile另有1504列合成樣本：SQLite／PG都只刪5×100筆，84張業務表前後相同；一次profile38 SQL／7commit，本機各約17.9／28.5ms，同process再次觸發0 SQL／0commit。這是每小時有資格才發生的額外成本，不是每個巡檢或HTTP的成本，也不是正式負載上限；完整EXPLAIN／SQL與限制見保留驗證。
