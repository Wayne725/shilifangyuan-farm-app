# FP-V58-W 到貨配網與雲端出單查核

日期：2026-10-31

使用者已確認型號為 FP-V58-W；2026-10-31 回覆 Wi-Fi 設定完成，後續自行輸入機身資料並儲存。已在亞太平台設備清單核對到一台設備，重新Search後仍顯示 `Offline`、`Status -`、待印0。尚未確認雲端在線或實印成功。本檔記錄官方文件查核與後續本機修正；未讀取設備KEY或開發者UKEY、未代綁定或送印。正式廚房頁唯讀核對顯示雲端出單「未啟用」。

使用者登入後，先確認設備為0台，再開啟 `device Manager → Add device` 表單，預填名稱「十里方圓便當出單機」與服務類型 `Printing`。機身 `Device SN`、`Device Key` 由使用者直接輸入並按Save，現已新增。設備Key不是開發者帳號UKEY，本文不收錄實際識別值。

目前停在離線排查：先核對前方NETWORK藍燈與背面Wi-Fi黃燈。型號手冊分別以藍燈表示伺服器連線、黃燈表示Wi-Fi連線；成功配網或成功新增SN均不能單獨證明已連亞太雲。若持續離線，須由賣家／飛鵝確認設備所屬區域及亞太相容性；不猜測或自行跨區切換。未送測試單，因此沒有本次測試新增的雲端待印工作。

## 已確認的硬體與配網方式

