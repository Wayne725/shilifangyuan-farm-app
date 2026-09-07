# 隔離瀏覽器驗收

測試檔：`tests/browser/payment-return.mjs`、`tests/browser/auth-navigation.mjs`、`tests/browser/membership.mjs`。只接受 localhost／127.0.0.1，攔截所有 `/v1/` 回傳假資料，阻擋其他外部網站；付款頁使用 `payment.example.test`，證件儲存使用攔截的 `storage.example.test`。不使用正式帳號、不執行真實付款、退款、開票或寄信。

## 執行

先在一個終端啟動專用前端：

```sh
pnpm --dir web dev --host 127.0.0.1 --port 4185 --strictPort
```

在另一個終端使用獨立暫存 runtime，避免將手機 App 或瀏覽器二進位加入專案：

```sh
test_runtime="$(mktemp -d)"
npm install --prefix "$test_runtime" playwright@1.62.1
PLAYWRIGHT_BROWSERS_PATH="$test_runtime/browsers" node "$test_runtime/node_modules/playwright/cli.js" install chromium webkit
NODE_PATH="$test_runtime/node_modules" PLAYWRIGHT_BROWSERS_PATH="$test_runtime/browsers" pnpm test:browser
NODE_PATH="$test_runtime/node_modules" PLAYWRIGHT_BROWSERS_PATH="$test_runtime/browsers" BROWSER_ENGINE=webkit pnpm test:browser
```

Linux 可能還需安裝 Playwright 所需系統函式庫；本輪是在 macOS 執行。已有相容 runtime 時可直接設定 `NODE_PATH`；`BROWSER_EXECUTABLE` 可指定已安裝的 Chromium 執行檔。`BROWSER_TEST_URL` 可改為其他**本機**測試埠，不能指向正式站。

## 覆蓋

- 社員認領／非社員註冊 → 輸入驗證碼 → 登入 → 首頁；登入後上一頁不再次要求登入。
- 已登入重新進入註冊／驗證頁會替換為首頁；重整可恢復登入。
- 驗證碼失效及密碼錯誤不會誤判成功；Email 驗證完成本身不等同登入。
- 商店／結帳／訂單頁中途登入保留原頁及訂單網址參數，不一律送回首頁。
- 既有訂單續付，以及結帳新建訂單後前往付款：上一頁、下一頁、重整接續原訂單。
- 新結帳只建立一張訂單、一次付款嘗試，返回後不再建立。
- `/cart` 相容到 `/checkout`，不顯示不存在頁面；不猜測沒有訂單編號的舊歷史應對應哪張訂單。
- 管理工作台分頁、搜尋重設頁碼，重送／金流重查必填原因；無確認視窗。
- 390px 寬度無水平溢位。
- 入社選檔時先拒絕過大與不支援格式，保留後端驗證；不將大檔讀入記憶體再向後端申請上傳。
- 三份假證件上傳、補件重送與撤回的畫面狀態；上傳失敗不可顯示確認成功，允許重選同一檔案。

入社補件狀態在瀏覽器測試由假管理 API 回應提供；管理員權限、跨人存取與刪除後禁止產生下載網址由後端公開 API 測試驗證。這不是瀏覽器直接串真實 R2 的端到端驗收，假 PDF 文字只供畫面測試，不能拿來替代正式檔頭與惡意檔掃描。

登入 refresh 是 API 假資料，只證明前端可還原及不自行跳出登入畫面，**不等同正式 Cookie、Safari ITP、跨分頁憑證輪替或 iPhone 實機驗收**。供應商封包與背景工作由後端整合測試另行覆蓋。雲端完整交易及收到汎宇信件需另外授權驗收。
