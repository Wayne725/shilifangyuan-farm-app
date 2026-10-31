# 資料庫正規化、約束與歷史資料複核

日期：2026-10-31。審查 HEAD `679d659cbd7e2f787ba952d778dfc5e30f71259b` 加目前已凍結、尚未提交的 D02–D06／0042–0044。模型及 migration SHA256 均保存；本報告只讀產品，沒有查正式 DB／個資、讀 .env、改產品或執行外部交易。

結論：這是以關聯實體為主、保留交易文件及少數效能投影的混合模型。**不能宣稱全庫嚴格 3NF，也不能說「沒有正規化」；不建議為了形式刪除成交／送審快照。**舊 D01–D06 的主要可達缺陷已有具體修復與定向證據；本輪新確認的是幾類 DB 防線缺口及 schema/bootstrap 預設值差異，尚未發現新的合法 API 可達資料毀損路徑。

## 範圍與證據

離線讀取全部模型：**84 業務表、977 欄、140 個 FK、108 CHECK、59 個 UniqueConstraint、40 個 unique index**。這些是本次 head0044 的模型來源快照，不是正式資料庫存量或已查核的正式DB schema。每表都有單欄 PK；140 FK 皆單欄，刪除策略為 RESTRICT56／CASCADE54／SET NULL30。索引共254，數目本身不代表查詢一定有效。完整欄位、nullable、default、PK／FK／unique／CHECK／partial predicate 見 [metadata JSON](../outputs/remediation-completion/database-normalization-metadata.json) 與 [唯讀工具](../outputs/remediation-completion/database_normalization_metadata_probe.py)。

另以 app factory 的新記憶體 SQLite、FK=1、全合成資料做 **15 個約束邊界觀測**，每案 rollback、沒有正式資料：[原始結果](../outputs/remediation-completion/database-normalization-constraints.json)、[工具](../outputs/remediation-completion/database_normalization_constraints_probe.py)、[log](../outputs/remediation-completion/database-normalization-constraints.log)。這些觀測不是15個漏洞，也不是新的15案產品修復綠燈。

最新0044在獨立暫存SQLite成功升級，**84業務表＋1 Alembic版本表**；欄位／型別／nullable／unique／index／FK 的常規 metadata compare 無差異，額外 CHECK SQL 比對亦無差異。server default 另有下述差異；一般autogenerate不完整比較partial predicate，新增partial條件另以來源常數及實際拒重複觀測交叉核對。暫存DB已刪；不與root的PG schema／全套共用任何寫入。[遷移結果](../outputs/remediation-completion/database-normalization-migration.json)、[log](../outputs/remediation-completion/database-normalization-migration.log)、[工具](../outputs/remediation-completion/database_normalization_migration_probe.py)。這不等於正式歷史資料 backfill 已驗收。

## 依函數依賴判斷 1NF／2NF／3NF

有 surrogate PK 並不能自動證明2NF／3NF；下表同時看自然候選鍵與實際業務事實。

