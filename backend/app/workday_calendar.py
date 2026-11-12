from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from functools import lru_cache
from pathlib import Path


DATA_DIR = Path(__file__).parent / "data"
SOURCE_URL = "https://data.gov.tw/dataset/14718"


class CalendarUnavailable(ValueError):
    pass


@lru_cache(maxsize=1)
def _calendar() -> dict[date, tuple[bool, str]]:
    days = {}
    for path in sorted(DATA_DIR.glob("taiwan_workdays_*.csv")):
        with path.open(encoding="utf-8", newline="") as source:
            for row in csv.DictReader(source):
                day = date.fromisoformat(row["西元日期"])
                days[day] = (row["是否放假"] == "0", row.get("備註", "").strip())
    return days


def _official_day(day: date) -> tuple[bool, str]:
    try:
        return _calendar()[day]
    except KeyError:
        raise CalendarUnavailable("此日期尚未載入官方工作日日曆，請由管理員確認並設定特殊供餐日期") from None


def is_workday(day: date) -> bool:
    working, _ = _official_day(day)
    return working and day.weekday() < 5


def is_saturday_catering_day(day: date) -> bool:
    working, note = _official_day(day)
    return day.weekday() == 5 and (working or not note or note in {"星期六", "例假", "例假日", "週休二日"})


def _adjacent_workday(day: date, direction: int) -> date:
    for _ in range(366):
        day += timedelta(days=direction)
        if is_workday(day):
            return day
    raise CalendarUnavailable("找不到可用的工作日，請管理員確認供餐日曆")


def previous_workday(day: date) -> date:
    return _adjacent_workday(day, -1)


def next_workday(day: date) -> date:
    return _adjacent_workday(day, 1)


def calendar_status(today: date | None = None) -> dict:
    days = _calendar()
    today = today or datetime.now(ZoneInfo("Asia/Taipei")).date()
    last_year = max(days).year if days else None
    update_year = last_year + 1 if last_year and today >= date(last_year, 7, 1) else None
    return {
        "available": bool(days),
        "from_date": min(days).isoformat() if days else None,
        "to_date": max(days).isoformat() if days else None,
        "message": f"已載入官方工作日日曆（{min(days).year}–{max(days).year}）" if days else "尚未載入官方工作日日曆",
        "source_url": SOURCE_URL,
        "update_year": update_year,
    }
