from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .models import MealEvent


TAIPEI = ZoneInfo("Asia/Taipei")
MEAL_ORDER_CUTOFF = time(20)
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
    if event.booking_policy in {"workday", "special"}:
        return _aware(event.ordering_starts_at), _aware(event.ordering_ends_at)
    service_date = meal_service_date(event)
    start = datetime.combine(service_date - timedelta(days=1), time.min, TAIPEI)
    end = datetime.combine(service_date - timedelta(days=1), MEAL_ORDER_CUTOFF, TAIPEI)
    return (
        max(start.astimezone(timezone.utc), _aware(event.ordering_starts_at)),
        min(end.astimezone(timezone.utc), _aware(event.ordering_ends_at)),
    )


def workday_booking_window(service_date: date, ends_at: datetime) -> tuple[datetime, datetime]:
    from .workday_calendar import is_workday, previous_workday

    if not is_workday(service_date):
        raise ValueError("此日期不是供餐工作日；如需加開，請選擇特殊日期")
    end = min(_aware(ends_at), datetime.combine(service_date - timedelta(days=1), MEAL_ORDER_CUTOFF, TAIPEI))
    start = datetime.combine(previous_workday(service_date), time.min, TAIPEI)
    if end <= start:
        local_end = end.astimezone(TAIPEI)
        opening_date = local_end.date()
        if local_end.time() == time.min or not is_workday(opening_date):
            opening_date = previous_workday(opening_date)
        start = datetime.combine(opening_date, time.min, TAIPEI)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def configured_booking_window(service_date: date, policy: str, starts_at: datetime,
                              ends_at: datetime) -> tuple[datetime, datetime]:
    start = _aware(starts_at)
    end = min(_aware(ends_at), datetime.combine(service_date - timedelta(days=1), MEAL_ORDER_CUTOFF, TAIPEI))
    if policy == "workday":
        opening, end = workday_booking_window(service_date, end)
        start = max(start, opening)
    if start >= end:
        raise ValueError("有效訂購截止必須晚於開賣時間，且最晚為取餐前一天 20:00")
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def meal_new_orders_blocked_reason(event: MealEvent) -> str | None:
    if event.booking_policy != "legacy" or not event.schedule_template_id:
        return None
    from .workday_calendar import CalendarUnavailable, is_workday

    try:
        if not is_workday(meal_service_date(event)):
            return "本場次非供餐工作日，已停止接受新預訂；既有訂單仍保留"
    except CalendarUnavailable as exc:
        return str(exc)
    return None


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
    if event.booking_policy not in {"workday", "special"} and meal_service_date(event) != current.astimezone(TAIPEI).date() + timedelta(days=1):
        raise ValueError("便當只能提前一天預訂，請選擇明天的取餐日期")
    start, end = meal_booking_window(event)
    if not start <= current < end:
        raise ValueError("目前不在便當預訂時間內，請於取餐前一天晚上 20:00 前完成預訂")
