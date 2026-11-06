# 跨分頁登入續期協調

日期：2026-10-31。此修正只調整前端 `api.ts` 的續期請求，不變更後端一次性 refresh token、重放 401、Origin、HttpOnly Cookie 或 AuthProvider 的登入語意。

## 已證實的問題

修正前，同一瀏覽器的兩個 React 分頁可能同時攜帶 R0 Cookie 呼叫真正的 refresh router。A 成功輪替為 R1，B 隨後得到真正的 401，於是 B 的 AuthProvider 清除登入狀態、遮蔽私人訂單；共用 R1 仍有效，第三次 fresh-cookie refresh 及重整 B 均可恢復。購物車沒有遺失，沒有發現私人資料越權或重複付款。

可重現證據保存在 `outputs/remediation-completion/auth-refresh-react-review.md` 與其結果、Cookie 雜湊及原始成品 manifest。這是登入恢復可用性缺陷，不代表正式環境曾發生攻擊。

## 行為與界線

- 保留原本每分頁 singleflight，以同 origin 的 `slf-auth-refresh-v1` Web Lock 協調不同分頁。
- 排他區段只涵蓋真正送出 refresh 至取得 HTTP Response，不涵蓋 JSON 解析、React 更新、私人 API、建單或付款。瀏覽器自行處理 HttpOnly Cookie。
- 排隊最多 15 秒；超時回可重試 503，沿既有「重試連線」流程保留購物車與既有登入資料，不繞鎖重新送出。
- 取得鎖前、Response 回來後、JSON 儲存前均確認 credential 與 identity revision。排隊期間登出、換帳號或更新本頁身分，舊請求不得復活或替新帳號重送原操作。
- 真正的 401 仍清除本頁登入；503／網路錯誤仍保留原有語意。callback 已執行後的錯誤不走 fallback，避免重送 refresh。
- API 缺席，或 callback 尚未執行且遇到原生 `SecurityError`／`NotSupportedError`，只走一次原來的單分頁路徑。普通程式錯誤不能僅靠同名屬性觸發 fallback。

Web Locks 的協調範圍是同 origin／storage bucket。**不支援 Web Locks 的瀏覽器仍可登入，但此 fallback 未解決其跨分頁競態。**本修正沒有把 token 寫入 localStorage／sessionStorage／IndexedDB，也沒有廣播 token；不承諾跨分頁立即同步登出或取消已送到後端的網路請求。回應遺失後 Cookie 是否已輪替，仍是原有網路不確定性。

## 驗證

`tests/audits/web-refresh-coordination.test.mjs` 使用真 `api.ts` 與兩個獨立 VM，僅替換外部 LockManager／fetch 邊界。先取得兩分頁同送 R0 的正式紅，再驗兩種先後順序、有界等待、晚到 callback、慢 JSON、callback 錯誤不重送、401／503、unsupported fallback、revision 及原操作不得借新身分重送、購物車與儲存界線。另有正式紅證實「只 abort signal」不足以限制已授鎖但 callback 尚未執行的等待，因此使用獨立 deadline Promise。

本輪定向命令（Node 22）共 **33 passed**，TypeScript 檢查成功：

```sh
node --test tests/audits/web-refresh-coordination.test.mjs tests/audits/web-session.test.mjs tests/audits/web-session-safety.test.mjs tests/web-api-resilience.test.mjs
```

真正 React／Cookie／原生 Web Locks 的 Chromium 與 Playwright WebKit 驗證及原 session／儲存受限回歸，記錄於 `outputs/remediation-completion/refresh-coordination-validation.md`；以該報告的來源及 immutable dist 雜湊為準。後續 `auth-integrated-validation.md` 再用包含 0047 共用配額的當前後端通過兩頁 200／200、購物車／私人訂單、舊 Cookie 重放 401 及 Chromium 故障恢復控制。兩批 fixture 均為合成帳號及隔離 SQLite，provider／非 auth 業務寫入禁止；不將 VM 測試當作瀏覽器證據。

本批尚無原生 Safari／iPhone 的新 auth 驗收。獨立 Safari WebDriver 能力檢查未建立可確認的專屬 listener，因而沒有送 new-session 或操作一般 Safari 分頁；未改設定或讀取使用者 Cookie。先前原生 Safari 的付款 pagecache 陽性是另一批舊來源證據，不能替代這次修正的相容性驗證。

參考規範：[Web Locks callback／signal 語意](https://w3c.github.io/web-locks/#dom-lockmanager-request)、[Fetch Cookie 處理](https://fetch.spec.whatwg.org/#http-network-or-cache-fetch)。
