# 入社教育與審核第一階段 API 契約

日期：2026-10-29。範圍為新流程的資料、志願書、教材、測驗與審核；不建立會籍、應收、繳費通知或發證。既有正式社員名冊認領及歷史會籍不自動轉換。

## 共同型別

`flow_version` 固定 `education-review-v1`。Round.status 為 `draft | submitted | needs_supplement | approved | rejected | withdrawn`。approved 只表示審核同意、後續待開放。version 從1單調增加；所有變更先鎖 User，再鎖 round。

Profile 使用現有基本資料字段：legal_name、phone、birth_date（ISO日期）、address（現居）、emergency_contact、gender、place_of_origin、occupation、registered_address、correspondence_address、landline_phone、line_id；另加 contact_email。前五項及 contact_email 必填，其餘沿用現有可選規則；新志願書畫面可以提示填妥戶籍／通訊資料。禁止 identity_number、證件欄位、任意額外欄位。Profile 是每輪加密快照，不寫回舊 MemberProfile。

RoundRead：

```ts
{
  id: string; user_id: string; round_number: number; version: number;
  flow_version: 'education-review-v1'; status: string;
  profile: Profile | null;
  requested_share_count: number; student_claimed: boolean;
  discovery_channel: string | null; discovery_other: string | null;
  declaration_version: string | null; privacy_version: string | null;
  consented_at: string | null;
  material_version: string | null; material_completed_at: string | null;
  quiz_version: string;
  quiz_eligibility: {allowed:boolean;remaining_attempts:number;next_available_at:string|null;reason:string|null;settings_configured:boolean};
  attempts: QuizAttemptRead[];
  submissions: {id:string;revision_number:number;submitted_at:string}[];
  reviews: {id:string;decision:string;reason:string;decision_reference:string|null;student_verified:boolean|null;reviewed_at:string}[];
  submitted_at:string|null; reviewed_at:string|null; review_reason:string|null;
  student_verified:boolean|null; decision_reference:string|null;
  created_at:string;updated_at:string;
  can_edit:boolean; can_submit:boolean; can_withdraw:boolean;
}
```

QuizAttemptRead：id、attempt_number、quiz_version、score、passed、submitted_at、settings_version，另有 results 陣列，每項 question_id、selected_option、correct_option、correct。僅本人／管理員已交卷結果可讀正解；Config 的題目沒有正解。

## 讀取

`GET /v1/membership/admission`（登入本人）：

```ts
{
 config: {
   flow_version:string;
   material:{version:string;title:string;url:string;sections:{title:string;body:string}[]};
   quiz:{version:string;pass_score:80;points_per_question:10;questions:{id:string;question:string;options:{value:string;label:string}[]}[]};
   declaration:{version:string;privacy_version:string;privacy_url:string;rights:string[];obligations:string[];statement:string;share_unit_amount:100;student_min_shares:5;student_max_shares:10;general_min_shares:10;general_max_shares:100};
   discovery_channels:string[];
 };
 current_round:RoundRead|null;
 history:{id:string;round_number:number;status:string;created_at:string;reviewed_at:string|null}[];
 profile_defaults:Profile|null;
 can_start:boolean;start_blocked_reason:string|null;
}
```

`GET /v1/membership/admission/rounds/{id}` 回 RoundRead，非本人404、管理員可讀。history 詳情按需讀取，不把其他輪合格帶到新輪。

Config material URL 使用本次提供的 Drive 文件；sections 是本次題目涵蓋的簡短閱讀重點，不複製整本簡報。原文件含2023舊題與舊費用，本次正式測驗以使用者提供10題為準，繳費階段未開放。quiz.version 使用2026-10-29.v1。

## 本人變更（全部回新的 RoundRead）

- `POST /v1/membership/admission/rounds`：`{idempotency_key:UUID}`。已有非結案的最新round則回該輪；只有rejected/withdrawn後才建立下一輪。相同key重送回原round。新輪不能沿用舊教材、成績、同意與審核。
- `PUT /v1/membership/admission/rounds/{id}`：`{expected_version, profile:Profile, requested_share_count:int, student_claimed:bool, discovery_channel:string, discovery_other?:string|null, declaration_version:string,privacy_version:string,declaration_accepted:true,privacy_accepted:true,share_rules_understood:true,payment_notice_understood:true}`。只能draft/needs_supplement。學生聲明5–10股，其餘10–100股，每股100元為認股意向，不建立應繳款。來源「其他」可補充文字，其他類別不收自由文字。明確同意時間由server記錄；不得自動預勾。
- `POST /v1/membership/admission/rounds/{id}/education`：`{expected_version,material_version,read_confirmed:true}`。需已保存資料/志願書。這是本人閱讀聲明，不宣稱已追蹤閱讀時長。
- `POST /v1/membership/admission/rounds/{id}/quiz-attempts`：`{expected_version,idempotency_key:UUID,quiz_version,answers:{question_id:string;option:string}[]}`。10題必答、後端評分；先確認同key同payload可重送，異payload409。每輪次數及最後交卷時間由資料庫計算，None policy只允許首次，管理員設定額外次數與間隔。補件保留本輪次數，已合格不得再交卷。
- `POST /v1/membership/admission/rounds/{id}/submit`：`{expected_version}`。draft/needs_supplement且本輪資料同意、閱讀及80分合格才可送審，建立不可變Submission快照；補件是同輪新revision，保留舊版。
- `POST /v1/membership/admission/rounds/{id}/withdraw`：`{expected_version,reason?:string}`。只關閉本輪、不改歷史會籍或款項。

Profile首次預填僅從本人既有資料讀取，新的輪次保存之後獨立加密。已存在active/trainee/pending_payment會籍或任何待核對應收不能開始新輪；suspended沿既有無款項復申請限制；resigned/terminated先核對歷史。只有未產生會籍/款項的legacy submitted/needs_supplement可明確開始新版，舊件保留不改。

## 管理員

- `GET /v1/admin/membership/admissions?status=pending&q=&limit=25&offset=0`：回 `{items:RoundRead[],total:number,limit:number,offset:number}`；status=pending 為submitted/needs_supplement，all不限，其餘可傳個別狀態；q依申請姓名、contact_email、帳號名稱／Email搜尋。只回目前頁，不回其他會員私密資訊。列表顯示每人每輪，保留結案歷史。
- `POST /v1/admin/membership/admissions/{id}/review`：`{expected_version,decision:'approve'|'request_supplement'|'reject',reason:string,decision_reference?:string,student_verified?:boolean}`。只接受submitted。approve要求本輪合格、決議reference；學生聲明時必須student_verified=true。request_supplement/reject均需原因；每次記錄Review及AdminAudit。回RoundRead。

所有新路徑均不得寫 Membership、MembershipCharge、付款通知Outbox、股票或社員證。一般購買／活動／既有名冊認領依原權限；有新流程歷史的使用者，舊入社核准/建應收/啟用不得繞過新版未開放的財務階段。歷史已收款仍記實收與退款，不為防旁路丟棄付款結果。

本次不自動寄信；審核進度在站內可查，財務收款及通知待後續確認。跨使用者404、非管理員403、版本衝突409保留草稿；非法／未知欄位422。不同意不送資料，可返回或撤回本輪。
