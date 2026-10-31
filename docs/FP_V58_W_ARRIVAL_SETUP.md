# FP-V58-W 到貨配網與雲端出單查核

日期：2026-10-31

最新狀態：已在中國站核對同一台 FP-V58-W 在線、正常、待印 0，送出一張不含個資的測試小票。中國站回覆「成功添加打印任务」，使用者確認「印出1張，繁體中文正常」。網站仍未啟用自動出單，尚待中國區程式發布、使用者自行填入 Render 憑證及網站接口驗收。未讀取設備 KEY 或開發者 UKEY。

以下保留到貨排查歷史：使用者先完成 Wi-Fi 設定及亞太設備新增；該平台初次顯示 Offline、待印 0，後續增為 4 筆，來源未確認。本次沒有清空、跨區重送或解綁。中國站的直接實測已提供可行路徑，不需繼續以硬體改亞太站作前提。區域來源及實測界線見 [區域調查](FEIE_CLOUD_REGION_SETUP_RESEARCH.md)。

使用者登入後，先確認設備為0台，再開啟 `device Manager → Add device` 表單，預填名稱「十里方圓便當出單機」與服務類型 `Printing`。機身 `Device SN`、`Device Key` 由使用者直接輸入並按Save，現已新增。設備Key不是開發者帳號UKEY，本文不收錄實際識別值。

排查時先核對前方 NETWORK 藍燈與背面 Wi-Fi 黃燈。型號手冊分別以藍燈表示伺服器連線、黃燈表示 Wi-Fi 連線；成功配網或新增 SN 均不能單獨證明已連亞太雲。後續透過中國站同 SN 在線及實印解決判定，不再由燈號猜測區域。

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

## 帳號與設備區域

產品頁的API能力不是亞太韌體／跨區帳號相容保證。官方分流表將中國、亞太與歐洲列為不同API主機，舊亞太規格亦使用亞太帳號體系。因此須確認實際SN可綁本次使用的亞太開發者平台，不能由型號或已接上Wi-Fi推定可以跨區使用。[分流公告](https://help1.feieyun.com/docs/96C8PyrC)、[亞太規格](https://feieyun.com/open/AsiaPacific-cn.html)

綁定使用機身 SN／設備 KEY；後端簽章使用開發者 USER／UKEY，兩組用途不同。本文不收錄實際識別值。本機現已在中國站確認同 SN 在線及實印；網站切區仍須配套使用中國帳號憑證與獨立確認旗標。[亞太接入與新增設備規格](https://feieyun.com/open/AsiaPacific-cn.html)、[官方區域分流](https://help1.feieyun.com/docs/96C8PyrC)

## 2026-01-15 API分流公告：不能沿用舊入口相容假設

官方公告明列亞太區，說明只改URL，協議、參數與鑑權保持不變；新URL於2026-01-15生效，舊共用URL於2026-07-01停用、2026-08-01完全下線。查核日已超過公告保留期限，不能再用舊規格頁當作永久相容承諾。[官方公告及亞太對照表](https://help1.feieyun.com/docs/96C8PyrC)

| 既有apiname | 公告指定的新亞太URL |
| --- | --- |
| `Open_printMsg` | `https://api.jp.feieyun.com/Api/Open/printMsg` |
| `Open_queryOrderState` | `https://api.jp.feieyun.com/Api/Open/queryOrderState` |
| `Open_queryPrinterStatus` | `https://api.jp.feieyun.com/Api/Open/queryPrinterStatus` |

來源同上。已確認的是官方契約已變；**未確認的是舊入口當下是否仍保留未承諾的相容服務**。本次沒有呼叫API，故不宣稱伺服器此刻必定拒絕舊請求。

本專案初次查核時三方法仍共用舊 URL，後續已按固定白名單選擇對應路徑，8 個紅測轉綠、相關三檔 64 passed；PR #23 已部署 API commit `6f39b471064255b6ed834d9d0fa103277a4b7b93`。後續雙區支援另有 131 項隔離回歸，維持 TLS、簽章及不跨站重試；發布狀態見需求及發布紀錄。

## jp可見文字／de超連結矛盾仍在

2026-10-31唯讀核對官方亞太規格HTML，HTTPS連結文字仍顯示`https://api.jp.feieyun.com:443/Api/Open/`，但實際href仍指向`https://api.de.feieyun.com:443/Api/Open/`。同頁也仍列舊共用入口；不能直接沿用其超連結作亞太端點，更不能改德國站湊合。[官方亞太規格](https://feieyun.com/open/AsiaPacific-cn.html)

此矛盾不推翻新版公告明列的jp分流URL；它表示舊規格頁存在文件不一致。正式啟用應依本次帳號／設備所屬區域確認契約，修妥路徑並驗證，不先將「亞太端點已確認」旗標設為true。

## 本專案可做與待做的驗收

- `GET /v1/admin/printers/configuration`不連外，只回布林檢查、缺少的變數名稱及worker狀態，不回傳USER／UKEY／SN。可先唯讀核對，但不代表已通過供應商驗證。
- `GET /v1/admin/printers/status`在正式模式且通過設定門禁時才會連供應商；本次未執行。
- 目前沒有不建立訂單的獨立測試列印接口；既有工作關聯Order，不應為試紙補印真實舊單。
- 中國站後台的一張繁中測試已由使用者確認；網站 API 狀態查詢、訂單排版與防重複仍需分別驗收。雲端接受是 submitted，不能單憑接受回應宣稱已出紙。

以上來自程式檢查、供應商後台及使用者紙本回報。完整接線流程見 [FEIE_PRINTING.md](FEIE_PRINTING.md)；供應商後台實印通過與網站完整驗收分開記錄。
