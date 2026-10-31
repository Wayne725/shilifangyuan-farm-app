# 隔離瀏覽器驗收

測試檔：`tests/browser/payment-return.mjs`、`tests/browser/auth-navigation.mjs`、`tests/browser/membership.mjs`。只接受 localhost／127.0.0.1，攔截所有 `/v1/` 回傳假資料，阻擋其他外部網站；付款頁使用 `payment.example.test`，證件儲存使用攔截的 `storage.example.test`。不使用正式帳號、不執行真實付款、退款、開票或寄信。

## 鎖定 runtime 與 CI 煙霧

根目錄 exact devDependency 為 `playwright@1.62.1`，由 pnpm lock 鎖定完整依賴及 integrity。使用 `.nvmrc` 的 Node 與 `packageManager` 指定的 pnpm；不需外部 `NODE_PATH`，也不透過未鎖定的 `npx`／`pnpm dlx` 安裝工具。

```sh
pnpm install --frozen-lockfile
pnpm exec playwright install --with-deps chromium
pnpm build
```

鎖定 CLI 會取得對應 Chromium revision1234，而非沿用其他版本的本機 executable。Linux 的系統依賴由 `--with-deps` 安裝；CI 只 cache pnpm，不 cache browser binary。原 `frontend-quality` 仍執行全 root 鎖檔的 audit／SBOM、型別、Node測試與build，另保留 backend 及 api-contract jobs。

新增獨立 `browser-smoke` job，完整串行執行以下三檔共28案，不使用 name filter：

| 檔案 | 案數 | 驗證內容 |
|---|---:|---|
| `checkout-recovery.mjs` | 7 | 回應遺失、凍結原單、價格／指紋更新、storage、身分交接與正常付款 |
| `session-consistency.mjs` | 9 | Email登入重試、401續期、503保登入、A→B隔離、晚到回應與logout |
| `line-customer-checkout.mjs` | 12 | 390／1440寬、一般／便當／共同購買、LINE帳號通知Email新填／預存與付款 |

job上限10分鐘，測試step上限6分鐘，每案45秒；只有本人啟動的loopback preview會在EXIT trap清除。安裝、瀏覽器、build、preview、測試log與session假資料紀錄，在成功或失敗後一律上傳 `browser-smoke-evidence`，保留7日；pipeline使用pipefail，失敗不能被tee掩蓋。job不取得正式帳號或供應商secret。

## 本機執行同一組28案

先在一個終端啟動已build的專用前端：

```sh
node web/node_modules/vite/bin/vite.js preview web --host 127.0.0.1 --port 4192 --strictPort
```

另一個終端執行：

```sh
BROWSER_TEST_URL=http://127.0.0.1:4192 SESSION_EVIDENCE_DIR=browser-evidence/session node --test --test-concurrency=1 --test-timeout=45000 tests/browser/checkout-recovery.mjs tests/browser/session-consistency.mjs tests/browser/line-customer-checkout.mjs
```

`BROWSER_TEST_URL` 只能指向本機；checkout恢復測試自行攔截獨立4239 origin的static/API，不會真的連線該埠。所有API與付款均為假資料，外站請求中止。結束時在第一個終端停止本人preview。

## 其他隔離驗收

`pnpm test:browser` 保留作擴充入口；各檔有自己的fixture、功能旗標與port要求，不代表全部已納入本次CI或皆可切換引擎。`payment-return.mjs`、`auth-navigation.mjs`、`membership.mjs` 等既有範圍列於下節。

需要WebKit的個別檔案，可先用鎖定CLI安裝 `pnpm exec playwright install --with-deps webkit`，再依該檔支援設定 `BROWSER_ENGINE=webkit`。三份CI煙霧目前只承諾Chromium；session及LINE檔固定用Chromium。不要把外部 `BROWSER_EXECUTABLE` 或 `NODE_PATH` 帶入CI，避免以不匹配版本取代鎖檔工具。WebKit測試不等同原生Safari、iPhone或真實金流驗收。

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