| 資料類別 | 主要相依／結構 | 判斷 |
|---|---|---|
| User、ExternalIdentity、token／session | User.id→登入資料；(provider,subject)→user_id，另(provider,user_id)唯一 | 身分與外部帳號、token拆表，主要標量符合關聯1NF；未見把多個外部身分塞進User欄位。email可空以支援LINE等情況，NULL unique不等於漏做唯一性。 |
| Product、Supplier、Accreditation、Document | product_id→目前商品屬性；supplier_id→供應者資料，文件／審核另表 | 大致3NF方向。Product沒有再存目前supplier商號，而以關聯／property取得；不用隨商品重複更新供應者主檔。 |
| BundleItem | (bundle_id,product_id)→quantity,position | 屬於兩者的關聯屬性，非只依賴其中一半；quantity>0、pair unique及FK均在，符合2NF／3NF方向。商品名稱不重複放此表。 |
| Order、OrderItem、OrderItemOption、Fulfillment、InvoiceItem | order／line／document identity→成交當時條件；集合拆成子表 | 重複品項／選項已拆表。歷史名稱／價稅／地址不由目前Product或User決定，不能誤認 product_id→成交價而刪快照；以時點事實看並非錯誤的3NF相依。總額是聚合投影，另見下表。 |
| ActivityRegistration、MeetingAttendance、Vote、MemberProposalVote、WishSupport、MemberBadge | (活動或議題,user)→該次參與狀態／選擇／時間 | 關聯實體與pair unique齊備，沒有只依賴user的目前主檔欄位混入投票事實。選項、留言、議程決議各有明確擁有者。option與proposal同父一致性仍有DB強化空間。 |
| ActivityCharge | (registration_id,registration_revision)→收費快照，但 registration_id→user_id,activity_id | 此處保留可推導的父層ID，是**明確反正規化**；按自然候選鍵分析有部分相依，不能稱嚴格2NF／3NF。聯絡資料、身分價與金額則是該版收費快照，有保留理由。各FK只能證明存在，不能證明三者同一報名。 |
| MembershipCharge | (application_id,charge_kind)→一次費用；application_id→user_id，並關聯Membership | 同樣有父層身分冗餘／部分相依；查詢與鎖定方便，但需同父驗證。fee_schedule.amount是「現行費率」，charge.amount是「當次應收」，兩者不是應隨主檔一起更新的重複事實。 |
| Admission Round／QuizAttempt／Submission／Review | (user,round_number)、(round,attempt_number)、(round,revision)、submission→review | 輪次、測驗、送審、審核分離，版本化歷史方向正確。Submission另存round_id，Review另存round_id，屬可推導父ID；若要求嚴格3NF可經parent推導，實務保留時宜加同父複合FK。不能刪除送審時profile／facts改讀現稿。 |
| PointAccount／PointTransaction、FiscalYear／Ledger／Distribution | account→owner；有ref的(account,source,ref)→一筆異動；(year,member)→分配 | 沒有第二份持久point balance，餘額依流水加總，避免更新異常。年度ledger／分配是確認時事實，後來訂單變動不應重算覆蓋。期間不重疊另由應用交易協定保護，非僅靠year label。 |
| Notification／Outbox／ExternalEvent | (user,event,dedupe_hash)→新通知；notification_id→一個郵件意圖；(provider,key)→外部事件 | 本輪將必要唯一識別從JSON提升為一級欄位與DB unique，改善「先查後建／只靠JSON慣例」。nullable key保留無去重鍵的新事件與歷史重複，不是要把所有NULL填成同一值。 |
| JSON／加密文件／設定 | 有序內容、版本化答案、原始回執、配置、快照 | 若按嚴格「每欄僅不可拆標量」解讀，JSON array／巢狀物件不屬純關聯1NF；本系統將它們作文件domain值，屬刻意取捨。opaque加密欄位在DB是單一值，不能為形式拆表而落敏感明文。 |

因此不是「改成UUID PK就已正規化」。主要可交易資料已良好拆分，但收費父ID冗餘、legacy投影、文件／設定JSON使全庫不宜標為無例外3NF。正規化、歷史真實性與交易鎖定是三個不同問題。

## 必須保留的快照與可重建投影

| 類型 | 現行用途與應保留規則 |
|---|---|
| 成交／收費快照 | OrderItem名稱、unit_price、tax_type、會員價稅身分、活動收費聯絡人／金額、Fulfillment地址／取貨資訊、Invoice品項與供應商帳戶context。依交易／revision決定，不能由目前主檔覆蓋。 |
| 修訂／送審／列印快照 | OrderAmendment前後JSON、AdmissionSubmission加密內容／facts、QuizAttempt題庫／設定版本／答案結果、PrintJob.snapshot/content。這些是歷史證據與已授權輸出，不能以「可從現稿再組」替代。 |
| 原始外部文件 | PaymentAttempt.checkout_payload/provider_response、Invoice/Refund/Shipment外部request/response、ExternalEvent、FinancialStatementImport.rows。是協定／稽核文件，外部格式不需要為3NF攤平成一堆欄；對常用查詢／約束的key才提升欄位。不得把可空receipt／provider_trade_no強改必填，因送出前／未知時合法不存在。 |
| 同一事實的legacy投影 | User.membership_type、Order.order_kind、Order.fulfillment_status等有legacy comment；權限仍以Membership.status、履約以OrderFulfillment.status為準。應集中transition與一致性檢查，不能僅刪欄位讓舊回應／歷史單失效。Order.payment_status是「當前版訂單」的聚合結果，並非隨便挑一筆attempt即可取代。 |
| 熱路徑數量與金額投影 | Offering/Campaign的paid/reserved quantity、Order totals/tax、Invoice totals。可由明確來源重算以稽核，但保留是為原子配額／穩定對帳；Offering已有非負及capacity CHECK，並使用父層鎖。Group計數也需同樣明定來源與unknown／retained狀態，不能直接SUM所有歷史訂單。 |
| 小型配置JSON | allowed_shipping_channels、schedule weekdays／offerings、badge rule、meeting agenda、system settings。目前是整份設定或有序文件。尤其schedule.offerings是**活動中的設定清單**，meal_id在JSON沒有FK；若未來需逐項查詢／關聯刪除保護，優先拆TemplateOffering子表，先處理失效ID，非直接drop模板。 |
| 去重與合約digest | Notification.dedupe_hash保留原key的可索引摘要；Order.group_contract_fingerprint作下單條款比對，不是能還原原條款全文的備份。原items／fulfillment／稽核仍需保存。 |

