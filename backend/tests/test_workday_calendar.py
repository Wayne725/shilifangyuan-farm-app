import csv
import hashlib
import json
from datetime import date, timedelta

import pytest

from app.workday_calendar import (
    DATA_DIR, CalendarUnavailable, calendar_status, is_workday, next_workday, previous_workday,
)


def test_official_snapshots_cover_whole_years_and_match_reviewed_checksums():
    manifest = json.loads((DATA_DIR / "taiwan_workdays_sources.json").read_text())
    for entry in manifest["files"]:
        raw = (DATA_DIR / entry["file"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == entry["sha256"]
        rows = list(csv.DictReader(raw.decode().splitlines()))
        year = entry["year"]
        expected = [date(year, 1, 1) + timedelta(days=i) for i in range(entry["days"])]
        assert [date.fromisoformat(row["西元日期"]) for row in rows] == expected
        assert expected[-1] == date(year, 12, 31)
        assert {row["是否放假"] for row in rows} <= {"0", "2"}


@pytest.mark.parametrize("start, expected", [
    (date(2026, 11, 6), date(2026, 11, 9)),
    (date(2026, 11, 7), date(2026, 11, 9)),
    (date(2026, 11, 8), date(2026, 11, 9)),
    (date(2026, 9, 25), date(2026, 9, 29)),
    (date(2026, 12, 31), date(2027, 1, 4)),
])
def test_weekends_holiday_monday_and_year_rollover(start, expected):
    assert next_workday(start) == expected


def test_previous_workday_spans_lunar_new_year():
    assert previous_workday(date(2026, 2, 23)) == date(2026, 2, 13)
    assert previous_workday(date(2026, 9, 29)) == date(2026, 9, 24)
    assert not is_workday(date(2025, 2, 8))  # 官方補班六仍須人工加開。


def test_missing_year_does_not_guess_weekdays():
    with pytest.raises(CalendarUnavailable):
        is_workday(date(2028, 1, 3))
    with pytest.raises(CalendarUnavailable):
        next_workday(date(2027, 12, 31))
    status = calendar_status()
    assert status["from_date"] == "2025-01-01"
    assert status["to_date"] == "2027-12-31"