官方產品頁列出 FP-V58-W 為 Wi-Fi 熱感式小票機、無自動切刀，使用約58mm紙卷（57.5 ± 0.5mm），有效列印寬48mm，Font A每行32列、簡繁中文字每行16列，並列API整合。這支持現有保守32顯示格排版及不送切刀指令的方向；實際罕見字與斷行仍須出紙驗證。[官方產品](https://www.feieyun.com/product_detail-V58W.html)

型號專屬教程提供APP及微信配網，iPhone優先用APP；官方明示配網成功後可移除APP。APP是初始配網工具，不應把「要用APP配網」解讀成「網站訂單一定要透過手機常駐代印」。後者是綜合產品API整合能力與教程所作的工程判斷。[型號教程](https://help1.feieyun.com/docs/yMkUs0ee)、[官方產品](https://www.feieyun.com/product_detail-V58W.html)

### 手機APP取得方式

- iPhone／iPad：已查到[台灣App Store的「飞鹅WIFI配置」](https://apps.apple.com/tw/app/%E9%A3%9E%E9%B9%85wifi%E9%85%8D%E7%BD%AE/id1525480285)，頁面列開發者永强 魏、iOS13以上，開發者網站連回飛鵝官網。可確認台灣區頁面存在，不等同已替現場Apple帳號安裝成功。
- Android：官方教程列出多家手機應用商店可搜尋「飞鹅WiFi配置」，亦有[飛鵝官方下載導引頁](https://www.feieyun.com/wificonfig.html)。本次直接讀取該導引頁逾時，未確認當下APK下載連結或台灣Google Play上架，不能保證Play商店一定能找到。[官方APP教程](https://help1.feieyun.com/docs/8rBDwn)

### 現場最短APP步驟

1. 裝紙、接原配電源並開機；先只短按一次「設定」，確認印出的Wi-Fi設定方法小票標題及選項。此時不需傳送SN、KEY或Wi-Fi密碼。
2. 再短按一次「設定」，取得含QR碼的「手機APP配置模式2」小票。
3. 手機先接上店內Wi-Fi，打開官方「飛鵝WiFi配置」APP，選打印機配網／開始配網，掃小票QR碼。
4. 在APP輸入店內Wi-Fi密碼；若系統要求暫時加入「Printer」網路，依APP提示加入。等待機器聯網成功提示，再確認平台在線。

以上按鍵順序來自官方APP圖文教程；若實機小票顯示其他配網模式，先依實機小票及對應型號教程確認，不盲目重設已正常連線的設備。[APP教程](https://help1.feieyun.com/docs/8rBDwn)

### Wi-Fi限制與批次差異

官方故障說明只明確指出「非雙頻」設備須使用2.4GHz、不支援5GHz，並要求核對密碼與手機已連Wi-Fi。產品文字未提供所有FP-V58-W批次的頻段對照，所以不能僅憑型號保證單頻或雙頻；第一次可保守使用2.4GHz，再依設備小票／賣家確認。iPhone讀取Wi-Fi名稱失敗時，官方另列定位權限排查。[Wi-Fi故障說明](https://help1.feieyun.com/docs/38UnCW)

官方「新模組網頁配網」只適用編號924767305–924767490的FP-V58-W。該範圍教程是：短按設定取得方法單，再長按設定逾5秒進入網頁模式，手機加入Printer，瀏覽器開啟10.10.100.254，依小票與官方頁面登入／選店內Wi-Fi；儲存後按提示等待再重啟。未核對編號前，不能泛化這組IP或步驟至所有FP-V58-W。[限定編號官方教程](https://help1.feieyun.com/docs/sNesITQJ)

## 亞太帳號與設備區域仍待現場確認

產品頁的API能力不是亞太韌體／跨區帳號相容保證。官方分流表將中國、亞太與歐洲列為不同API主機，舊亞太規格亦使用亞太帳號體系。因此須確認實際SN可綁本次使用的亞太開發者平台，不能由型號或已接上Wi-Fi推定可以跨區使用。[分流公告](https://help1.feieyun.com/docs/96C8PyrC)、[亞太規格](https://feieyun.com/open/AsiaPacific-cn.html)

綁定使用機身SN／設備KEY；後端簽章使用開發者USER／UKEY，兩組用途不同。本文不收錄任何實際識別值。仍需現場確認：設備所屬區域、是否已被其他帳號綁定、亞太平台能否成功新增並顯示正確設備；再由後端秘密設定及安全的設定檢查確認接線。[亞太接入與新增設備規格](https://feieyun.com/open/AsiaPacific-cn.html)

## 2026-01-15 API分流公告：不能沿用舊入口相容假設

官方公告明列亞太區，說明只改URL，協議、參數與鑑權保持不變；新URL於2026-01-15生效，舊共用URL於2026-07-01停用、2026-08-01完全下線。查核日已超過公告保留期限，不能再用舊規格頁當作永久相容承諾。[官方公告及亞太對照表](https://help1.feieyun.com/docs/96C8PyrC)

| 既有apiname | 公告指定的新亞太URL |
| --- | --- |
| `Open_printMsg` | `https://api.jp.feieyun.com/Api/Open/printMsg` |
| `Open_queryOrderState` | `https://api.jp.feieyun.com/Api/Open/queryOrderState` |
| `Open_queryPrinterStatus` | `https://api.jp.feieyun.com/Api/Open/queryPrinterStatus` |

來源同上。已確認的是官方契約已變；**未確認的是舊入口當下是否仍保留未承諾的相容服務**。本次沒有呼叫API，故不宣稱伺服器此刻必定拒絕舊請求。

本專案初次查核時的`backend/app/integrations/feie.py`仍由三個方法共用`FEIE_API_BASE_URL`，且驗證器只接受`/Api/Open/`，不能僅改環境變數為其中一個新URL。後續已按固定三方法白名單選擇對應路徑，保留亞太 HTTPS 前綴與簽章／表單契約；8 個紅測轉綠，三個出單相關測試檔合計64 passed，`git diff --check`通過。本分支修正尚未部署；測試沒有真實HTTP，沒有開啟正式旗標或實印。

## jp可見文字／de超連結矛盾仍在

2026-10-31唯讀核對官方亞太規格HTML，HTTPS連結文字仍顯示`https://api.jp.feieyun.com:443/Api/Open/`，但實際href仍指向`https://api.de.feieyun.com:443/Api/Open/`。同頁也仍列舊共用入口；不能直接沿用其超連結作亞太端點，更不能改德國站湊合。[官方亞太規格](https://feieyun.com/open/AsiaPacific-cn.html)

此矛盾不推翻新版公告明列的jp分流URL；它表示舊規格頁存在文件不一致。正式啟用應依本次帳號／設備所屬區域確認契約，修妥路徑並驗證，不先將「亞太端點已確認」旗標設為true。

## 本專案可做與待做的驗收

- `GET /v1/admin/printers/configuration`不連外，只回布林檢查、缺少的變數名稱及worker狀態，不回傳USER／UKEY／SN。可先唯讀核對，但不代表已通過供應商驗證。
- `GET /v1/admin/printers/status`在正式模式且通過設定門禁時才會連供應商；本次未執行。
- 目前沒有不建立訂單的獨立測試列印接口；既有工作關聯Order，不應為試紙補印真實舊單。
- 須先確認現場聯網、帳號區域／綁定與新版API適配，再安排一筆明確測試內容，核對繁中、數量、取餐時間及僅出一張。雲端接受是submitted；確認出紙後才完成printed驗收。

以上來自`backend/app/routers/printers.py`、`integrations/feie.py`、`printer_worker.py`及`printing.py`的唯讀檢查。既有到貨流程見[FEIE_PRINTING.md](FEIE_PRINTING.md)，其舊共用URL相關結論須以本次新公告查核修正；本檔不宣稱硬體已驗收。