**不能新增 `OrderItem.subtotal = quantity * unit_price` 的盲目CHECK。**`order_amendments._quote`會把優惠按品項分攤，subtotal合法為乘積減reduction；運費、稅內含、退款亦非簡單等式。可加的完整性檢查須依已保存的折扣／revision語義定義。

## 本輪已確認的 DB 防線缺口

以下均用全假資料直接寫owned DB／或讀取DDL確認；**不宣稱已經能從合法API繞過，不計為新的P1線上事故**。

| 類別／優先級 | 已確認事實 | 現行應用保護、建議與驗收 |
|---|---|---|
| 同父一致性／P2強化 | ActivityCharge可指報名A，同時填另一user及activity；Submission可引用另一round quiz；Review可指另一round submission，全部FK仍通過。 | `admit_registration`、`submit_round`、`review_round`目前由已鎖父層自行組資料／限定本輪查詢，未找到公開造錯配路徑。日後加parent複合UNIQUE及複合FK，或集中唯一constructor並有反向一致性稽核；MemberProposalVote.option/proposal及MembershipCharge同類需同時盤點。先查legacy錯配，列出識別供人工核對，不能自動改成另一個人。 |
| 範圍／counter／P2強化 | 直接DB接受Order.revision=0、Campaign.paid_quantity=-1/reserved_quantity=-2。 | API／付款service有版本、鎖與配額控制；不是已證實超賣。可優先加revision≥1及counter非負CHECK；total≤cap須先確認歷史、未知款與例外容量規則，不能先假定。遷移preflight阻擋不合法歷史，不刪／歸零。 |
| enum domain／P3強化 | enum_type(native_enum=False,validate_strings=True)不產生自動DB CHECK；55個enum映射欄位不應被當成55個漏洞。raw SQL可填User.user_role='not_a_role'，ORM後續讀取可能LookupError。 | 正常Pydantic／SQLAlchemy Enum寫入會驗值；沒有由此取得admin權限的證據。針對重要status新增顯式CHECK並預查舊值，先不全庫改native PostgreSQL Enum。 |
| 有意的多型參照／設計取捨 | Proposal/Campaign.target_type+target_id、Audit／Outbox aggregate type/id不能由普通單一FK指到多表；JSON內meal id亦無FK。 | 不是「忘記所有FK」。目前target resolve、Wish422 prevalidation等有應用驗證。未來需要DB強制時可考慮共同Target identity／子類FK或專門關聯表，但需維持custom與歷史快照，不為本輪一次大遷移。 |
| 刪除／保留／P3設計 | Order硬刪會cascade items/attempt/refund/invoice等；user的部分私人資料cascade，財務關聯多RESTRICT。 | 未找到一般刪Order／User的公開路由，現行用取消／退款；不能報成使用者可刪帳。未來資料保留／個資刪除程序須明定匿名化與財務保留，不能讓cleanup以ORM cascade意外清掉交易證據。 |

15案中真正拒絕的控制：孤兒FK、第二PointAccount、無付款主體、quantity=0、重複open target、新通知dedupe、同notification第二Outbox；closed提案與NULL dedupe可以共存則是合法控制。**nullable FK表示尚未發生／來源刪除後保留快照，不等於orphan；NULL unique也不代表唯一鍵失效。**

## 新確認的 schema/bootstrap default 差異

常規Alembic compare未開 `compare_server_default` 會漏掉這類問題。開啟後15個原始default差異，另以實際SQLite expression／原表插入區分：3個是空字串render形式等價；11個為migration保留server default、模型僅有Python default；另1個Boolean有實際行為差異。

**P3：MealEvent.allow_cash_payment的SQLite建庫路徑不一致。**模型 `server_default="false"` 產生字串literal `'false'`；raw INSERT省略欄位，SQLite存TEXT false，SQLAlchemy Boolean讀成True。Alembic0044庫相同省略寫入存整數0／讀False；正常ORM建立兩者皆False。原表／原模型對照結果已在migration JSON保留。未證實正常API錯開現場付款，故不是正式收款事故；建議以 `server_default=false()` 對齊語意，另做正式紅綠後再落產品。

其餘11個：MealScheduleTemplate.cutoff_days_before／advance_days／enabled／auto_publish；MemberProposal.proposal_type；MemberRosterEntry.share_capital_amount／share_count／is_active；Membership.share_capital_amount／share_count；Order.tax_amount。應先決定DDL契約，再讓metadata/create_all與migration對齊。較保守方向是模型明確宣告已部署且既有raw importer可能依賴的預設值，而非移除現有DB default；ORM目前有Python default，不表示裸SQL／bootstrap路徑一樣。這11個確認於新SQLite；PG正式庫未讀，不能說已核實正式PG drift。

