from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path


project_root = Path(__file__).resolve().parents[1]
backend_dir = project_root / "backend"


def stop(processes: list[subprocess.Popen]) -> None:
    for process in processes:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)


def main() -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend_dir,
        check=True,
    )
    processes = [
        subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
            cwd=backend_dir,
            start_new_session=True,
        ),
        subprocess.Popen(
            ["pnpm", "--dir", "web", "dev", "--host", "127.0.0.1", "--port", "4173"],
            cwd=project_root,
            start_new_session=True,
        ),
    ]

    print("\n十里方圓展示環境已啟動：http://127.0.0.1:4173")
    print("正式社員：member@shilifangyuan.tw / member123")
    print("管理員：admin@shilifangyuan.tw / admin123")
    print("按 Control-C 關閉前後端。\n")

    try:
        exit_codes = [process.wait() for process in processes]
        if any(exit_code != 0 for exit_code in exit_codes):
            raise SystemExit(max(exit_codes))
    except KeyboardInterrupt:
        stop(processes)


if __name__ == "__main__":
    main()
