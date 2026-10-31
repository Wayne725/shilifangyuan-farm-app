# 訂單隔離負載驗證

這個工具用合成資料、專用 PostgreSQL 18 與實際 localhost HTTP，檢查大量下單時的交易正確性、連線池排隊及回應時間。它不對正式站送出壓測，也不認證正式環境容量。

## 執行邊界

- 工具：`backend/scripts/order_load_test.py`；安全 guard：`backend/tests/test_order_load_test_guards.py`。
- 資料庫 URL 只接受明確 loopback 主機、明確連接埠，以及 `shilifangyuan_load_test` 或 `shilifangyuan_test`；拒絕 URL query／fragment。每次只建立、刪除自己產生的 `load_<uuid>` schema。
- HTTP 由工具啟動單一 uvicorn worker，綁定 `127.0.0.1` 隨機非特權埠；使用實際 TCP 與 HTTPX，不用 ASGITransport 代替負載。
- 保留 `app.main.create_app()` 的實際 router、認證、錯誤處理、安全標頭與 HTTP middleware。資料庫使用正式範本的 pool size 5、overflow 5、timeout 10 秒、pre-ping、recycle 300 秒與 `autoflush=False`，另加獨立 schema 的 search_path 及唯讀觀測 hook。
- 測試設定不讀 `.env` 或繼承外部服務秘密。停用 lifespan，避免示範資料、定期對帳與出單 worker；僅在測試啟動層將 request middleware 排程函式換成 no-op。**因此本測試只量前景交易。**
- Python socket 的非 loopback connect／DNS 均拒絕；uvicorn 明確用 `loop="asyncio"`，避免可選的 uvloop 原生網路層繞過 Python socket guard。實際 serve 入口有 guard 回歸測試。
- 付款只在本機產生合成 ECPay payload，並將測試簽章直接 POST 到本機 callback；不開付款頁、不呼叫銀行、不送測試站或正式站、不寄信、不開發票、不列印。`.test` 的 HTTPS URL 只作為 payload 字串，符合 adapter 的格式驗證。

## 本機 PostgreSQL

使用既有 Docker runtime，保持使用者的 context。以下以已啟動的 Colima socket 示範；容器只有 localhost 轉發，資料放在 tmpfs，刪除即清空。密碼是此一次性容器的合成測試值。

```sh
docker --host unix:///Users/liuweiwei/.colima/default/docker.sock run \
  --detach --rm --name farm-order-load-20261031 \
  --publish 127.0.0.1:55441:5432 --tmpfs /var/lib/postgresql:rw \
  --env POSTGRES_DB=shilifangyuan_load_test --env POSTGRES_USER=load_test \
  --env POSTGRES_PASSWORD=isolated-load-only postgres:18
```

確認容器 ready 後，在 `backend/` 執行；使用專案現有 Python 及已備妥的測試依賴，不需要負載工具新套件。

```sh
python scripts/order_load_test.py \
  --db-url postgresql+asyncpg://load_test:isolated-load-only@127.0.0.1:55441/shilifangyuan_load_test \
  --output ../outputs/legacy-load-audit
```

預設每個主要階段 100 筆，可用 `--per-stage 125` 改為 1000 筆主要流程。工具不提供外部 HTTP target 選項。任何非預期狀態、例外、空的付款回呼階段或資料守恆錯誤都使 exit code 非 0，原始 JSON 保留錯誤；不得透過重跑後只取綠結果掩蓋失敗。

## 情境與驗收

| 情境 | 流程與資料 | 必須滿足 |
|---|---|---|
| 冷／暖機 | 新程序啟動探測；10 筆一般商品 quote → create → payment-attempt | 預檢錯誤立即停止；第一筆 HTTP 延遲與程序啟動時間分開保存 |
| 一般商品 | 1／5／20／50 併發，每階段 100 筆、同一商品、100 位合成買家 | quote 200、create 201、付款保留 201；庫存＋active／consumed 保留數＝初始庫存 |
| 便當現場付款 | 同樣四階段，每階段 100 筆；明日合法場次、同一 offering | quote 200、create 201；訂單數＝保留數；reserved＋paid 不超過 capacity |
| 一般商品最後庫存 | 先合法建立 50 張待付款單，再 50 併發搶 1 份付款保留 | 1 個 201、49 個 409；50 張待付款單不等於售出 50 份 |
| 便當最後容量 | 先取 50 份合法 quote，再 50 併發搶 1 份現場付款保留 | 1 個 201、49 個 409；只新增 1 張訂單、1 份保留 |
| 相同內容重送 | 一般商品同 body 4 送；便當同 client_request_id 4 送 | 如實保留一般商品建立 4 張待付款單的既有 OPS03 缺口；便當只產生同一單及 1 份保留 |
| 合成付款回呼重送 | 25 個實際付款保留，每個同簽章 callback 送兩次 | 50 次 acknowledgement 200，但只付清 25 張、25 個 external event、25 個發票 Outbox；不執行外部工作 |

