# 付款返回與管理操作驗收

日期：2026-09-06（Asia/Taipei）
補充：2026-09-07 重新逐頁核對汎宇兩份 PDF，修正下方 `notifyEmail` 的先前誤判；本文件其餘測試數保留當時紀錄，最新發布與驗收見 [09-07 發布紀錄](TRANSACTION_ADMISSION_RELEASE_2026-09-07.md)。
範圍：`codex/v2-social-commerce` 工作區，基準 HEAD `2cff5d7eaeefc9231f302641c511a2ee75c54edd`。

## 結論

本輪程式修改及 SQLite／PostgreSQL／模擬供應商／隔離瀏覽器驗證完成，**尚未提交、推送、部署或正式營運放行**。沒有真實扣款、退款、開票、寄信或修改正式資料庫。保留原先尚未提交的文件及其他修改。

使用者已決定：**折讓暫緩**，不新增自動折讓或人工登錄折讓；保留既有資料表、歷史紀錄及待辦，不刪 migration。

## 已處理

1. 付款跳轉前，以正式路由先載入 `/orders?order_id=…`，再離開網站；入社付款則回 `/account?membership_charge_id=…`。一般、團購、便當與入社入口共用 handoff。不能用瀏覽器返回認定已付款。
2. 舊 `/cart` 相容到 `/checkout`。沒有訂單 ID 的歷史網址不會猜一張訂單；新結帳返回已建立的原訂單。
3. 管理端新增訂單分頁搜尋，不再只顯示前 12 筆；支援編號、Email、統編。保留既有 `/v1/orders` 回應格式。
4. 管理員可填原因重查最新一次雷門付款紀錄，沿用原 query adapter、金額／商店／交易驗證及冪等套用；不重新建立付款、不直接重送退款。失敗有稽核，不因查詢失敗宣稱付款成功。
5. 失敗 Outbox 分頁顯示錯誤分類、嘗試次數與阻擋原因；不回傳 raw payload、Email body、Token 或 provider secret。只允許當前狀態仍有效的付款／退款通知、開票工作重新排入；同一事件保留 idempotency key 和嘗試歷史。過期驗證信、平台發票 Email、已完成或已退款的開票工作不放行。
6. 全額退款後可填原因向汎宇作廢／核對。先查原銷貨單，核對賣方快照、票號、日期與狀態；未全額退款、已有折讓、票號不符、查無原票、缺日期及狀態矛盾均停止。送出前保存持久化標記；逾時、程序中止或回傳不明後只查詢，不重送作廢。只有汎宇查回作廢狀態才顯示完成。
7. 修正 HelpPage 的開票時點；平台寄付款通知，汎宇寄官方發票通知，兩者不混用。
8. 部署範本移除 API 的固定免費方案，省略 plan 以保留既有服務的方案；先以失敗測試重現再修改，新增回歸保護。僅改本機檔案，未同步 Render。新服務預設可能收費，詳見部署手冊；其他環境變數仍須部署前逐項核對。

## 通知信觀察（09-06 補充）

使用者回報不同的夜間測試票及自行 NT$10 付款後產生的測試票，都在隔天約 08:00 收到汎宇信。本輪未讀取其 Gmail 或重新寄信；此為使用者驗收回報，不是供應商確認的固定排程、休眠規則或正式環境服務時效。

保留「付款成功 → 開票 → 汎宇通知送達」各自的判定；有票號但信延遲不可重新開票。汎宇仍是測試環境，真實金流成功不代表已切換正式發票。正式憑證、寄送時效與未送達處理另待供應商確認，詳見 [發票流程](INVOICE_FLOW.md)。

## 供應商契約

本輪核對《汎宇電子發票API說明文件》第 7 頁 `/cancelInvoice` 及第 14 項銷貨單查詢，並核對範例 V4.1 的折讓資訊：

- 作廢仍使用 `/cancelInvoice`，不誤用註銷 `/voidInvoice`。
- 作廢 reqData 保留 `orderID/process_type/sellerID/buyerID/invNo/invDate/cancelReason/remark/notifyEmail`。原因最多 20 字；B2C buyerID 為八個 0，B2B 為買方統編；日期 YYYYMMDD。
- **09-07 更正**：說明書第 7 頁的欄位表未列 `notifyEmail`，但《汎宇電子發票API範例V4.1》第 3 頁作廢範例有列；「文件沒有此欄位」不正確。未部署的刪除已撤回，維持原 adapter 可傳入通知信箱、預設空字串的相容行為。管理作廢目前不指定通知信箱，不宣稱一定寄作廢信。開立發票、會員載具、手機條碼、B2B 與 HMAC／加密／Header 均不變。
- 只有 API 接受作廢、但尚未查回狀態時回 202，不能當成完成。若標記已保存但程序在真正送出前停止，也只查詢，需管理員至汎宇核對處理，不自動冒險重送。
- 折讓需區分未稅額與含稅額，文件未提供依未知銷退單號查回折讓結果的補償 API；依使用者決定暫緩。

