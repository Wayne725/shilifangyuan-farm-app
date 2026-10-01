from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AdminAudit, Meal, MealEvent, MealEventOffering, MealEventStatus, MealScheduleTemplate


TAIPEI = ZoneInfo("Asia/Taipei")


async def generate_scheduled_meal_events(
    session: AsyncSession,
    now: datetime,
    *,
    template_id: str | None = None,
    limit: int = 100,
) -> list[str]:
    current = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now
    today = current.astimezone(TAIPEI).date()
    query = select(MealScheduleTemplate).where(MealScheduleTemplate.enabled.is_(True))
    if template_id is not None:
        query = query.where(MealScheduleTemplate.id == template_id)
    templates = list(await session.scalars(
        query.order_by(MealScheduleTemplate.id).limit(limit)
        .with_for_update(skip_locked=template_id is None)
    ))
    created_ids: list[str] = []
    for template in templates:
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
                MealEvent.service_date <= today + timedelta(days=template.advance_days),
            )
        ))
        for offset in range(template.advance_days + 1):
            service_date = today + timedelta(days=offset)
            if service_date.weekday() not in template.weekdays or service_date in existing_dates:
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
            if cutoff <= current:
                continue
            ordering_start = datetime.combine(service_date - timedelta(days=template.advance_days), time.min, TAIPEI)
            event = MealEvent(
                title=f"{template.title} {service_date.isoformat()}",
                location=template.location,
                schedule_template_id=template.id,
                service_date=service_date,
                meal_period=template.meal_period,
                ordering_starts_at=ordering_start.astimezone(timezone.utc),
                ordering_ends_at=cutoff.astimezone(timezone.utc),
                pickup_starts_at=pickup_start.astimezone(timezone.utc),
                pickup_ends_at=pickup_end.astimezone(timezone.utc),
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
