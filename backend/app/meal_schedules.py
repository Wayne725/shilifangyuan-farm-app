from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AdminAudit, Meal, MealEvent, MealEventOffering, MealEventStatus, MealScheduleTemplate, Order
from .meal_booking import MEAL_ORDER_CUTOFF, TAIPEI, _aware, meal_schedule_has_pickup_slots, workday_booking_window
from .workday_calendar import CalendarUnavailable, is_weekend_catering_day, is_workday


def advance_meal_schedule_version(template: MealScheduleTemplate, now: datetime) -> None:
    template.updated_at = max(now, _aware(template.updated_at) + timedelta(microseconds=1))


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
    template_ids: list[str] | None = None,
    limit: int = 100,
    report: dict | None = None,
    convert_legacy: bool = True,
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
    elif template_ids is not None:
        query = query.where(MealScheduleTemplate.id.in_(template_ids))
    templates = list(await session.scalars(
        query.order_by(MealScheduleTemplate.id).limit(limit)
        .with_for_update(skip_locked=template_id is None and template_ids is None)
    ))
    created_ids: list[str] = []
    for template in templates:
        pickup_start_time = time.fromisoformat(template.pickup_start_time)
        pickup_end_time = time.fromisoformat(template.pickup_end_time)
        if not meal_schedule_has_pickup_slots(template.meal_period, pickup_start_time, pickup_end_time):
            continue
        if convert_legacy:
            await _convert_unordered_legacy_events(session, template, today, report)
        meal_ids = {item["meal_id"] for item in template.offerings}
        prices = dict((await session.execute(
            select(Meal.id, Meal.price).where(Meal.id.in_(meal_ids), Meal.is_active.is_(True))
        )).all())
        active_offerings = [
            {**item, "price": prices[item["meal_id"]]} if template.fixed_booking_window else item
            for item in template.offerings if item["meal_id"] in prices
        ]
        if not active_offerings:
            continue
        existing_dates = set(await session.scalars(
            select(MealEvent.service_date).where(
                MealEvent.schedule_template_id == template.id,
                MealEvent.service_date >= today,
            )
        ))
        weekend_enabled = {5: template.saturday_business_only, 6: template.sunday_business_only}
        eligible_in_horizon = False
        for offset in range(1, 367):
            if offset > template.advance_days and (eligible_in_horizon or template.fixed_booking_window):
                break
            service_date = today + timedelta(days=offset)
            try:
                business_only = weekend_enabled.get(service_date.weekday(), False) and is_weekend_catering_day(service_date)
                workday = is_workday(service_date)
            except CalendarUnavailable:
                report["calendar_unavailable"] += 1
                break
            if not business_only and (not workday or service_date.weekday() not in template.weekdays):
                continue
            pickup_start = datetime.combine(service_date, pickup_start_time, TAIPEI)
            pickup_end = datetime.combine(service_date, pickup_end_time, TAIPEI)
            if pickup_end < pickup_start:
                pickup_end += timedelta(days=1)
            try:
                if business_only:
                    friday = service_date - timedelta(days=service_date.weekday() - 4)
                    ordering_start = datetime.combine(friday, time.min, TAIPEI)
                    cutoff = datetime.combine(friday, MEAL_ORDER_CUTOFF, TAIPEI)
                else:
                    configured_cutoff = None if template.fixed_booking_window else datetime.combine(
                        service_date - timedelta(days=template.cutoff_days_before),
                        time.fromisoformat(template.cutoff_time), TAIPEI,
                    )
                    ordering_start, cutoff = workday_booking_window(service_date, configured_cutoff)
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
                booking_policy="special" if business_only else "workday",
                business_only=business_only,
                ordering_starts_at=ordering_start.astimezone(timezone.utc),
                ordering_ends_at=cutoff.astimezone(timezone.utc),
                pickup_starts_at=pickup_start.astimezone(timezone.utc),
                pickup_slot_ends_at=pickup_end.astimezone(timezone.utc),
                pickup_ends_at=(pickup_end + timedelta(minutes=30)).astimezone(timezone.utc),
                status=MealEventStatus.PUBLISHED if template.auto_publish else MealEventStatus.DRAFT,
                created_by_id=template.created_by_id,
                pickup_location_ids=list(template.pickup_location_ids or []),
                offerings=[MealEventOffering(**item) for item in active_offerings],
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


async def apply_schedule_pickup_locations(
    session: AsyncSession, template: MealScheduleTemplate, actor_id: str, now: datetime,
) -> int:
    target = list(template.pickup_location_ids or [])
    events = list(await session.scalars(select(MealEvent).where(
        MealEvent.schedule_template_id == template.id,
        MealEvent.pickup_starts_at > now,
        MealEvent.archived_at.is_(None),
        MealEvent.status.not_in([MealEventStatus.CANCELLED, MealEventStatus.COMPLETED]),
    ).order_by(MealEvent.id).with_for_update()))
    changed = 0
    for event in events:
        before = list(event.pickup_location_ids or [])
        if before == target:
            continue
        event.pickup_location_ids = target
        session.add(AdminAudit(
            actor_id=actor_id, action="meal_event.pickup_locations_update", aggregate_type="meal_event",
            aggregate_id=event.id, data={"schedule_template_id": template.id, "before": before, "after": target},
        ))
        changed += 1
    return changed
