from __future__ import annotations

from unittest.mock import Mock

import pytest

from app.startup import run_with_retries


def test_startup_migration_retries_transient_failures() -> None:
    operation = Mock(side_effect=[ConnectionError, TimeoutError, None])
    sleep = Mock()

    run_with_retries(
        operation,
        attempts=5,
        retry_seconds=2,
        sleep=sleep,
    )

    assert operation.call_count == 3
    assert [call.args[0] for call in sleep.call_args_list] == [2, 4]


def test_startup_migration_stops_after_the_configured_attempts() -> None:
    operation = Mock(side_effect=ConnectionError("database unavailable"))
    sleep = Mock()

    with pytest.raises(ConnectionError, match="database unavailable"):
        run_with_retries(
            operation,
            attempts=3,
            retry_seconds=1,
            sleep=sleep,
        )

    assert operation.call_count == 3
    assert sleep.call_count == 2
