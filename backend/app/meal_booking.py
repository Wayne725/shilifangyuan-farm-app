from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .models import MealEvent


TAIPEI = ZoneInfo("Asia/Taipei")
MEAL_PICKUP_TIMES = {
    "lunch": (time(11), time(13)),
    "dinner": (time(16, 30), time(18)),
}


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def meal_service_date(event: MealEvent):
    return event.service_date or _aware(event.pickup_starts_at).astimezone(TAIPEI).date()


def meal_period(event: MealEvent) -> str:
    return event.meal_period or (
        "dinner" if _aware(event.pickup_starts_at).astimezone(TAIPEI).hour >= 15 else "lunch"
    )


def meal_booking_window(event: MealEvent) -> tuple[datetime, datetime]:
    service_date = meal_service_date(event)
    start = datetime.combine(service_date - timedelta(days=1), time.min, TAIPEI)
    end = datetime.combine(service_date, time.min, TAIPEI)
    return (
        max(start.astimezone(timezone.utc), _aware(event.ordering_starts_at)),
        min(end.astimezone(timezone.utc), _aware(event.ordering_ends_at)),
    )


def meal_pickup_slots(event: MealEvent) -> list[datetime]:
    service_date = meal_service_date(event)
    first, last = MEAL_PICKUP_TIMES[meal_period(event)]
    selected = datetime.combine(service_date, first, TAIPEI)
    last_slot = datetime.combine(service_date, last, TAIPEI)
    event_start = _aware(event.pickup_starts_at)
    event_end = _aware(event.pickup_slot_ends_at or event.pickup_ends_at)
    end_inclusive = event.pickup_slot_ends_at is not None
    slots = []
    while selected <= last_slot:
        within_end = selected <= event_end if end_inclusive else selected < event_end
        if event_start <= selected < _aware(event.pickup_ends_at) and within_end:
            slots.append(selected.astimezone(timezone.utc))
        selected += timedelta(minutes=30)
    return slots


def validate_meal_booking_day(event: MealEvent, now: datetime) -> None:
    current = _aware(now)
    if meal_service_date(event) != current.astimezone(TAIPEI).date() + timedelta(days=1):
        raise ValueError("便當只能提前一天預訂，請選擇明天的取餐日期")
    start, end = meal_booking_window(event)
    if not start <= current < end:
        raise ValueError("目前不在便當預訂時間內，最晚需於取餐前一天完成預訂")
