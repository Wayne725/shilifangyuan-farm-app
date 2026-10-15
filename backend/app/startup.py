from __future__ import annotations

import argparse
import asyncio
import logging
import time
from collections.abc import Callable
from pathlib import Path

from alembic import command
from alembic.config import Config

from .config import get_settings
from .database import SessionLocal
from .seed import seed_demo_data


logger = logging.getLogger(__name__)
BACKEND_ROOT = Path(__file__).resolve().parents[1]


def run_with_retries(
    operation: Callable[[], None],
    *,
    attempts: int,
    retry_seconds: float,
    stage: str = "migration",
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    for attempt in range(1, attempts + 1):
        try:
            operation()
            logger.info("startup_stage=%s status=complete attempt=%s", stage, attempt)
            return
        except Exception:
            logger.exception(
                "startup_stage=%s status=failed attempt=%s max_attempts=%s",
                stage,
                attempt,
                attempts,
            )
            if attempt == attempts:
                raise
            delay = min(retry_seconds * (2 ** (attempt - 1)), 30)
            logger.info(
                "startup_stage=%s status=retrying delay_seconds=%s",
                stage,
                delay,
            )
            sleep(delay)


def migrate_database() -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    command.upgrade(config, "head")


def seed_preview_database() -> None:
    async def seed() -> None:
        async with SessionLocal() as session:
            await seed_demo_data(session)

    asyncio.run(seed())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["migrate", "prepare"])
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = get_settings()
    settings.validate_runtime_secrets()
    run_with_retries(
        migrate_database,
        attempts=settings.startup_migration_attempts,
        retry_seconds=settings.startup_migration_retry_seconds,
    )
    if args.command == "prepare" and settings.environment == "preview":
        run_with_retries(
            seed_preview_database,
            attempts=settings.startup_migration_attempts,
            retry_seconds=settings.startup_migration_retry_seconds,
            stage="preview_seed",
        )


if __name__ == "__main__":
    main()
