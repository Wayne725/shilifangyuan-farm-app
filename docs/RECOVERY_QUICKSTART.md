# 緊急停止交易與 Render 資料庫還原

1. **停止新交易與送件。** 在 Render 暫停呼叫 `/internal/reconcile` 的外部排程，停止所有仍可連到正式資料庫的 API、對帳與列印工作。可在對外入口先顯示維護訊息。以 `RECOVERY_MODE=true` 啟動專用的隔離 API；這個模式會關閉付款、付款回呼、發票送件、對帳和列印。舊實例若仍在跑，仍能送件。隔離 API 的 `/ready` 預期回 503 和 `reason=recovery_mode`，因此直接把正式服務改成此模式再觸發一般滾動部署，可能讓 Render 繼續保留舊實例。已開啟的外部付款頁仍可能入帳，請保留金流商紀錄。
2. **還原到新資料庫。** 在 Render 選原 PostgreSQL 的 **Recovery → Point-in-Time Recovery → Restore Database**，記下切點時間與時區，以新名稱建立還原資料庫。若使用已下載的邏輯備份，把 Render 的目錄格式匯出解開後，以相符的 PostgreSQL 客戶端執行 `pg_restore --format=directory --no-owner --no-privileges --dbname="$RESTORE_DATABASE_URL" 備份目錄`，連線必須指向新的空資料庫。
3. **先查資料，再恢復服務。** 在隔離環境明確升級副本結構並限制供應商連線，核對訂單、付款、發票與列印工作。保留原資料庫和備份，比對切點後的實際金流與發票，確認待處理交易不會重複送出後再切換。詳細命令見 [部署手冊](DEPLOYMENT.md)，Render 步驟見 [Render 官方還原說明](https://render.com/docs/postgresql-backups)。

11/11 已完成一次正式備份的本機還原。恢復時間 1 小時、資料差距 15 分鐘、附件與媒體備份以及供應商自動對帳，安排於此批上線後演練。
