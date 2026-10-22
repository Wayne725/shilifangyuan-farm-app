# LINE 社員驗證與便當現場付款發布

日期：2026-10-22

## 發布內容

- LINE 社員入口改為電話與名冊 Email 核對，採用 LINE 顯示名稱，保留原信箱驗證碼及既有會籍邊界。
- 便當場次可啟用合作社現場付款；後台確認收款及更正誤標，保留對帳稽核，發票交由現場 POS 處理。
- 飛鵝接口與持久化列印工作隨版部署，但正式列印預設停用；沒有設備或新密鑰時不發送列印。
- 官方帳號「十里方圓(測試版)」雙入口已儲存，並已換為純文字圖片。
- CI 完整 pytest 現在也設定一次性 PostgreSQL，避免略過現場收款併發測試。

## 部署前紀錄

- 正式 API 當時為 `0d98f80`，部署分支 `codex/v2-social-commerce`；Auto-Deploy 因指定提交部署而停用。
- Render 資料庫於台灣時間 17:32 建立邏輯 export，Recovery 顯示成功及下載項目；另有 3 天 PITR。未下載私人資料。
- 本機後端 1,679 項通過，4 項需要 PostgreSQL 的測試另跑；PostgreSQL 完整組及該 4 項共 33 項通過。
- 本機 PostgreSQL fresh upgrade → downgrade base → re-upgrade head 通過，最新 revision `0030_feie_print_jobs`。
- 前端 68 項、TypeScript 與正式建置通過。

## 部署與正式驗收

`50d1569` 的雲端 CI [39756632831](https://github.com/Wayne725/shilifangyuan-farm-app/actions/runs/39756632831) 全部通過：後端 1,683 項、PostgreSQL 29 項、前端 68 項與 API 契約；完整 pytest 中的現場付款 PostgreSQL 測試未略過。本機 50 項 LINE／現場付款／出單瀏覽器測試通過。

發布分支已快轉至 `50d1569`。Blueprint 自動同步觸發的 `dep-dbctl7tg1s2s73eght30` 失敗；該同步把 `APP_ENV`、`WEB_BASE_URL` 及原本正式金流／發票設定套回舊範例，並嘗試改免費規格。部署未取代先前成功的 `0d98f80`；實際 Compute 仍為原 `$7/月、0.5 CPU／512 MB`，未另購方案。重複排隊的手動部署已取消。

修正 Blueprint：移除 `plan`；將執行環境、網域、金流／發票旗標及出單開關設成 `sync: false`，保留現有設定。新增回歸防止重新覆寫。原正式設定依既有 [便當發布紀錄](MEALS_PRODUCTION_RELEASE_2026-09-12.md) 核對；自動審核要求明確授權恢復金流旗標，等待使用者回覆後儲存並重新部署。未更換密鑰、未手動呼叫付款／退款或開票。

正式成功版本與 LINE 實測結果完成後續補。隔離測試不等同真實 LINE 帳號與 Email 驗證。教師操作見 [LINE 測試說明](LINE_TEST_GUIDE.md)。
