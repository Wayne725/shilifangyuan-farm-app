# 手動開票與紙本證明聯交接

核對日期：2026-11-09

網站可完成的本輪程式已實作，完整 CI 六項工作全數通過，PR #44 已合併。API 與 Web 均已發布同一版本；正式操作頁的唯讀及未送出表單驗收已完成。正式紙本證明聯仍停用，本輪未建立正式交易、開真發票或送印；供應商實開驗收與全財務分類仍未完成。

## 目前可用範圍

本次修改前，正式 API 為 `4d9c75e`、Web 為 `1cf160ff`；由主代理重新載入 Render 後核對。Git 整合基線是 `b868283a`，不可把 merge SHA 當成已部署版本。

本次發布版本為 `ab9b90ffca72c55f06c16218915501a75354d0a9`（PR #44），其 Git tree 與完整 CI 測試版本 `75939a8d` 相同。2026-11-09 核對 Render：API 部署 `dep-dbomk9rbc2fs739ndpt0` 與 Web 部署 `dep-dboml3psrm7s73cfap5g` 均為 Live，版本一致。主代理已完成下列正式頁唯讀及未送出表單驗收。

已有獨立「手動開票」入口 `/admin?section=manual-invoice`：管理員輸入品名、含稅單價、數量、稅別及客人發票資料，確認現場實收後建立交易，再交背景工作開電子發票。送出回應不明時保留同一 UUID 與完整 payload；重新整理先查原交易，避免重複收款／開票。已有唯讀手動開票紀錄。

這不是從空白金額直接產生一張沒有交易依據的發票：後端保留現場銷售、實收與操作紀錄。現場交易不歸屬管理員個人，不增加其點數，不預留網站庫存，也不觸發便當自動出單。

程式入口：

- `web/src/components/AdminWalkInSale.tsx`：表單與 UUID 回復／重試。
- `backend/app/routers/walk_in_sales.py`：管理員收款、交易、稅額、冪等與安排開票。
- `backend/app/routers/invoices.py`：已存在且已付款訂單的人工安排開票，檢查既有發票及外部開票確認。
- `backend/app/invoice_timing.py`：明確選取 `pickup_day` 時，以台灣取餐日零時作為最早開票時間；不是所有便當都自動採此方式。
- `backend/app/integrations/fanyu_invoice.py`：目前一律 `printMark=N`，沒有紙本交付流程。

## 本輪完成與未完成

| 項目 | 狀態 | 邊界 |
| --- | --- | --- |
| 便當／一般商品兩個分類按鈕 | 完整 CI 通過、已發布，正式表單已核對 | 過濾選品清單，並明確送出 `sale_category=meal/product` |
| 各分類既有啟用品項 | 完整 CI 通過、已發布，正式表單已核對 | 沒有虛構預設餐盒；無品項／載入失敗不可送出 |
| 切分類清除品名與單價 | 完整 CI 通過、已發布，正式表單已核對 | 避免把前一品項的金額用於另一分類 |
| 電子交付、UUID、回應不明重試 | 沿用原流程且回歸通過 | 不帶入店員已儲存的 Email 或手機載具 |
| 現場銷售來源分類保存／紀錄查詢 | 完整 CI 通過、已發布，正式紀錄讀取已核對 | `WalkInSale.sale_category` 保存分類；歷史資料維持 null，顯示「未分類」；未新增正式交易驗證寫入 |
| 全部財務／匯總報表依現場來源分類分流 | **未完成** | 核心 `order_kind=regular`、`sales_channel=regular` 維持原約定，本輪不改全站報表 |
| 手機條碼可不填 Email | 完整 CI 通過、已發布，正式表單已核對 | 限現場交易、汎宇、有效手機條碼，適用個人／公司；Email 會員載具仍需 Email；尚未實開真票驗收 |
| 手機條碼／紙本二選一 | **未完成** | 紙本無可靠傳輸與完整驗收，不顯示可操作的紙本選項 |
| 正式紙本電子發票證明聯 | **未完成、不可啟用** | 條碼／PDF 取得、正式列印狀態及合規 WiFi 實印仍有缺口 |