## 舊 D01–D06 與本輪修復狀態

| 原項目 | 現行實作／確實證據 | 尚存界線 |
|---|---|---|
| D01期間重複分配 | `confirm_surplus`專用SystemSetting列UPSERT取得跨process DB鎖，鎖內查日期重疊至原commit；歷史與合法不重疊保留。既有 [34案含真PG證據](../outputs/safety-fix-validation/data-surplus-fix.md)。 | 不是DB exclusion constraint；直接SQL或舊writer不遵守protocol仍可造重疊。不能因此把已修HTTP競態再列成開放缺陷。 |
| D02帳戶初始化 | 共用point_account UPSERT RETURNING、不提前commit；一帳戶unique仍在，來源ledger同交易。徽章定義首次初始化亦修ON CONFLICT。 | [D02/D04正式49案](../outputs/remediation-completion/data-regressions.log)含真PG；source／helper已讀，沒有新增balance快取。 |
| D03本地通知意圖 | Notification三欄unique＋Outbox.notification_id FK/unique，repository UPSERT，domain effect commit改flush；0043保留歷史canonical／NULL／delivery_version0。 | [22案含PG／migration](../outputs/remediation-completion/notification-dedupe-pg-green.log)；外部交付未知另有sender修正與[32UI獨立複驗](../outputs/remediation-completion/email-delivery-unknown-independent-review.md)。本地唯一意圖不等於實際郵件exactly once，舊報告外部缺口已由後批處理，不能引用舊「仍會fallback」作現行結論。 |
| D04開放提案 | 0042 partial UNIQUE＋target lock、關舊建新無中途commit；closed/custom可並存，舊open duplicates preflight停止且不刪投票。 | 定向49案含API/PG／migration；本輪15案亦實證新unique拒重複。 |
| D05列表查詢 | 頁內batch＋100 ORM buffer串流，完整回應及精確total保留；private搜尋仍O(N)。 | [獨立報告](../outputs/remediation-completion/root-data-independent-review.md)與26組新舊JSON／PG證據；不是資料正規化能消除加密substring CPU。 |
| D06SQLite FK | app factory每connection PRAGMA=ON，共用fixture同factory；Wish非法關聯回422。 | [133定向案](../outputs/remediation-completion/sqlite-fk-targeted-green.log)，本輪真FK拒orphan。Alembic／其他raw factory未因此全局打開；migration schema正確不等於所有raw fixture已受FK驗證。 |
| OPS04新合約guard | 0044只加nullable Order.group_contract_fingerprint，保留legacy；加入舊單恢復／在途unknown的guard由專屬owner處理。 | 本報告核對schema／migration與快照用途，不代替owner完整API／PG／browser或root第二輪整合驗收。 |

舊 `data-architecture-review.md` 固定656版本，當時「沒有PG」「未修D01–D06」「head0040」都是歷史，不能當今天現況。當前root完整backend／PG整合正在重跑，不能以這份source審查先宣布已部署或整案驗收。

## 遷移與後續順序

1. 先完成目前0042–0044整合與checkpoint，保留原失敗／成功log；本報告沒有改產品。
2. OPS01獨立0045按已核方向處理階段隔離與持久監測，不把本報告所有schema強化混進去。
3. 小型default一致性可另做一批，先對Boolean原表案例紅→綠、metadata/migration對照，避免改變現場付款業務預設。
4. 關鍵複合FK／非負CHECK／enum CHECK各自先跑**唯讀preflight**：所有FK存在但parent不一致、revision<1、counter<0、無效enum、JSON失效ID。只有彙總及受控管理員識別；不把個資／raw payload打到log。歷史異常阻擋migration或交人工核對，不自動刪除／改user／重算帳務。
5. 對每項新constraint保留正常歷史、折扣、NULL receipt／未發生時間、closed提案控制；真PG與SQLite各驗錯配拒絕、合法流程、並行及up/down/up。既有有資料後拒降版的0043／0044規則保留。
6. 需要效能索引時，再用合成／授權資料的真PG EXPLAIN驗證實際predicate與排序。單欄索引多不代表有效，也不盲目每個nullable FK再加索引；本輪不宣稱已做正式PG query plan／容量／長期RSS量測。

重跑owned盤點／觀測：以安全app-venv執行本目錄三個 `database_normalization_*_probe.py`。三者均明確 `_env_file=None`，只離線metadata或自行建立／清理SQLite；沒有使用正式DB URL、容器或外部供應商。
