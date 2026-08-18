# 真實合作社資料格式對照

來源：`資料格式.docx`（2026-08-16 提供）。本文件記錄真實欄位如何映射到系統，並避免把個資或未定政策直接硬編碼進 UI。

## 實作原則

- FastAPI 與 PostgreSQL 是唯一資料真相；Web 不直接存取資料庫。
- 身分證、地址、電話、LINE、負責人、聯絡人與銀行帳號均視為私密資料，使用版本化 AES-256-GCM 加密。
- 系統內部主鍵使用 UUID；對外編號另設唯一欄位，避免把可變的電話號碼當主鍵。
- 金額以新臺幣整數儲存；日期用 `date`，交易時間用具時區的 `datetime`。
- 真實欄位逐步加入時維持舊 Sandbox 資料可讀，不直接破壞既有訂單與會籍編號。

## 1. 社員資料

| 文件欄位 | 系統位置 | 狀態 |
| --- | --- | --- |
| 社員編號 | `memberships.member_number` | 已有；編碼規則待隱私確認 |
| 姓名 | `member_profiles.legal_name_encrypted` | 已有 |
| 身分證字號 | `member_profiles.identity_number_encrypted` | 新增 |
| 性別 | `member_profiles.gender_encrypted` | 新增 |
| 出生年月日 | `member_profiles.birth_date_encrypted` | 已有 |
| 籍貫 | `member_profiles.place_of_origin_encrypted` | 新增 |
| 職業 | `member_profiles.occupation_encrypted` | 新增 |
| 戶籍地址 | `member_profiles.registered_address_encrypted` | 新增 |
| 通訊地址 | `member_profiles.correspondence_address_encrypted` | 新增；舊 `address_encrypted` 保留相容 |
| 聯絡電話 | `member_profiles.landline_phone_encrypted` | 新增、可選 |
| 手機號碼 | `member_profiles.phone_encrypted` | 已有 |
| 電子郵件 | `users.email` | 已有 |
| LINE 帳號 | `member_profiles.line_id_encrypted` | 新增、可選 |
| 股票號碼 | `memberships.share_certificate_number` | 新增 |
| 股金 | `memberships.share_capital_amount` | 新增 |
| 股數 | `memberships.share_count` | 新增 |
| 認購社股日期 | `memberships.share_subscribed_on` | 新增 |
| 社股繳納日期 | `memberships.share_paid_on` | 新增 |

## 2. 交易資料

| 文件欄位 | 系統位置 | 狀態 |
| --- | --- | --- |
| 訂購者會員號碼 | `users.customer_number` | 已有；不以手機作主鍵 |
| 社員編號 | `memberships.member_number`、訂單資格快照 | 已有 |
| 訂購項目編號／名稱 | `order_items.source_product_id`、`product_name` | 已有 |
| 單價／數量 | `order_items.unit_price`、`quantity` | 已有 |
| 訂購日期時間 | `orders.created_at` | 已有 |
| 領取日期時間地點 | `order_fulfillments`、`pickup_locations` | 地點主檔與訂單關聯已新增；排程操作介面待補 |
| 付款方式／金額 | `payment_attempts`、`orders.amount_total` | 已有；目前只支援綠界預付 |
| 訂單號碼 | `orders.order_number` | 已有日期時間與唯一亂數 |
| 應／免稅 | `order_items.tax_type` | 已有 |
| 稅金金額 | `orders.tax_amount` | 新增下單快照；含稅價捨入規則待會計確認 |

## 3. 領取地點

新增 `pickup_locations` 主檔，初始資料包含：

- 合作社門市（水木書苑內左側）
- 台積館
- 教育學院大樓
- 人社院
- 創新育成大樓

地點可由管理員新增、排序、停用；歷史訂單仍保留當時名稱快照。

## 4. 產品資料

| 文件欄位 | 系統位置 | 狀態 |
| --- | --- | --- |
| 編號 | `products.product_number` | 新增 |
| 名稱 | `products.name` | 已有 |
| SKU | `products.sku` | 新增；每種包裝視為可獨立販售 SKU |
| 產品說明 | `products.description` | 已有 |
| 供應者 | `products.supplier_id` | 新增 |

## 5. 供應者資料

新增 `suppliers`：供應商編號、商號、統編、負責人、業務接洽人、電話、Email、LINE、結帳規律、銀行帳號、審認日期與啟用狀態。私人聯絡與銀行欄位加密。

## 6. 供應者審認

新增 `supplier_accreditations` 與 `supplier_documents`：審認日期、人員、過程、結果、供應商編號，以及 PDF／JPG 文件中繼資料。文件實體延用私有 R2 與短效簽名網址，但需先把目前只服務入社證件的儲存介面泛化。

## 尚待合作社確認

1. 社員編號若包含身分證片段與出生年，是否已完成個資風險評估，以及舊編號如何轉換。
2. 手機號碼是否只是登入／查詢憑證，或真的要成為對外會員編號；換號、重複、停號與回收號碼如何處理。
3. 性別、籍貫的允許值、填寫必要性與保存目的。
4. 股金與股數的換算規則、面額，以及部分繳納／增資／減資／返還的歷程要求。
5. 稅額應保存下單快照，或以會計期間報表重算；含稅價的捨入規則需由會計確認。
6. 付款方式目前是否維持綠界預付，或未來要加入匯款、現金等人工對帳流程。
7. 供應者審認狀態、複審週期、到期與撤銷規則。
8. 供應商銀行帳號的可見角色、修改覆核與稽核保存期限。
9. 供應商文件種類、必填文件、有效期限、版本與刪除政策。
10. 文件指定的訂單號為「日期＋當日流水號」；目前使用日期時間＋亂數以避免併發衝突，正式顯示格式與舊訂單轉換方式待確認。
11. 一般商品與團購的取貨起訖時間由誰排定，以及可否讓消費者改選時段。
