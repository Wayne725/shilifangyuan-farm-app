from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AdminAudit, Meal, MealEvent, MealEventOffering, MealEventStatus, MealScheduleTemplate, Order
from .meal_booking import TAIPEI, _aware, workday_booking_window
from .workday_calendar import CalendarUnavailable, is_workday


async def _convert_unordered_legacy_events(session, template, today, report):
    events = list(await session.scalars(select(MealEvent).where(
        MealEvent.schedule_template_id == template.id,
        MealEvent.service_date > today,
        MealEvent.booking_policy == "legacy",
        MealEvent.status.in_([MealEventStatus.DRAFT, MealEventStatus.PUBLISHED]),
    ).order_by(MealEvent.id).with_for_update()))
    for event in events:
        if await session.scalar(select(Order.id).where(Order.meal_event_id == event.id).limit(1)):
            report["held_for_orders"] += 1
            continue
        if await session.scalar(select(AdminAudit.id).where(
            AdminAudit.aggregate_type == "meal_event", AdminAudit.aggregate_id == event.id,
            AdminAudit.action == "meal_event.update",
        ).limit(1)):
            report["held_for_manual_edits"] += 1
            continue
        try:
            workday = is_workday(event.service_date)
            if workday:
                start, end = workday_booking_window(event.service_date, event.ordering_ends_at)
        except CalendarUnavailable:
            report["calendar_unavailable"] += 1
            continue
        before = {"booking_policy": event.booking_policy, "status": event.status.value,
                  "ordering_starts_at": _aware(event.ordering_starts_at).isoformat(),
                  "ordering_ends_at": _aware(event.ordering_ends_at).isoformat()}
        if workday:
            event.ordering_starts_at, event.ordering_ends_at = start, end
            report["converted"] += 1
        else:
            event.status = MealEventStatus.DRAFT
            report["unpublished_holidays"] += 1
        event.booking_policy = "workday"
        session.add(AdminAudit(actor_id=template.created_by_id, action="meal_event.workday_conversion",
            aggregate_type="meal_event", aggregate_id=event.id,
            data={"source": "automatic_workday_policy", "before": before,
                  "after": {"booking_policy": event.booking_policy, "status": event.status.value,
                            "ordering_starts_at": _aware(event.ordering_starts_at).isoformat(),
                            "ordering_ends_at": _aware(event.ordering_ends_at).isoformat()}}))


async def generate_scheduled_meal_events(
    session: AsyncSession,
    now: datetime,
    *,
    template_id: str | None = None,
    limit: int = 100,
    report: dict | None = None,
) -> list[str]:
    current = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now
    today = current.astimezone(TAIPEI).date()
    if report is None:
        report = {}
    report.update(converted=0, held_for_orders=0, held_for_manual_edits=0,
                  unpublished_holidays=0, calendar_unavailable=0)
    query = select(MealScheduleTemplate).where(MealScheduleTemplate.enabled.is_(True))
    if template_id is not None:
        query = query.where(MealScheduleTemplate.id == template_id)
    templates = list(await session.scalars(
        query.order_by(MealScheduleTemplate.id).limit(limit)
        .with_for_update(skip_locked=template_id is None)
    ))
    created_ids: list[str] = []
    for template in templates:
        await _convert_unordered_legacy_events(session, template, today, report)
        meal_ids = {item["meal_id"] for item in template.offerings}
        active_ids = set(await session.scalars(
            select(Meal.id).where(Meal.id.in_(meal_ids), Meal.is_active.is_(True))
        ))
        if active_ids != meal_ids:
            continue
        existing_dates = set(await session.scalars(
            select(MealEvent.service_date).where(
                MealEvent.schedule_template_id == template.id,
                MealEvent.service_date >= today,
            )
        ))
        eligible_in_horizon = False
        for offset in range(1, 367):
            if offset > template.advance_days and eligible_in_horizon:
                break
            service_date = today + timedelta(days=offset)
            try:
                workday = is_workday(service_date)
            except CalendarUnavailable:
                report["calendar_unavailable"] += 1
                break
            if not workday or service_date.weekday() not in template.weekdays:
                continue
            pickup_start = datetime.combine(service_date, time.fromisoformat(template.pickup_start_time), TAIPEI)
            pickup_end = datetime.combine(service_date, time.fromisoformat(template.pickup_end_time), TAIPEI)
            if pickup_end < pickup_start:
                pickup_end += timedelta(days=1)
            cutoff = datetime.combine(
                service_date - timedelta(days=template.cutoff_days_before),
                time.fromisoformat(template.cutoff_time),
                TAIPEI,
            )
            try:
                ordering_start, cutoff = workday_booking_window(service_date, cutoff)
            except CalendarUnavailable:
                report["calendar_unavailable"] += 1
                continue
            if cutoff <= current or cutoff <= ordering_start:
                continue
            eligible_in_horizon = True
            if service_date in existing_dates:
                continue
            event = MealEvent(
                title=f"{template.title} {service_date.isoformat()}",
                location=template.location,
                schedule_template_id=template.id,
                service_date=service_date,
                meal_period=template.meal_period,
                booking_policy="workday",
                ordering_starts_at=ordering_start.astimezone(timezone.utc),
                ordering_ends_at=cutoff.astimezone(timezone.utc),
                pickup_starts_at=pickup_start.astimezone(timezone.utc),
                pickup_slot_ends_at=pickup_end.astimezone(timezone.utc),
                pickup_ends_at=(pickup_end + timedelta(minutes=30)).astimezone(timezone.utc),
                status=MealEventStatus.PUBLISHED if template.auto_publish else MealEventStatus.DRAFT,
                created_by_id=template.created_by_id,
                offerings=[MealEventOffering(**item) for item in template.offerings],
            )
            session.add(event)
            await session.flush()
            session.add(AdminAudit(
                actor_id=template.created_by_id,
                action="meal_schedule.generate",
                aggregate_type="meal_event",
                aggregate_id=event.id,
                data={"schedule_template_id": template.id, "service_date": service_date.isoformat(), "auto_publish": template.auto_publish},
            ))
            created_ids.append(event.id)
    await session.commit()
    return created_ids