預設全部完成時共 866 張合成訂單、411 個線上付款嘗試、813 份 active／consumed 保留，其中 25 份 consumed。最後庫存刻意產生的 98 個 409 是預期結果，不能算成基礎服務錯誤，也不能把嘗試 RPS 當成成功售出 RPS。

JSON 頂層 `passed` 僅表示工具斷言通過；`known_gaps` 仍列既有缺口，`production_readiness="not_assessed"` 明確保留正式驗收界線。每個階段記錄 workflow 與 HTTP p50／p95／p99、嘗試及成功 workflow RPS、逐步驟延遲、5xx、client timeout、SQL statement 數、pool acquisition 延遲、最大同時 checkout 與 pool timeout。pool acquisition 也可能包含新連線建立，不是純等待佇列時間。SQL 計數是 SQLAlchemy cursor statements，不包含協定層交易封包或 pre-ping。

workflow 延遲從取得 client semaphore 後開始，包含該流程各 HTTP 往返，不包含工具自身排隊；這是固定併發的 closed-loop 測法，未模擬固定到達率或長時間突發流量。階段間重新建立商品／場次，但同一程序的 ORM、連線與快取持續暖化，解讀冷暖差異時須保留執行順序。

## 單號碰撞修正的回歸

原本一般商品及共同購買單號都是 `SLF`＋12 位秒時間戳＋4 位 hex，只有 16 bit 同秒隨機空間。真負載曾撞到 `ix_orders_order_number`，使一筆 create 500；這份紅證據須保留，不能歸因於測試庫存不足。

目前共用 `app/order_numbers.py` 保留原本 19 碼格式，符合物流最多 20 碼 ASCII 英數、Order 欄位 32 碼及發票關聯編號最多 30 碼。付款 trade number 由專用函式產生固定 20 碼，不直接拿訂單號截斷。舊 router 的 `make_order_number` import 仍可用。

新單插入在 SAVEPOINT 內才加入 session；只有 PostgreSQL SQLSTATE `23505` 且 constraint 為 `ix_orders_order_number`（SQLite 則精確對應該欄位 unique 訊息）才重取單號，最多三次。其他 IntegrityError 原樣拋出；連續三次碰撞回 409。這不宣稱亂數絕不重複，而是保留 DB unique 防線，讓罕見碰撞不直接成為 500。呼叫端原有商品／共同購買 row lock 與最終 commit 邊界保持不變。

SQLite 的 legacy transaction control 在此前只有 SELECT、無待寫入欄位時，不會先開始實體交易；第一個 SAVEPOINT 的 RELEASE 因而可能提前提交。helper 僅對 SQLite 檢查公開 driver connection 的 `in_transaction`，尚未開始時才送 `BEGIN`；不改全站 engine 設定，也不對 PostgreSQL 多送 BEGIN。回歸涵蓋有／無既有 DML、是否先碰撞再成功，成功插入後呼叫端 rollback 仍須撤銷整張新單與子關聯。

真 PostgreSQL、實際 TCP HTTP 回歸涵蓋一般商品及共同購買的碰撞後成功、連續碰撞 409、其他 unique 不誤攔；驗證完整訂單子項／履約關聯、無部分庫存／通知／會籍異動，並在成功 commit 前用另一 session 的 NOWAIT 確認原 row lock 仍持有。

既有 PG fixture 只接受 `shilifangyuan_test`，因此先在同容器建立該測試資料庫，再執行：

```sh
docker --host unix:///Users/liuweiwei/.colima/default/docker.sock exec \
  farm-order-load-20261031 psql -U load_test -d shilifangyuan_load_test \
  -c 'CREATE DATABASE shilifangyuan_test'

TEST_POSTGRES_URL=postgresql+asyncpg://load_test:isolated-load-only@127.0.0.1:55441/shilifangyuan_test \
  python -m pytest pgtests/test_order_number_collisions.py \
  tests/test_order_number_compatibility.py tests/test_order_load_test_guards.py -q
```

## 限制與證據

本機 macOS 原生 Python 未限制至正式 Render 的 0.5 CPU／512MB；正式服務還啟用 reconciliation，並承擔真金流、發票、信件及列印工作。因此這裡的 RPS 不能當成正式站承諾。高併發同商品／同場次的鎖競爭可觀察相對變化，尚未覆蓋共同購買大量負載、跨程序、多服務、正式網路延遲、長時間穩態、儲存耗盡、備援切換或真實第三方故障。

schema 依既有 PG fixture 慣例用 metadata 建表；不替代 Alembic 升級驗證。JSON 記錄確切 commit、tracked tree dirty 狀態、腳本 SHA256、Python／PG 版本及安全界線。正式量測應在產品修正已保存成確切 commit、無 tracked dirty 檔案後執行，並避免同機同時跑完整測試搶占 CPU。

原始與彙整證據放在 `outputs/legacy-load-audit/`；紅／綠與測試設定失敗要分開標示。結束後工具刪除自己 schema 並停止 HTTP 程序，再由操作者移除專用容器：

```sh
docker --host unix:///Users/liuweiwei/.colima/default/docker.sock stop farm-order-load-20261031
```
