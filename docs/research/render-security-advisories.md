# Render 近期資安公告查證

日期：2026-10-23

## 結論與範圍

轉貼的修補版本與公開公告相符，措施可行，但只升級應用程式的 `certifi` 不一定涵蓋所有 HTTPS 用戶端。必須確認實際採用的 CA store、建置工具與部署後行為。本筆記是官方來源查證，未登入 Render、未檢查即時正式環境，無法據此驗證原信寄件身分、排除入侵或宣告線上修補完成。

## Render 的更新責任

- 2026-10-22 發布 [eMudhra 公告](https://render.com/docs/advisory-emudhra) 與 [PHP2Shell 公告](https://render.com/docs/advisory-php2shell)。前者明言 Render 平台未因該漏洞遭入侵。
- Native runtime 的系統 CA store 會在下次 **build** 更新；Python 另需 `certifi>=2026.10.19`，並確認依賴／lockfile 解析結果。Node 即使只用於 build 也要更新；靜態網站若 build 使用 Node 或 HTTPS 亦適用。[Render eMudhra 公告](https://render.com/docs/advisory-emudhra)
- Docker 需自行更新映像內 `ca-certificates`、runtime 及自帶 CA 的套件；registry image 要推送修正版並確認 Render 引用正確 tag／digest。更新後執行 `Manual Deploy → Deploy latest commit`；預建映像則為 `Deploy latest reference`。[Render eMudhra 公告](https://render.com/docs/advisory-emudhra)
- Render 會快取 Docker 中間層與公開映像，mutable tag 可能取到較舊映像；用明確版本／digest 並查驗結果。[Render Docker 文件](https://render.com/docs/docker) Python 安全公告另建議停用 layer cache 重建容器。[Python 安全公告](https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576)

## Python：三個不同的信任來源

| 來源 | 必須處理的事項 |
| --- | --- |
| 應用程式 `certifi` | 升至 `2026.10.19` 以上；版本確實已發布。[PyPI](https://pypi.org/project/certifi/2026.10.19/) |
| 標準函式庫／系統 CA | `ssl.create_default_context()` 未指定 CA 時載入預設 CA；`urllib.request` 可傳入明確的 `SSLContext`。因此安裝新版 `certifi` 不會自動改寫每個既有標準函式庫呼叫。[ssl](https://docs.python.org/3.12/library/ssl.html#ssl.create_default_context)、[urllib.request](https://docs.python.org/3.12/library/urllib.request.html#urllib.request.urlopen) |
| pip 自帶的 `certifi` | 更新應用程式 `certifi` 不等於更新 pip 內嵌副本；`pip 26.2.2` 才將該副本升至 `2026.10.19`。[pip changelog](https://pip.pypa.io/en/stable/news/#v26-2-2) |

Python 安全公告要求同時更新 `pip>=26.2.2`；若使用 uv，則為 `uv>=0.13.4`。公告提供帶 SHA256 的 pip wheel Direct URL 作為安全升級起點。[Python 安全公告](https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576)

pip 在 Python 3.10+ 的預設 truststore 模式同時信任系統 CA 與內嵌 `certifi`。單獨指定 `--cert`／`REQUESTS_CA_BUNDLE` 不能當作已排除舊信任來源的證據；`--use-deprecated=legacy-certs` 可退出系統 CA 模式，但仍須確保所用 bundle 已修補。[pip HTTPS 文件](https://pip.pypa.io/en/stable/topics/https-certificates/)、[Python 安全團隊說明](https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576/3)

若要讓某個標準函式庫呼叫明確採用已更新的 `certifi`，可使用以下模式；這不等於修補整台主機的系統 CA：

```python
import ssl
import certifi
from urllib.request import urlopen

tls_context = ssl.create_default_context(cafile=certifi.where())
tls_context.set_alpn_protocols(["http/1.1"])
with urlopen(url, context=tls_context, timeout=10) as response:
    response_body = response.read()
```

此模式依 Python 的 `cafile` 與 `context` 語義推導；仍保留憑證及主機名稱驗證。[Python ssl 文件](https://docs.python.org/3.12/library/ssl.html#ssl.create_default_context)、[urllib.request 文件](https://docs.python.org/3.12/library/urllib.request.html#urllib.request.urlopen)

## 受影響憑證與驗收

問題涉及八張中繼 CA 私鑰；可通往四個根：`emSign Root TLS CA - G3`、`emSign Root CS CA - G3`、`AAA Certificate Services`、`emSign ECC Root CA - G3`。最後一張 SHA256 為 `86A1ECBA089C4A8D3BBE2734C612BA341D813E043CF9E8A862CD5C57A36BBE6B`。[Mozilla 官方討論](https://groups.google.com/a/mozilla.org/g/dev-security-policy/c/0R3XLJL6FuE)、[事件根憑證清單](https://emudhra.we-have-a-problem.com/roots.json)

`certifi 2026.10.18` 先移除 emSign 根；`2026.10.19` 恢復不受影響的根以維持相容性，所以「bundle 完全沒有 emSign 字串」不是合理驗收條件。[certifi 公告](https://github.com/certifi/python-certifi/security/advisories/GHSA-6q48-6c7w-9gw2)、[Python 安全公告](https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576)

部署後從 **實際執行中的服務、使用正式 HTTP client** 測試以下兩個 Render 公告指定端點，兩者都必須因憑證驗證失敗而拒絕連線。一般 DNS、timeout 錯誤不能算通過；任何 HTTP status 都代表握手已成功。只測本機或服務 shell 的 curl，可能採用不同 CA store。[Render eMudhra 公告](https://render.com/docs/advisory-emudhra)

- `https://dv-g3b-emudhra.we-have-a-problem.com/`
- `https://dv-g3b-aaa-emudhra.we-have-a-problem.com/`

## Node.js 與 PHP 核對

| Runtime 分支 | 最低修補版本 |
| --- | --- |
| Node.js 22／24／26 | `22.23.4`／`24.22.1`／`26.12.1` |
| PHP 8.2／8.3／8.4／8.5 | `8.2.35`／`8.3.36`／`8.4.27`／`8.5.12` |

Node.js 於 2026-10-21 修補 eMudhra CA 與多項獨立漏洞，包括特殊 IP 主機名稱的 TLS 驗證繞過，以及 Brotli decoder 越界寫入 RCE。各漏洞受影響分支不同，不能據此斷言每個 Node 網站都可直接被遠端執行程式碼。[Node.js 官方公告](https://nodejs.org/en/blog/vulnerability/october-2026-security-releases)

PHP2Shell 對應 `CVE-2026-109618`、`CVE-2026-109619`；PHP 官方將所有處理 web request 的 PHP 安裝列入風險範圍，mod_php 風險最高，暫時設定無法取代更新。發行版可能回補修補程式而保留較舊版本字串，需對照廠商公告；只跑 Python 而不含 PHP 的服務不需額外安裝 PHP。[PHP 官方公告](https://www.php.net/archive/2026.php#2026-10-21-5)、[Render PHP2Shell 公告](https://render.com/docs/advisory-php2shell)

## 歷史環境記錄的使用界線

專案既有 2026-10-22 記錄提及正式環境應用程式 `certifi` 已為 `2026.10.19`，但系統 SSL 與 pip 內嵌 bundle 仍保留舊信任；這些是歷史觀察，未在本研究工作中重新連線驗證，不能作為 2026-10-23 即時狀態。上述官方行為解釋了為何單看 `pip show certifi` 不足以判定完成修補。

## 修補實作參考

查證日期：2026-10-23。以下為版本及建置相容性核對，未操作 Render 帳號或修改部署設定。

### Node.js 22.23.4 與現有前端相容

| 元件 | 該版本宣告的 Node 條件 | Node 22.23.4 |
| --- | --- | --- |
| pnpm 11.9.0 | `>=22.13` | 符合 |
| Vite 8.0.16 | `^20.19.0 || >=22.12.0` | 符合 |
| Node 22.23.4 內附 Corepack 0.36.0 | `^22.22.2 || ^24.15.0 || >=26.0.0` | 符合 |

以上直接核對官方 tag 的 manifest：[pnpm 11.9.0](https://raw.githubusercontent.com/pnpm/pnpm/v11.9.0/pnpm11/pnpm/package.json)、[Vite 8.0.16](https://raw.githubusercontent.com/vitejs/vite/v8.0.16/packages/vite/package.json)、[Node 22.23.4 的 Corepack](https://raw.githubusercontent.com/nodejs/node/v22.23.4/deps/corepack/package.json)。這表示 runtime 版本條件相容；專案仍須實際跑既有 typecheck、test 與 build。

Corepack 官方說明 Node 14.19 至 24 系列內附 Corepack，`corepack enable` 建立 package-manager shims，並依專案 `packageManager` 決定 pnpm 版本。因此採 Node 22.23.4 時可保留現有 `corepack enable` 與 `pnpm@11.9.0`；本次未找到必須額外升級 Corepack 的官方要求，無須以 `corepack@latest` 擴大變動。應在 build log 核對 `node --version`、`corepack --version`、`pnpm --version`。[Corepack 官方文件](https://github.com/nodejs/corepack#how-to-install)

### Render 的 Node 選版優先序

由高至低為：`NODE_VERSION` 環境變數 → repository 根目錄 `.node-version` → 根目錄 `.nvmrc` → `package.json` 的 `engines.node`。Dashboard 既有 `NODE_VERSION` 會覆蓋 repository 檔案；不能只加 `.node-version` 就推定正式服務已改版。若使用版本範圍，Render 要求包含上界，避免未來自動跨 major。Render 預設依服務建立時間決定，舊服務不會因新服務預設已升級就自動變成修補版。[Render Node 選版文件](https://render.com/docs/node-version)

### Python 3.12.15 的角色

官方來源列表目前最新的 3.12 系列為 `3.12.15`，發布於 2026-09-30，是仍受安全維護的 3.12 分支安全版本，涵蓋 SSL、urllib、tarfile 等修補。保留 3.12 系列升級 patch 有助補齊既有 runtime 漏洞；它早於本次 eMudhra 事件，不能取代系統 CA、應用程式 certifi 與 pip 的獨立更新。此任務不需要為了 CA 修補強制跨升 3.13／3.14／3.15。[Python source releases](https://www.python.org/downloads/source/)、[Python 3.12.15 發行說明](https://www.python.org/downloads/release/python-31215/)

### pip 26.2.2 的官方 bootstrap 檔案

- Wheel：`pip-26.2.2-py3-none-any.whl`
- 直接下載：[files.pythonhosted.org wheel](https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/pip-26.2.2-py3-none-any.whl)
- SHA256：`fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70`

PyPI 檔案頁與 Python 安全公告的 URL／SHA256 一致；pip 26.2.2 要求 Python 3.10+，因此與 3.12 相容。以下 Direct URL 的 hash 驗證可避免只靠舊 CA 信任來源判定下載內容：[PyPI 檔案資料](https://pypi.org/project/pip/26.2.2/#files)、[Python 安全公告](https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576)

```sh
python -m pip --disable-pip-version-check install --no-deps \
  'pip @ https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/pip-26.2.2-py3-none-any.whl#sha256=fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70'
```

更新 pip 後，仍需確認其 HTTPS 驗證採用的系統 CA 已更新；pip 的預設模式同時使用系統 CA。若系統 CA 尚未更新，不能僅因 pip 版本已達標就宣告所有信任路徑已修補。[pip HTTPS 文件](https://pip.pypa.io/en/stable/topics/https-certificates/)

### 本機驗證用的 Node 官方封裝

Node 22.23.4 官方發布頁的 SHASUMS 列出以下 Apple Silicon 封裝，可下載至暫存目錄並先驗證 SHA256，再以其完整路徑測試專案，無須替換系統 Node。[Node 22.23.4 官方發布頁](https://nodejs.org/en/blog/release/v22.23.4)

| 封裝 | SHA256 |
| --- | --- |
| [node-v22.23.4-darwin-arm64.tar.gz](https://nodejs.org/dist/v22.23.4/node-v22.23.4-darwin-arm64.tar.gz) | `c9e1dbc748b33ed2d14f42445191ecaf14c56259c7a9a26d0042c49ec2b66428` |
| [node-v22.23.4-darwin-arm64.tar.xz](https://nodejs.org/dist/v22.23.4/node-v22.23.4-darwin-arm64.tar.xz) | `037650d0839f457747396f052073fb48333d31bf5abd3c98b74da20d89ebb1ad` |

### AAA 交叉鏈與明確 CA context

若 native 重建後的系統 CA 已阻擋受影響中繼 CA 的所有信任鏈，採系統 CA 的標準函式庫不需要額外疊加 certifi 機制；仍須以 Render 的兩個端點確認，不能只檢查 G3 root 消失。此結論依 Mozilla 列出的跨簽信任路徑與阻擋策略推導。[Mozilla 官方說明](https://groups.google.com/a/mozilla.org/g/dev-security-policy/c/0R3XLJL6FuE)

若改由程式固定 bundle，應在建立 context 時傳入 `cafile=certifi.where()`；先建立預設 context、再 `load_verify_locations()` 會累加 CA，不能排除系統原有信任。`certifi 2026.10.19` 的官方 bundle 已排除 AAA 與受影響 G3，並保留未受影響的 emSign G1／C1／C3。[Python SSLContext 語義](https://docs.python.org/3.12/library/ssl.html#ssl.create_default_context)、[certifi 官方 bundle](https://raw.githubusercontent.com/certifi/python-certifi/2026.10.19/certifi/cacert.pem)

## 十里方圓專案核對（修補前）

以下為 2026-10-23 修改前的程式與本機套件盤點；後續本機修補及驗證另列於文末。未操作交易或觸發部署。

| 檢查項目 | 專案證據 | 判斷 |
| --- | --- | --- |
| API 部署 | [`render.yaml`](../../render.yaml) 為 `runtime: python`，安裝 `backend/requirements.txt` | 使用原生 runtime；存在 Dockerfile 不代表正式服務使用 Docker |
| Python 版本 | `render.yaml` 與 `backend/.python-version` 固定 `3.12.8` | 重新建置不會自動解除此版本固定；Python 本身的更新另列處理 |
| certifi 最低版本 | [`backend/requirements.txt`](../../backend/requirements.txt) 有 HTTPX、boto3，未直接列 certifi | 增列最低安全版本可避免舊環境／未來解析低於修補門檻；不等於更新系統或 pip bundle |
| 金流／發票／郵件 | [`common.py`](../../backend/app/integrations/common.py) 的 `_urlopen()` 同時有 `urlopen()` 與 `build_opener()` 路徑，均未指定 SSL context；雷門、汎宇及 Resend 使用此共用傳輸 | 系統 CA 必須更新並驗收；只更新 certifi 不會自動切換這兩條路徑 |
| LINE | [`line_login.py`](../../backend/app/integrations/line_login.py) 使用預設 HTTPX AsyncClient | 通常使用 certifi，但仍須核對正式環境的 `SSL_CERT_FILE`／`SSL_CERT_DIR` 等有效覆寫 |
| R2 | [`r2_storage.py`](../../backend/app/integrations/r2_storage.py) 使用 boto3，未顯式設定 verify | 驗收 SDK 的實際 CA bundle，也需留意 `AWS_CA_BUNDLE` 覆寫 |
| Web | `render.yaml` 為 `runtime: static`，透過 pnpm／Vite 建置 | Node 是建置工具，仍需修補；不能把 Node RCE 直接當作靜態訪客可利用的伺服器漏洞 |
| PHP | 儲存庫與上述部署描述未見 PHP 應用 | 本專案沒有已辨識的 PHP 修補項目；未盤點帳戶內所有其他服務 |

本機 `.venv312` 的唯讀結果：Python `3.12.14`、certifi `2026.7.22`、pip `26.2.1`、HTTPX `0.28.1`；系統與 certifi SSL context 皆仍包含 `emSign ECC Root CA - G3`。本機 Node 為 `26.0.0`。這些數值不能替代正式環境版本。

另有 [2026-10-22 正式盤點](../../outputs/security-production-inventory-20261022.json)：應用程式 certifi `2026.10.19`，系統仍含 G3；pip `24.3.1` 內嵌 certifi `2024.08.30`，Web 建置 Node `22.23.3`。此後可能已有部署或平台更新，這些值僅代表當時觀察。

### 對本專案可行的處理順序

1. 在來源 requirements 明列 `certifi>=2026.10.19`；建置程序在安裝應用依賴前，依 Python 安全公告的完整性驗證方式更新至 `pip>=26.2.2`。
2. 將 Web 建置 Node 設為同一主版本的修補版，例如 `22.23.4` 或更新的 22.x；同步檢查本機與 CI 的實際執行版本。
3. 將修改提交至實際部署分支，觸發 API 與 Web 的新建置。Render 原生 runtime 的系統 CA 應隨新 build 更新；單純重啟或重新啟動舊映像不足以證明更新。
4. 從新部署的實際 urllib（含禁止 redirect 路徑）、HTTPX、R2 SDK 驗證上述兩個測試端點均遭憑證驗證拒絕；測試不攜帶任何供應商 Token、API Key 或付款內容。另以正常、唯讀或隔離測試確認合法服務仍可連線。
5. 如系統 CA／實際客戶端仍未通過，再查明有效 CA 來源，選擇明確 SSLContext 或受控 Docker 映像；目前沒有必須切換 Docker 的證據。

以上可處理這次事件中本專案適用的信任庫與 Node 風險。既有 [專案安全檢查](../../outputs/專案安全檢查.md) 的上傳先解析後認證等缺口需要另外修正，不能因憑證更新而視為解決。原信寄件真偽、是否曾遭利用、2026-10-23 正式客戶端的拒絕行為，均未在本次確認。

## 供應商端若使用 PHP，會影響什麼

2026-10-23 補充核對：目前雷門與汎宇 adapter 透過 HTTPS 傳遞資料，沒有將供應商的 PHP 程式載入本機執行。API 格式是資料契約，與供應商伺服器實作語言不同；對方使用 PHP 不會使本機 Python 自動具備 PHP2Shell 漏洞。本次未確認兩家實際使用的語言、版本或是否受影響。

以下為依程式信任邊界推導的風險情境，並非兩家已發生事件：

- 單純服務故障會使付款、查單、開票／作廢延遲；timeout 不能證明對方未完成操作，應先查單／對帳再決定是否重送。
- 若 API 主機或其資料遭控制，可能回傳格式正常但內容錯誤的交易、退款或發票狀態。平台可能因此記錄錯誤結果，對帳或履約也會受到影響。
- 供應商可接觸的訂單、Email、統編、發票明細、商戶串接帳密或共享金鑰可能受影響；具體範圍依遭控制系統及權限而定，不能推論為本平台全部資料或所有 Render 秘密皆外洩。
- 惡意回應若再遇上本平台的解析、資源限制或輸出處理缺口，可能擴大影響；必須另外找出可達的缺陷，不能只憑對方 PHP 漏洞宣稱本平台可被遠端執行程式碼。

第三方 API 亦應視為不可信輸入；OWASP 明列第三方被控制後可藉回應造成資料暴露、注入與阻斷服務等風險。[OWASP API10:2023](https://api-security.owasp.org/editions/2023/en/0xaa-unsafe-consumption-of-apis/)

程式已有 HTTPS origin 限制、金流／發票不跟隨 redirect、欄位檢查；雷門回應後另做查單，核對訂單、金額、交易編號、付款類型與商店資訊（[`payments.py`](../../backend/app/routers/payments.py)）。這些可降低部分外部偽造或資料不一致風險；若共享金鑰及查單端點也被攻擊者控制，格式、加密與同來源查單不能獨立證明實際收款。發票號碼格式正確亦不等於已完成獨立查驗。

HTTPS 能驗證通道及伺服器身分，無法保證已遭入侵的合法伺服器回傳真實內容。憑證庫修補與供應商安全修補需各自完成；供應商事故時再依影響範圍暫停自動狀態更新、由獨立清算／官方紀錄核對，並更換受影響串接憑證。

## 本機修補與驗證

日期：2026-10-23

使用者後續要求修改主要安全項目。本輪完成本機修改，尚未提交、推送、同步 Render 設定或部署。

- Node `22.23.4`：同步 `.nvmrc`、根目錄 engines（22 系列最低 `22.23.4`）、Render API／Web 的 `NODE_VERSION`；CI 改讀 `.nvmrc`。
- Python `3.12.15`：同步 backend 版本檔與 Render `PYTHON_VERSION`；CI 改讀同一版本檔。維持 3.12 分支。
- requirements 明列 `certifi>=2026.10.19`；新增 `backend/scripts/bootstrap_pip.py`，依官方附 SHA256 的 wheel 安裝 pip `26.2.2`，Render build 與兩個 Python CI 工作都先執行 bootstrap。
- 新增 `backend/app/integrations/tls.py`，直接以 certifi cafile 建立 SSLContext，不累加系統 CA。urllib 的一般／禁止轉址路徑、LINE HTTPX 與 R2 SDK 均使用此信任來源，保留憑證及主機名稱驗證。
- 新增 8 項實際本機 TLS 握手測試：系統環境／SDK 覆寫額外信任的測試 CA，應用程式仍拒絕；放入應用 bundle 的測試 CA 可成功連線。修補前 4 項拒絕測試失敗、4 項正常連線控制組通過；修補後 8 項皆通過。

驗證結果：

| 檢查 | 結果 |
| --- | --- |
| 後端既有 tests／audits | `1607 passed`，Python `3.12.14` 的隔離測試環境 |
| 新 TLS 測試 | `8 passed`；另與汎宇 API01 測試合跑共 `34 passed` |
| 前端測試 | `68 passed`，實際 Node `22.23.4` |
| 乾淨前端建置 | 暫存副本使用 Node `22.23.4`／pnpm `11.9.0`，frozen lockfile 安裝及 tsc／Vite production build 通過 |
| pip bootstrap | 全新暫存 venv 安裝成功；pip `26.2.2`、其內嵌 certifi `2026.10.19` |
| 設定 | Render／CI YAML 解析與 git diff whitespace 檢查通過 |

Node 官方下載檔已對照發布頁 SHA256。沒有替換系統 Node 或既有 `.venv`／`.venv312` 的套件。前端建置沿用鎖定的 Vite `8.2.1`；其他依賴公告與上傳資源防護不屬於這次修補完成範圍。

本機沒有 Python `3.12.15`，後端功能驗證使用 `3.12.14`；精確目標版本的 CI 與 Linux／Render 執行環境仍須驗收。系統 CA 的平台更新、正式 HTTP 客戶端雙測試端點、正常供應商 TLS 連線也尚未在部署後確認，因此不能宣告正式服務已修補。

使用者另詢問「汎宇出錯是否會當機」：目前 `issue_paid_order_invoice()` 捕捉供應商例外並記錄 invoice failed，不變更已付款狀態；外層 outbox 捕捉例外、延後重試，達 8 次後標記 failed。普通 HTTP／連線／憑證錯誤不會因此直接終止網站程序。這不涵蓋供應商被控制後的假回應或惡意資源消耗情境；先前信任邊界分析仍適用。
