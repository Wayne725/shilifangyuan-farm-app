# LINE 登入設定與驗收

日期：2026-10-13

正式設定與部署已完成：Provider「十里方圓」、LINE Login Channel `2011990124` 已為 Published，正式回呼網址與 Render 後端設定已儲存。前後端 `f68c237` 均為 Live，已在正式站完成真實 LINE 授權並連結 `teas`；帳號中心顯示已連結，原有正式社員身分仍保留。

## 帳號與 Channel

1. 若合作社已有 LINE Login Channel，沿用原 Channel，由其管理員在 Roles 邀請目前開發者帳號。
2. 若只有官方帳號，先確認 Provider 的所有權，再建立 LINE Login Channel。新 Channel 的協議由站方確認。
3. App types 選 Web app，Callback URL 設為 `https://shilifangyuan.com/auth/line/callback`。
4. 測試 Developing Channel 需 LINE 帳號具備 Admin／Tester 角色。一般使用者啟用前，須將 Channel 發布為 Published。
5. 有 Email 權限可申請 `email` scope；未核准時先使用 `openid profile`，仍可自動建立顧客帳號，通知信箱到結帳才填。

## 後端設定

| 環境變數 | 值 |
| --- | --- |
| LINE_LOGIN_CHANNEL_ID | 2011990124 |
| LINE_LOGIN_CHANNEL_SECRET | Channel secret；僅存後端環境變數，不提交 Git 或傳給前端 |
| LINE_LOGIN_REDIRECT_URI | https://shilifangyuan.com/auth/line/callback |
| LINE_LOGIN_SCOPE | 目前為 openid profile；取得 Email 權限後可加入 email |
| WEB_BASE_URL | https://shilifangyuan.com，須與回呼網址相同 origin |

沿用正式 HttpOnly／Secure cookie 及前端 `/v1/` API 代理設定。缺少設定時 `/v1/auth/line/status` 回傳 ready=false，LINE 按鈕停用，原 Email 登入仍可使用。

## LINE 官方帳號入口與資料同意

- 官方帳號圖文選單可新增「便當預訂／LINE登入」連結，網址設為 `https://shilifangyuan.com/line-consent`。
- 此頁先說明網站會使用 LINE 使用者識別碼與顯示名稱來建立及辨認網站帳號，並說明訂單、社員資格及通知 Email 的資料用途；使用者勾選同意後才可開始 LINE 授權。
- 後端只接受目前同意版本，成功登入或連結後，在 LINE 身分紀錄保存同意版本及時間。取消 LINE 授權不會自動刪除網站已建立的帳號或訂單。
- 2026-10-16 已在官方帳號「十里方圓(測試版)」`@939gjjuy` 的既有圖文選單 B「便當訂購」儲存上述網址。選單圖片、其餘動作與使用期間沿用原設定。
- 已登入者可從同意頁直接按「開始訂餐」前往 `/shop`；需要連結既有 Email 帳號時，另提供帳號中心入口。
- Render Dashboard 目前因指定提交部署而停用 Auto-Deploy。`render.yaml` 的 `checksPass` 不代表 Dashboard 已啟用；發布時須確認實際 Live 提交，並手動部署已驗證的版本。

## 帳號與社員資格

- 已有 Email 帳號：先登入原帳號，再於帳號中心按「連結LINE」，保留訂單與社員資格。
- 相同 Email 不自動合併帳號，避免另一個身分取得原帳號權限。
- 新 LINE 使用者可選「我是社員／我不是社員」，意向不直接授予社員或管理員權限，仍須核對線下資格或既有名冊。
- 新身分授權後直接自動建立一般顧客帳號，無 Email 也可回原頁訂餐。意向不改變返回路徑；管理員另核對名冊與線下會籍。
- LINE 身分確認與本站 Email 驗證分開；新使用者無須建立密碼。
- 登入 Email 可為空，結帳通知信箱另存 notification_email，三種結帳都以使用者確認的通知信箱建單。結帳填寫不視為帳號 Email 驗證，也不合併帳號。
- 舊版留下的尚未到期待註冊流程可自動完成，保留瀏覽器綁定與一次性保護；新授權不再產生補 Email 頁。
- 新增 0022_line_customer_email 遷移：既有 Email、密碼與 LINE 綁定保持不變；已有無 Email 帳號時阻止直接回退必填欄位。

## 驗證紀錄與待驗項目

- GitHub CI `38356508862` 的 backend、api-contract、frontend-quality 全部通過；包含完整後端測試、PostgreSQL 遷移升降版與付款／發票並行測試。
- 本機 66 前端單元檢查與 34 瀏覽器隔離測試通過，LINE／遷移／發布隔離相關 91 項回歸通過。
- 0021 遷移新增身分與一次性授權資料，保留既有使用者；已有 LINE 身分後禁止直接回退刪除資料。
- 正式站真實授權已成功返回帳號中心；`teas` 顯示已連結 LINE、Email 已驗證、正式社員。原頁返回與既有社員狀態均已確認。
- 已在正式站登出 `teas`，再透過已連結的 LINE 登入並返回網站，重整後仍為原正式社員帳號。全新 LINE 身分自動建帳及瀏覽器完全關閉後的長期 Cookie 存續，仍須另外區分真實使用者驗收與隔離驗證。
- state、PKCE、nonce、官方 ID token、瀏覽器綁定、到期及重播保護均須通過測試。
- 帳號連結須在 LINE 授權返回後重新鎖定並核對原帳號，期間登出或停用時拒絕連結。
- 前後端同版本發布後，在正式網域完成一次真實授權往返，確認 cookie、保持登入與原頁返回。
- 正式寫入金融交易不作為 LINE 測試的一部分。

## 2026-10-15 自動建帳相容性

- 結帳聯絡 Email 未傳或為 null，但已填合法的發票 Email 時，後端沿用該通知信箱；兩者都未填仍拒絕建立訂單。這保留已開啟舊版頁面的結帳能力。
- 三種結帳在同一筆交易保存通知偏好及訂單通知快照；不以通知信箱更新登入憑證、驗證狀態或合併既有帳號。
- 無登入 Email 的顧客可由管理員按姓名、會員編號或通知信箱查詢，核對線下名冊後授予會籍。既有已驗證 Email 帳號仍保留原認領流程。
- 測試僅使用隔離回應與本機資料庫，不為驗收建立真實付款或發票。

相關：[便當管理員手冊](MEAL_ADMIN_MANUAL.md)、[需求狀態](MEAL_REQUIREMENTS_STATUS.md)。
