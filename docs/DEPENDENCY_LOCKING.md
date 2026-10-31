# 依賴鎖定與安全掃描

日期：2026-10-31。

## 正常安裝

- 前端使用 Node 22.23.4、專案指定 pnpm 11.9.0，執行 `pnpm install --frozen-lockfile`；不在其他 worktree 共用的依賴目錄更新套件。
- 後端使用 Python 3.12。`backend/requirements.in` 保留直接依賴政策，`backend/requirements.txt` 為完整精確版本及 SHA256。先執行 `python scripts/bootstrap_pip.py`，再 `python -m pip install --require-hashes -r requirements.txt`。
- CI、Render 與本機沿用同一鎖檔；不重新解析浮動範圍當成發布版本。稽核工具使用獨立 venv，避免其依賴改寫應用環境。

## 更新流程

在新的隔離 venv 安裝 `pip-tools==7.6.2`。編輯 `requirements.in`，以既有鎖檔保留未更新的精確版本：

```sh
cd backend
pip-compile --generate-hashes --no-emit-index-url --no-emit-trusted-host --output-file requirements.txt requirements.in
```

審查實際版本差異，再做乾淨 hash 安裝、`pip check`、安全掃描、migration 升／降／升及完整交易回歸。Linux／Mac 的環境標記和 wheel 仍須驗證；不把只有下載 wheel 的檢查說成已在該 CPU 執行過測試。

本次 62 個後端套件只更新 PyJWT 2.15.1、dnspython 2.9.1、urllib3 2.8.0，其餘保留已測版本；1294 個 SHA256。Mac Python 3.12.14 與 Linux aarch64 Python 3.12.15 乾淨安裝／audit 通過，Linux x86_64 CPython 3.12 的 wheel hash 下載通過，x86 實際執行由 CI 補驗。

前端只更新 Vite 8.2.4、seroval 1.6.11、source-map-js 1.2.2；React 與 TanStack 保留。更新前鎖檔有 13 筆公告，更新後本輪完整 audit 為 0。公告筆數不等於已證實可利用漏洞，也不能以本輪零公告承諾不存在未知漏洞。

## CI 產物與範圍

- CI 執行完整前端 audit，以及對完整 Python lock 的 audit；不忽略公告。失敗即停止放行。
- 前端以 `pnpm sbom --sbom-format cyclonedx --lockfile-only` 產生 `frontend-lockfile-sbom`，含開發及跨平台 optional 套件；它是來源／建置清冊，不代表所有套件都送進瀏覽器。
- Python 以獨立 `pip-audit==2.10.1` 工具環境產生 `backend-lockfile-sbom`；掃描精確 lock，不讓工具另解析一組新版套件。bootstrap pip 由固定 wheel URL／SHA256 管理，不混入應用套件清冊。
- SBOM、安裝報告與 audit 是版本追蹤證據；實際部署 commit、建置 hash、環境及供應商設定仍須分別核對。

本次本機原始證據在 `outputs/remediation-completion/`，包括跨 OS 安裝、版本比對、audit、雙 SBOM、完整後端／PG／migration 及瀏覽器回歸。正式環境尚未因本文件更新。
