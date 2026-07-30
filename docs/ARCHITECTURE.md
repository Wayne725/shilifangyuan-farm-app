# 十里方圓 v2 系統架構

## 系統全貌

```mermaid
flowchart LR
    app["Expo App<br/>iOS・Android・Web"] --> api["FastAPI API"]
    admin["管理員模組"] --> api
    api --> auth["JWT・Email 驗證・有效會籍"]
    api --> commerce["商品・團購・便當・訂單"]
    api --> community["入社・活動・治理提案"]
    api --> db[("PostgreSQL")]
    api --> r2[("Cloudflare R2<br/>私有測試證件")]
    api --> ecpay["綠界 AIO・B2C 發票・物流 Stage"]
    api --> outbox[("Outbox")]
    outbox --> sendgrid["SendGrid"]
    cron["GitHub Actions"] --> jobs["Reconciliation"]
    jobs --> db
    jobs --> outbox
```

前端不判斷最終社員資格、價格、付款結果或可執行動作。FastAPI 依有效會籍與資料庫狀態計算，訂單回傳 `available_actions`、`fulfillment` 與 `shipment`。

## 兩個工作區

- `生活消費`：首頁、團購、便當、訂單、我的；購物車放在頁首。
- `社務系統`：社務首頁、社員、活動、提案、我的。
- `WorkspaceContext` 分別記住兩個工作區最後開啟的頁面。
- 管理端以總覽、販售、訂單與物流、社務、設定為模組入口。

一般商品、團購與便當共用 `CatalogCard`：手機兩欄、圖片 4:3；詳情圖 3:2 並限制高度。團購只額外顯示狀態、截止、門檻與細進度條。

## 身分與入社

```text
User                    登入帳號與 customer/admin 管理權限
MembershipApplication   入社申請與審核
Membership              會籍與社員編號
MemberProfile           AES-GCM 加密私密資料
MemberDirectoryEntry    自願公開資料
```

`users.membership_type` 是 migration 相容欄位，不是資格真相。只有 `memberships.status=active` 才能取得社員價、查看及報名活動、治理提案、留言與記名投票。

```mermaid
stateDiagram-v2
    [*] --> 註冊
    註冊 --> Email已驗證
    Email已驗證 --> 草稿
    草稿 --> 待審核: 資料及三份測試證件齊全
    待審核 --> 補件
    補件 --> 待審核
    待審核 --> 已駁回
    待審核 --> 待付款: 管理員核准
    待付款 --> 有效社員: 入社費與股金皆付款
    有效社員 --> 停權
    有效社員 --> 退社
    有效社員 --> 終止
```

入社費與股金是通用付款主體 `membership_charge`，不建立銷售訂單、不開電子發票，只產生系統收據。

## 社員活動與治理提案

活動由有效社員建立、管理員審核。正式名額依 `queue_position` 排序；滿額後進候補，正式名額取消時自動遞補最早候補者。活動結束後未簽到者由 reconciliation 標記 `no_show`。

治理提案與商品團購提案使用不同資料表及狀態：

```text
draft → pending_review → discussion → voting → passed/rejected → closed
```

預設討論 3 天、表決 7 天、最低 10 名有效社員。`yes/no/abstain` 是公開記名票；棄權計入最低投票數但不進入贊成率，只有 `yes > no` 才通過。

## 銷售與履約

訂單分成兩個獨立維度：

```text
sales_channel       regular | group | meal_preorder
fulfillment_method  cooperative_pickup | event_pickup | ecpay_logistics
```

`OrderFulfillment.status` 是履約資格真相：

```text
pending_confirmation | preparing | ready_for_pickup | picked_up |
awaiting_shipment | shipped | delivered | no_show | cancelled
```

`orders.fulfillment_status` 只保留舊資料相容。物流細節保存於一對一 `Shipment`；價格、品名、稅別及下單時社員資格都保存在訂單快照。

## 便當容量

1. `Meal` 是可重用餐點；`MealEvent` 是單次校園／攤位場次。
2. `MealEventOffering` 保存該場次價格、容量、保留量及已付款量。
3. 建立付款頁時以資料庫鎖保留 15 分鐘。
4. 截止前建立的付款，允許在保留期限內跨過截止完成。
5. 核銷六位短碼後標記 `picked_up`；取餐結束仍未領取者標記 `no_show`。
6. `picked_up` 與 `no_show` 都會進入發票 Outbox；未取不退款。

## 物流

- 商品／團購設定 `can_ship`、溫層及允許通路。
- 同一訂單必須是單一溫層、單一地址、單一包裹，不相容時要求分開結帳。
- 收件人姓名、電話、地址使用版本化 AES-256-GCM。
- 綠界物流 Selection callback 以一次性 token 綁定訂單；Server callback 以 `ExternalEvent` 去重。
- 團購在管理員確認成團且進入備貨後才建立正式物流單。
- Stage 後續貨態由管理員 Sandbox 按鈕推進，每次寫入 `AdminAudit`；正式環境只接受已驗證回呼。

## 私密證件

- R2 Bucket 不啟用公開網域。
- PUT 簽名 URL 5 分鐘、管理員 GET 2 分鐘。
- 只允許 JPEG、PNG、PDF，單檔最多 8MB。
- object key 完全隨機，不包含姓名、電話或證件號。
- 確認時以 HEAD 驗證 Content-Type、大小與 SHA-256。
- 私密資料及證件只供本人與管理員；管理員查看、補件與刪除皆留稽核。
- `sandbox` 缺少 R2、PII 或物流密鑰時 API 拒絕啟動。

## 金流、發票與背景工作

`PaymentAttempt` 與 `Refund` 都只能指向 `order` 或 `membership_charge` 其中一種。綠界回呼驗證簽章、商店、交易編號、金額與狀態，並以 `ExternalEvent` 防止重複及亂序。

發票資格：

- 合作社／活動現場取貨：核銷完成。
- 物流：確認送達。
- 便當未取：場次結束並標記 `no_show`。
- 入社費與股金：不開發票。

`POST /internal/reconcile` 處理付款保留、團購截止、治理提案時鐘、活動結束、便當截止／未取、Sandbox 退款、發票與 Email Outbox。GitHub Actions 定期呼叫，API 請求也提供 lazy reconciliation。
