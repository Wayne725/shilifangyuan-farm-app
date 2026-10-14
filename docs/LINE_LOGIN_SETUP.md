# LINE 登入設定與驗收

日期：2026-10-13

正式設定與部署已完成：Provider「十里方圓」、LINE Login Channel `2011990124` 已為 Published，正式回呼網址與 Render 後端設定已儲存。前後端 `f68c237` 均為 Live，已在正式站完成真實 LINE 授權並連結 `teas`；帳號中心顯示已連結，原有正式社員身分仍保留。

## 帳號與 Channel

1. 若合作社已有 LINE Login Channel，沿用原 Channel，由其管理員在 Roles 邀請目前開發者帳號。
2. 若只有官方帳號，先確認 Provider 的所有權，再建立 LINE Login Channel。新 Channel 的協議由站方確認。
3. App types 選 Web app，Callback URL 設為 `https://shilifangyuan.com/auth/line/callback`。
4. 測試 Developing Channel 需 LINE 帳號具備 Admin／Tester 角色。一般使用者啟用前，須將 Channel 發布為 Published。
5. 有 Email 權限可申請 `email` scope；未核准時先使用 `openid profile`，新使用者只補一次通知信箱。

## 後端設定

| 環境變數 | 值 |
| --- | --- |
| LINE_LOGIN_CHANNEL_ID | 2011990124 |
| LINE_LOGIN_CHANNEL_SECRET | Channel secret；僅存後端環境變數，不提交 Git 或傳給前端 |
| LINE_LOGIN_REDIRECT_URI | https://shilifangyuan.com/auth/line/callback |
| LINE_LOGIN_SCOPE | 目前為 openid profile；取得 Email 權限後可加入 email |
| WEB_BASE_URL | https://shilifangyuan.com，須與回呼網址相同 origin |

沿用正式 HttpOnly／Secure cookie 及前端 `/v1/` API 代理設定。缺少設定時 `/v1/auth/line/status` 回傳 ready=false，LINE 按鈕停用，原 Email 登入仍可使用。

## 帳號與社員資格

- 已有 Email 帳號：先登入原帳號，再於帳號中心按「連結LINE」，保留訂單與社員資格。
- 相同 Email 不自動合併帳號，避免另一個身分取得原帳號權限。
- 新 LINE 使用者可選「我是社員／我不是社員」，意向不直接授予社員或管理員權限，仍須核對線下資格或既有名冊。
- 首次註冊選社員並補填信箱後，前往帳號中心認領既有社員資格；非社員返回原頁。既有 LINE 身分登入不受這個新註冊分流影響。
- LINE 身分確認與本站 Email 驗證分開；新使用者無須建立密碼。
- 缺少 LINE Email 時，填一次通知信箱；重整可恢復尚未到期的補填流程。

## 驗證紀錄與待驗項目

- GitHub CI `38356508862` 的 backend、api-contract、frontend-quality 全部通過；包含完整後端測試、PostgreSQL 遷移升降版與付款／發票並行測試。
- 本機 66 前端單元檢查與 34 瀏覽器隔離測試通過，LINE／遷移／發布隔離相關 91 項回歸通過。
- 0021 遷移新增身分與一次性授權資料，保留既有使用者；已有 LINE 身分後禁止直接回退刪除資料。
- 正式站真實授權已成功返回帳號中心；`teas` 顯示已連結 LINE、Email 已驗證、正式社員。原頁返回與既有社員狀態均已確認。
- 已在正式站登出 `teas`，再透過已連結的 LINE 登入並返回網站，重整後仍為原正式社員帳號。尚未實測瀏覽器完全關閉後的長期 Cookie 存續或新使用者註冊、通知 Email。
- state、PKCE、nonce、官方 ID token、瀏覽器綁定、到期及重播保護均須通過測試。
- 帳號連結須在 LINE 授權返回後重新鎖定並核對原帳號，期間登出或停用時拒絕連結。
- 前後端同版本發布後，在正式網域完成一次真實授權往返，確認 cookie、保持登入與原頁返回。
- 正式寫入金融交易不作為 LINE 測試的一部分。

相關：[便當管理員手冊](MEAL_ADMIN_MANUAL.md)、[需求狀態](MEAL_REQUIREMENTS_STATUS.md)。
