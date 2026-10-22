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

發布完成後補記雲端 CI、前後端 Live SHA、正式 LINE 分流及授權流程實測結果。隔離測試不等同真實 LINE 帳號與 Email 驗證。