`/v1/products` 會依管理員身分回傳完整商品主檔，與 `AdminCatalogPage` 同端點；不受日常選購是否上架限制。本頁再篩選 `is_active`。便當來自 `/v1/admin/meals`。品名目錄背景更新時保留目前選取的原名稱；已送出或結果不明的交易保持鎖定，重試送出原 payload。

新增 `0051_walk_in_sale_category`，接續 `0050_meal_workday_policy`。欄位可空白，既有紀錄保持「未分類」，不能以品名猜測歷史分類；資料庫限制新值只能為 `meal/product`。舊客戶端不傳分類時仍可建立／查回未分類交易，計算 request fingerprint 會省略新欄位，避免舊版本同 UUID 重試發生衝突；明確送入的分類則納入 fingerprint，不可用同 UUID 改分類。已存在分類時 migration 阻止降版丟失資料。

後續財務／匯總分類應明確讀取現場來源分類，再與報表連動。不能僅把 `order_kind` 改成 `meal`，以免誤啟用便當預訂、取餐、退款或自動列印的專屬邏輯。

## 本輪驗證

- 完整 CI [42570685707](https://github.com/Wayne725/shilifangyuan-farm-app/actions/runs/42570685707) 於 `75939a8d` 六項工作全數通過：後端 3,186 項及 31 個 subtests、PostgreSQL 183 項、Chromium 309 項、WebKit 250 項。PR #44 合併版本 `ab9b90ffca72c55f06c16218915501a75354d0a9` 的 Git tree 與此測試版本相同。
- `web` TypeScript `tsc --noEmit` 通過。
- `tests/browser/admin-walk-in-sale.mjs`：16 項通過，包含原 10 項回歸與新增 6 項無 Email／個人公司／重試測試。
- `tests/browser/manual-invoice-categories.mjs`：5 項分類測試通過。
- `tests/browser/admin-manual-invoice-records.mjs`：5 項通過，加入來源分類顯示；三份 browser 共 26 項通過。
- 後端定向 143 項通過，涵蓋新舊分類 payload、舊 fingerprint 重試、provider 限制、共用結帳契約不變、汎宇假 transport worker 僅開立一次、退款、統編前後空白正規化、0051 升降版與財務資料保留、原綠界 integration；後續完整 CI 亦已通過。
- `git diff --check` 通過；已檢視 390px 公司＋手機載具無 Email 表單截圖，無橫向溢出。
- 涵蓋 390px／1280px、分類隔離、停用品項排除、未上架但啟用商品、空目錄、載入失敗及唯讀重試、切換清除、交易鎖定與同 UUID 完整 payload 重試；無效條碼、統編、Email 於有值且失焦後顯示具 ARIA 關聯的提示，初開空白不報錯。
- 本輪手動開票瀏覽器測試使用 Vite 與攔截 API fixture，後端供應商驗證使用假 transport，沒有建立正式交易、開真發票或送印；不能以隔離測試當正式開票或紙本驗收。

### 正式頁唯讀與未送出表單驗收

主代理於 2026-11-09 核對同一已發布版本，未按下收款／開票確認：

- 便當／一般商品的品項清單正確；切換分類會清空品名、單價。
- 選擇三明治並手動輸入單價 139、數量 2，合計正確顯示 278 元；有效手機條碼且 Email 留空時，確認按鈕可用，但未送出。
- 切換至一般商品後合計歸零、確認按鈕停用；重新載入表單資料清空。
- 開啟紀錄成功顯示「尚無手動開票紀錄」，沒有錯誤，確認包含 `0051` 分類欄位的正式讀取路徑可用；未建立正式交易測試分類寫入。

以上只驗證正式頁與讀取 API。供應商實開、正式紙本及送印均未驗收。

## 手機載具免 Email 的限定契約

汎宇 API 文件將 `notifyEmail` 列為選填；`carrierType=3J0002` 使用手機條碼，不需要將 Email 當會員載具識別碼。本輪改動限定於：

1. `AdminWalkInSale` 使用獨立 `WalkInInvoiceFields`，預設手機條碼；Email 留空可送出，若填入仍驗格式。切換 Email 會員載具則須 Email，最多 64 字元；手機載具通知 Email 最多 80 字元。原 `InvoicePreferenceFields`／一般結帳驗證不放寬。
2. `WalkInInvoicePreferenceInput` 專屬 schema 仍驗統編、公司名稱與條碼；`WalkInSaleCreate` 合併通知 Email，再由 route 限制只有 `invoice_provider=fanyu` 能在電子開票交易省略 Email。Email 會員載具不可省略。其他 provider 在建立交易前拒絕無 Email，未建立收款／訂單。
3. `InvoiceIssueRequest.validate()` 預設仍要求 Email 或電話。僅汎宇 adapter 明確使用 `allow_mobile_delivery_without_contact=True`，且必須為格式有效的手機載具；開票資料仍為 `3J0002`、`printMark=N`，空白通知 Email 不作替代填寫。

個人及公司＋手機載具均可不填 Email；公司仍需有效統編與抬頭。此頁不讀取管理員的已儲存載具或 Email，「下一筆」清空客人資料。格式有效不代表已向財政部驗證載具歸屬；provider 實際開票回覆仍由原背景工作及結果不明／重試流程處理。

程式、定向回歸及完整 CI 已通過，API 與 Web 均已發布；正式頁已核對有效手機條碼搭配空白 Email 可通過表單驗證，但未送出。本輪沒有建立正式無 Email 發票，因此不可宣稱已完成供應商實開驗收。通知 Email 空白時不代填管理員 Email、假 Email 或手機號碼。

## 汎宇正式列印資料的既有準備

另一工作分支 `codex/invoice-proof-contract-20261107` 的未發布提交 `88bb1d5c57b33b8d9640b31e04cd3ded40a16d0a`，只有以下 adapter seam 與 mock 測試：

- `get_invoice_print_codes` 對應 `/getInvoicePrintCode`：取得真正發票的 Code39 內容與左右 QR 內容，檢查回傳發票號碼一致及必要欄位。
- `get_invoice_b2c_pdf` 對應 `/getInvoiceB2CPDF`：取得 base64 PDF，限制大小與檢查 PDF 邊界。
- 沒有新增發票號碼、沒有自製假 QR、沒有改變已列印狀態，也沒有送印。

汎宇《電子發票 API 說明文件》第 8 頁另支援 `rtnType=JPG` 取得 JPG；第 17 頁是三種條碼內容 API。現有準備提交未做 JPG。取得 PDF／JPG／條碼內容，不代表飛鵝已能列印。

仍缺紙本交付意圖、權限保護的取得入口、不可變列印內容、正式證明聯專用列印工作與狀態、重試／結果不明／補印規則，以及實際傳輸驗收。`printMark` 在開票前後與實印成功之間如何更新，要依汎宇正式合約確認；目前文件看到開票欄位，尚未確認單獨「標記已印」API。不要直接把現有 `N` 改成 `Y` 就宣稱可印。

原始供應商文件：`/Users/liuweiwei/Downloads/汎宇電子發票API說明文件.pdf`。第 6 頁載明 `printMark`、載具與 `notifyEmail`；第 8 頁為 PDF／JPG；第 17 頁為列印碼。

## 已知飛鵝實測與目前阻塞

設備為 FP-V58-W，走飛鵝中國站 WiFi 雲端；一般便當小票繁中已驗收。正式發票與便當小票屬不同功能，既有小票成功不能當作正式證明聯驗收。

2026-11-07 另一工作區保留的 A–H 測試紀錄顯示：

- 單個官方 QR 可印；兩個 QR 仍垂直排列。
- `BC39` 標籤被當成文字；原生 Code39 有可見印出結果，但尚未取得可靠掃描驗收。
- 原生二進位內容到第一個 NUL 即截斷，未釐清截斷層；不能假設可傳標準 raster 指令。
- 標籤機的座標／圖像 API 不能當成這台小票機可用功能；page mode 也未產生並排 QR。
- 離線生成可掃描的 384px 圖，只證明檔案本身，不代表能透過這台的 WiFi 雲端送達。
- 本輪沒有重跑 A–H，也沒有新增任何實印工作。

FP-V58-W 官方規格為 57.5±0.5mm 紙寬、48mm／384 dots 可印寬、203dpi；兩個最小 QR 的物理寬度並非直接證明不可能。尚未找到、也未實機驗證這個型號可逐筆接受完整 PNG／JPG／PDF 或不截斷二進位的 WiFi 列印通道。

官方 receipt `Open_printMsg` 與 label `Open_printLabelMsg` 的能力不可混用。官方 LOGO 流程是每台固定一張、上傳後人工審核，不適合把每張發票的動態 QR 圖當 LOGO。WiFi 配網也不能推論開放 TCP 9100 或通用 ESC/POS 協定。

最短需要補的是供應商提供 **FP-V58-W 此韌體適用的 WiFi 完整圖片或二進位列印文件／SDK**，明確包含端點、內容格式、大小限制、逐筆圖片方式、二進位 NUL 處理、回傳狀態與重試規則；若為區網 SDK，須有此型號支持證據及現場代理可用方式。使用者若取得這份文件、原廠回覆或可重現示範即可提供，不需要再提供帳密／UKEY、不需要先買其他設備。使用者暫停要求廠商協助時，不代寄信。

沒有這條可靠通道前，紙本選項保持未啟用；可以先完成分類、電子載具與正式發票資料取得 seam，但不能把正式紙本標示完成。

## 正式證明聯規格與驗收界線

依核對日可取得的財政部《電子發票實施作業要點》（115/04/22 修正）及附件：

- 58mm 小票常用格式一為寬 5.7cm±3%、證明聯主體高 9cm±3%；文字內容、字高、Code39 與二維碼須依規定。
- Code39 與發票期別／號碼／隨機碼資料必須正確。兩個 QR 同尺寸、頂部對齊、左右並排；條碼規格要求 QR 編碼區至少 1.5cm，四周留白至少 0.2cm（規格容差另見原文）。
- 買方有統編的證明聯還有銷售／稅額與明細規則；不可只印個人發票樣式而漏掉公司所需內容。
- 熱感紙品質、背面領獎資訊／收據欄位亦有規定；58mm 白紙能印字並不等於符合正式證明聯用紙。
- 正式證明聯原則列印一次；特定補印須標示「補印」。不得沿用廚房訂單「全部訂單可無限制補印」的功能。

硬體傳輸可行後，至少需核對真實 provider 內容、量測尺寸字高、三種條碼實掃、雙 QR 排列與留白、背面與用紙、紙本／載具互斥、首次列印及不明結果／補印情境；正式發票測試必須另有明確授權與合適測試程序。

Primary sources：

- [財政部電子發票實施作業要點（現行）](https://law-out.mof.gov.tw/LawContent.aspx?id=FL041411)
- [現行附件：證明聯格式與熱感紙規格](https://law-out.mof.gov.tw/Download.ashx?FileID=53599&id=FL041411&type=LAW)
- [財政部電子發票證明聯一維及二維條碼規格 1.9](https://www.einvoice.nat.gov.tw/static/ptl/ein_upload/download/20.pdf)
- [飛鵝 FP-V58-W 官方規格](https://www.feieyun.com/product_detail-V58W.html)
- [飛鵝目前 API 文件入口](https://help.feieyun.com/#/home/doc/zh;nav=1-1)
- [飛鵝官方 API 文件：小票與標籤機接口分別定義](https://feieyun.com/open/apidoc-en.html)
- [飛鵝 LOGO 設定文件](https://help1.feieyun.com/docs/H78QVu)
- [飛鵝 WiFi 配網文件](https://help1.feieyun.com/docs/yMkUs0ee)

## 後續接手順序

1. 本輪分類 UI／保存、手機載具免 Email、0051 migration 已整合，完整 CI 通過且 API、Web 均已發布；正式頁唯讀及未送出表單驗收完成，後續保留這些回歸檢查。
2. 若需要實開驗收，取得另行授權並沿既有正式交易驗收程序，不能把 mock 成功當作 provider 實開成功。
3. 後續評估財務與匯總報表讀取現場來源分類；歷史未分類不推測成便當或一般商品。
4. Review 未發布 `88bb1d5c` 並接入正式資料取得 seam；仍不啟用列印。
5. 取得並驗證同型號 WiFi 圖像／二進位傳輸證據，再完成紙本資料模型、列印工作與法定版式驗收。缺少傳輸契約時停在此阻塞，不反覆消耗紙張做已失敗的 A–H 測試。
