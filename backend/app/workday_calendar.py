from __future__ import annotations

import csv
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path


DATA_DIR = Path(__file__).parent / "data"
SOURCE_URL = "https://data.gov.tw/dataset/14718"


class CalendarUnavailable(ValueError):
    pass


@lru_cache(maxsize=1)
def _calendar() -> dict[date, bool]:
    days = {}
    for path in sorted(DATA_DIR.glob("taiwan_workdays_*.csv")):
        with path.open(encoding="utf-8", newline="") as source:
            for row in csv.DictReader(source):
                day = date.fromisoformat(row["西元日期"])
                # 補班週六仍由管理員加開，避免自動供應一般便當。
                days[day] = row["是否放假"] == "0" and day.weekday() < 5
    return days


def is_workday(day: date) -> bool:
    try:
        return _calendar()[day]
    except KeyError:
        raise CalendarUnavailable("此日期尚未載入官方工作日日曆，請由管理員確認並設定特殊供餐日期") from None


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


def calendar_status() -> dict:
    days = _calendar()
    return {
        "available": bool(days),
        "from_date": min(days).isoformat() if days else None,
        "to_date": max(days).isoformat() if days else None,
        "message": f"已載入官方工作日日曆（{min(days).year}–{max(days).year}）" if days else "尚未載入官方工作日日曆",
        "source_url": SOURCE_URL,
    }