## 結果

| 檢查 | 本輪結果 |
| --- | --- |
| Backend tests + audits | 651 通過，0 失敗（隔離 SQLite） |
| 前端與 API 契約 | 32 通過，0 失敗；含新增保留 Render 既有方案檢查 |
| Chromium / WebKit | 前次各 4 個瀏覽器案例通過，總計 8；390px；本次未改前端執行邏輯，未重跑瀏覽器 |
| TypeScript / Vite build | 通過 |
| API route manifest | 180 項；含新 5 個管理端 API |
| Python requirements / 前端 production dependencies 漏洞檢查 | 未發現已知漏洞；不等同所有程式已無資安問題 |
| PostgreSQL 18.6 併發 | 2 案例通過，首次修正後通過並追加 5 輪（共 6 輪、12 次案例執行）；每輪 5 個並行工作／操作 |
| PostgreSQL migration | 空白測試庫升至 0013 → 降回 base → 再升至 0013，全部通過 |
| 新 schema / migration | 無；舊 migration、模型與 lockfile 未更動，部署設定只移除 API 固定免費方案 |

TDD 先重現缺少管理 API、返回遺失訂單 ID、查無發票仍送作廢、查回缺日期造成錯誤，再以公開 API／真實前端測試修正。外部 HTTP 和 Email 才使用假供應商，保留實際 adapter 轉換與資料庫邏輯。WebKit 模擬 HTML 曾因缺 charset 顯示亂碼，已修測試頁 meta charset，不修改正式供應商頁面。

PostgreSQL 初次執行為 1 通過、1 失敗：測試的 `CONCURRENT-VOID` 含連字號，被現有 adapter 的銷貨單號驗證拒絕，尚未送出模擬 HTTP。測試資料改為 `CONCURRENTVOID` 後全數通過，**沒有放寬供應商格式或修改正式交易邏輯**。臨時 PostgreSQL 從 [Homebrew 官方套件資訊](https://formulae.brew.sh/api/formula/postgresql@18.json) 取得並核對 SHA-256，只在本機 loopback 使用；沒有全域安裝或註冊常駐服務。測試僅接受 localhost 的 `shilifangyuan_test`，每輪使用新 schema。

併發斷言：同一批背景工作只查一次待處理付款、只開一次發票、只寄一封平台付款信；五個管理員同時作廢只呼叫一次 cancelInvoice，五次重排同一失敗工作只有一次成功、其餘四次 409。這些是指定競爭情境的隔離驗證，不是 500 人壓測或外部服務必然 exactly-once 的保證。

測試結束已停止本機 55439 埠的 PostgreSQL，移除本輪下載的暫存 runtime、空測試資料庫與短路徑連結（約 219 MB）。這些均為可重建測試產物，未刪使用者資料；前次移至垃圾桶的舊 App 仍可復原。

後台目前只列終態 FAILED 工作，不是完整監控告警、供應商 Email 送達查詢或所有 payment poll failure dashboard；一般使用者的全部訂單歷史仍未改成後端分頁。

## 舊 App 清理

使用者授權後，將根 `dist/`、`.expo/`、空 `app/`、空 `src/` 移到 macOS 垃圾桶 `farm-old-expo-Yr14Bv`，合計約 51 MB，可復原。移到垃圾桶不代表已釋放磁碟空間。這些均非 Git 追蹤檔；原 Expo 程式先前已刪。

保留 `web/`、`web/dist`、共用 `assets/`、響應式手機網頁、後端、所有交易資料模型與 migration。測試產生的 0-byte 根目錄 SQLite 空檔已移除；不涉及任何資料紀錄。

## 部署前關卡

1. PostgreSQL 併發與 migration 關卡已於本機完成，不再以缺 server 阻擋；不可拿正式資料庫重跑。
2. 經同意再提交／部署，核對前後端同版、180 項 API 契約、Blueprint 差異、付費方案、資料庫綁定與背景工作設定；本輪未重新查閱雲端設定，不直接套用範本預設值，不能宣稱線上已修好。
3. 另行授權後走同一筆低額交易：付款 → 關頁等待背景處理 → 訂單票號 → 平台付款信＋汎宇官方信 → 全額退款 → 作廢查回。Sandbox 與正式憑證要明確區分。
4. Safari／iPhone 實機、跨分頁登入與正式 Cookie 行為；隔離 WebKit 的 mock refresh 不取代這項驗收。
5. 折讓、宅配、階梯壓測、完整告警及備份另列待辦，不算本輪完成。

## 重跑

```sh
DATABASE_URL=sqlite+aiosqlite:///:memory: PYTHONPATH=backend .venv312/bin/python -m pytest -c backend/pyproject.toml backend/tests backend/audits -q
pnpm test
pnpm typecheck
pnpm build
```

瀏覽器執行方式見 [隔離瀏覽器驗收](BROWSER_TESTS.md)。PostgreSQL 測試僅接受 localhost 的 `shilifangyuan_test`，每輪使用新的隔離 schema。
