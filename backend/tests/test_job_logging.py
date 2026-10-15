import os
import subprocess
import sys
from pathlib import Path


def test_job_progress_is_visible_under_uvicorn_without_enabling_http_debug_logs():
    result = subprocess.run(
        [sys.executable, "-c", """
import logging
import logging.config
from uvicorn.config import LOGGING_CONFIG

logging.config.dictConfig(LOGGING_CONFIG)
from app.jobs import logger

logger.info("reconcile_completed counts=%s", {"outbox_failed": 0})
assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)
"""],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "APP_ENV": "test", "DATABASE_URL": "sqlite+aiosqlite:///:memory:"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert "reconcile_completed counts={'outbox_failed': 0}" in result.stderr
