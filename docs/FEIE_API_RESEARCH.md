# 飛鵝亞太站 API 查核

查核日期：2026-10-22。只讀官方文件與官方 SDK；沒有登入帳戶、使用真實憑證或呼叫列印 API，亦未驗證實機。

## 確認的介面

| 項目 | 亞太站契約 |
| --- | --- |
| 請求目的路徑 | `/Api/Open/`；方法由表單 `apiname` 選擇，並非附加 `/printMsg` |
| 公共欄位 | `user`、UNIX 秒數字串 `stime`、`sig`、`apiname` |
| 簽章 | `sha1((user + UKEY + stime).encode("utf-8")).hexdigest()`，小寫 40 碼 |
| 傳輸 | HTTPS POST；`application/x-www-form-urlencoded`，不是 JSON；不啟用 debug |
| 送印 | `Open_printMsg`，欄位 `sn`、`content`、`times=1`；內容不得超過 5,000 bytes |
| 接受送印 | 僅 `ret=0` 且 `data` 是非空字串時保存其供應商訂單 ID；這不是已出紙 |
| 查結果 | `Open_queryOrderState`，`orderid` 使用供應商 ID；`data=true` 表示平台確認已印，`false` 僅代表尚未印 |
| 查設備 | `Open_queryPrinterStatus`、`sn`；回傳離線、在線正常、在線異常等文字；離線判定可能落後約兩分鐘 |

來源：[亞太站官方規格](https://feieyun.com/open/AsiaPacific-cn.html)，公共參數、列印訂單、訂單狀態、設備狀態章節。將 SHA1 輸入明確編為 UTF-8 是程式選擇，亦符合下述官方 Python 範例的實作；API 文件未另訂簽章輸入 charset。

## 區域網址有文件矛盾

文件的 HTTP 位址是 `http://api.jp.feieyun.com/Api/Open/`。同頁 HTTPS 連結的**可見文字**是 `https://api.jp.feieyun.com:443/Api/Open/`，但 2026-10-22 原始 HTML 的 **href 實際指向 `https://api.de.feieyun.com:443/Api/Open/`**。不可直接點連結、跟隨其德國區域主機或把其他站當作亞太站。

建議客戶端只接受明確設定的 `https://api.jp.feieyun.com/Api/Open/`，關閉重新導向並驗證主機。這是依亞太頁面顯示名稱及既有 HTTP 主機所作的保守實作決定，**本次沒有向 API 送請求證實路由可用**；啟用真實列印前應向飛鵝確認亞太 HTTPS 主機及設備相容性。來源：[官方亞太頁](https://feieyun.com/open/AsiaPacific-cn.html)。

## 編碼、58mm 與繁體中文

官方 Python ZIP 的 `api_python_demo.py` 以 UTF-8 原始碼、Python 預設 UTF-8 `.encode()` 與 `requests.post(data=params)` 送表單，但它預設網址是中國站 `api.feieyun.cn`，不是亞太站 SDK；可佐證 UTF-8 表單實作方式，不能拿來證明亞太契約完全相同。[官方 Python SDK](https://www.feieyun.com/api/API-Python.zip)

亞太小票 API 章節沒有明訂 GBK／GB18030 或切換繁體字庫的參數。文件的 Big5 字型說明屬於**標籤機**，不能套用到本次 58mm 小票機。建議用 UTF-8 表單傳送、以 UTF-8 bytes 檢查 5,000 上限，中文按顯示寬度換行；58mm 的實際欄寬、罕見字、字型及繁中韌體必須以購買型號實測。`<BR>` 可換行；使用者文字應消除／替換控制標籤，手動撕紙機不送切刀指令。[亞太小票與標籤章節](https://feieyun.com/open/AsiaPacific-cn.html)

## 防重複與錯誤界線

- 此頁沒有提供客戶端自訂工作 ID、冪等鍵或按自訂 ID 找回供應商訂單的契約。資料庫唯一工作 ID 只能防止本系統重複排程，不能替供應商去重。送印後失去回應而沒取得供應商 ID 時，不能靠日期統計數判定某張已印。[官方 API 清單及送印參數](https://feieyun.com/open/AsiaPacific-cn.html)
- 明確的送印拒絕包括空參數 `-2`、內容過長 `1001`、帳號與設備不匹配 `-1`；公共參數另有簽章／時鐘 `-3`、綁定問題 `1002`。相同碼在不同介面意義不同，不能把所有 `-1` 視為暫時忙碌。已知拒絕設 `failed`，先修設定或內容；未列出的錯誤碼保留安全錯誤代號，不自行猜測可重試。[官方錯誤碼](https://feieyun.com/open/AsiaPacific-cn.html)
- **實作建議／推論**：本機校驗失敗、尚未建立連線即失敗可確定未送出，後者可有限退避重試。送出後 read/write timeout、連線中斷、HTTP 5xx、非 JSON、`ret=0` 卻缺有效 ID 等一律 `unknown`，不自動再送。已取得 ID 者只重試查詢；`false` 保持 `submitted`，不能改成 `failed` 後補印。沒有 ID 者由管理員查證後決定補印，另建有稽核與關聯原工作的補印工作。

## 回呼規格與第一版選擇

文件確實有回呼簽章：移除空值及 `sign`，按欄位名 ASCII 排序並以 `key=value&...` 串接，Base64 解碼簽章後，以供應商公鑰進行 RSA／SHA-256 驗證；資料含 `orderId`、`status=1`、`stime`。有公鑰[官方下載](https://help.feieyun.com/templates/file/public.key)，不是單靠公開訂單 ID 信任回呼。[亞太回呼章節](https://feieyun.com/open/AsiaPacific-cn.html)

不過該頁要求送印時提交回呼網址，卻未在 `Open_printMsg` 參數表列出其欄位名稱。第一版採經簽章認證的結果輪詢，**不新增未驗簽的 webhook、不猜 callback 參數**。未來若接回呼，須先確認亞太回呼配置與公鑰取得／輪替方式，並以持久化防重送、已知供應商 ID 比對及單向狀態轉換處理。

## 可啟用的最小條件

取得賣家完整型號、確認能綁亞太開發者帳號與小票 API、確認繁體韌體及紙寬；先更換曝光 UKEY。綁定使用機身 SN／設備 KEY，API 簽章使用開發者 UKEY，兩者不同。`PRINTER_ENABLED=false` 或 `PRINTER_DRY_RUN=true` 時不得向供應商傳送工作；Dry Run 不能標記成實際 `printed`。[官方接入步驟](https://feieyun.com/open/AsiaPacific-cn.html)
