from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .audited_changes import audit_changes
from .auth import require_admin
from .database import get_session
from .meal_booking import MEAL_ORDER_CUTOFF, MEAL_PICKUP_TIMES, _aware, meal_period, meal_schedule_has_pickup_slots, meal_service_date
from .meal_schedules import (
    advance_meal_schedule_version, apply_schedule_pickup_locations, apply_schedule_weekend_access, generate_scheduled_meal_events,
    weekend_modes,
)
from .models import AdminAudit, Meal, MealEvent, MealEventOffering, MealEventStatus, MealScheduleTemplate, Order, User
from .pickup_locations import selectable_pickup_location_ids
from .schemas import UNLIMITED_MEAL_CAPACITY, MealScheduleRead, validate_pickup_location_ids


router = APIRouter(prefix="/v1/admin/meal-schedules/wizard", tags=["meals"])


class ScheduleMenuItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meal_id: str = Field(min_length=1, max_length=36)
    capacity: int = Field(default=UNLIMITED_MEAL_CAPACITY, ge=1, le=UNLIMITED_MEAL_CAPACITY, strict=True)


class ScheduleWizardEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str | None = Field(default=None, min_length=1, max_length=36)
    expected_updated_at: datetime | None = None
    title: str = Field(min_length=1, max_length=140)
    location: str = Field(min_length=1, max_length=240)
    meal_period: Literal["lunch", "dinner"]
    pickup_start_time: time | None = None
    pickup_end_time: time | None = None
    weekdays: list[int] = Field(max_length=7)
    saturday_business_only: bool = False
    sunday_business_only: bool = False
    enabled: bool = True
    auto_publish: bool = True
    offerings: list[ScheduleMenuItem] = Field(min_length=1, max_length=100)
    pickup_location_ids: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def validate_entry(self) -> ScheduleWizardEntry:
        validate_pickup_location_ids(self.pickup_location_ids)
        if (self.id is None) != (self.expected_updated_at is None):
            raise ValueError("編輯排程時須提供當前版本")
        if self.expected_updated_at is not None and self.expected_updated_at.tzinfo is None:
            raise ValueError("時間必須包含時區")
        if len(set(self.weekdays)) != len(self.weekdays) or any(day not in range(7) for day in self.weekdays):
            raise ValueError("請選擇有效的供餐日")
        if (5 in self.weekdays and self.saturday_business_only) or (6 in self.weekdays and self.sunday_business_only):
            raise ValueError("週末請選擇只接受會餐，或一般便當與會餐其中一種")
        if not (self.weekdays or self.saturday_business_only or self.sunday_business_only):
            raise ValueError("請至少選擇一個供餐日")
        if len({item.meal_id for item in self.offerings}) != len(self.offerings):
            raise ValueError("每日菜單不可重複餐點")
        default_start, default_end = MEAL_PICKUP_TIMES[self.meal_period]
        start = self.pickup_start_time if self.pickup_start_time is not None else default_start
        end = self.pickup_end_time if self.pickup_end_time is not None else default_end
        for clock in (start, end):
            if clock.tzinfo is not None or clock.second or clock.microsecond:
                raise ValueError("取餐時間請使用台灣時間，精確至分鐘")
        if start == end or not meal_schedule_has_pickup_slots(self.meal_period, start, end):
            raise ValueError("取餐時間須包含至少一個該餐別的半小時取餐時段")
        self.pickup_start_time, self.pickup_end_time = start, end
        return self


class ScheduleWizardInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schedules: list[ScheduleWizardEntry] = Field(min_length=1, max_length=2)
    scope: Literal["empty_future", "new_only"] = "empty_future"

    @model_validator(mode="after")
    def validate_pair(self) -> ScheduleWizardInput:
        if len(self.schedules) == 2:
            first, second = self.schedules
            if first.meal_period == second.meal_period or (first.id is not None and first.id == second.id):
                raise ValueError("每個餐別只能選擇一個排程")
            if first.offerings != second.offerings:
                raise ValueError("同時設定午餐和晚餐時須使用同一份菜單")
        return self


class ScheduleWizardSave(ScheduleWizardInput):
    preview_version: str = Field(pattern=r"^[a-f0-9]{64}$")


class PlannedMeal(ScheduleMenuItem):
    price: int


class ScheduleWizardEvent(BaseModel):
    id: str
    title: str
    service_date: date
    meal_period: Literal["lunch", "dinner"]


class ScheduleWizardPreview(BaseModel):
    preview_version: str
    canonical_offerings: list[PlannedMeal]
    updated_events: list[ScheduleWizardEvent]
    protected_events: list[ScheduleWizardEvent]


class ScheduleWizardSaved(BaseModel):
    schedules: list[MealScheduleRead]
    updated_event_count: int


@dataclass
class ScheduleWizardPlan:
    preview: ScheduleWizardPreview
    templates: dict[str, MealScheduleTemplate]
    changes: list[tuple[MealEvent, list[dict]]]
    menu: list[dict]


async def _wizard_templates(session: AsyncSession, body: ScheduleWizardInput, *, lock: bool) -> dict[str, MealScheduleTemplate]:
    entry_ids = [entry.id for entry in body.schedules if entry.id is not None]
    query = select(MealScheduleTemplate).where(MealScheduleTemplate.id.in_(entry_ids)).order_by(MealScheduleTemplate.id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    templates = {template.id: template for template in await session.scalars(query)}
    for entry in body.schedules:
        if entry.id is None:
            continue
        template = templates.get(entry.id)
        if template is None:
            raise HTTPException(404, "找不到每日供餐設定")
        if _aware(template.updated_at) != entry.expected_updated_at:
            raise HTTPException(409, "每日供餐設定已更新，請重新開啟編輯後再試")
        if template.meal_period != entry.meal_period:
            raise HTTPException(422, "請保留既有排程的餐別，另一餐別可另外加入")
    new_keys = [(entry.title, entry.location, entry.meal_period) for entry in body.schedules if entry.id is None]
    if new_keys:
        existing = await session.scalar(select(MealScheduleTemplate.id).where(tuple_(
            MealScheduleTemplate.title, MealScheduleTemplate.location, MealScheduleTemplate.meal_period,
        ).in_(new_keys)).limit(1))
        if existing is not None:
            raise HTTPException(409, "同名、同地區的餐別已有排程，請開啟原排程編輯")
    return templates


def _event_menu(event: MealEvent) -> list[dict]:
    return [
        {"meal_id": item.meal_id, "price": item.price, "capacity": item.capacity,
         "position": item.position, "is_active": item.is_active}
        for item in sorted(event.offerings, key=lambda item: (item.position, item.meal_id))
    ]


async def _plan_wizard(
    session: AsyncSession, body: ScheduleWizardInput, now: datetime, *, lock: bool,
) -> ScheduleWizardPlan:
    templates = await _wizard_templates(session, body, lock=lock)
    for entry in body.schedules:
        await selectable_pickup_location_ids(session, entry.pickup_location_ids)
    future = select(MealEvent).where(
        MealEvent.schedule_template_id.in_(templates),
        MealEvent.pickup_starts_at > now,
        MealEvent.archived_at.is_(None),
        MealEvent.status.not_in([MealEventStatus.CANCELLED, MealEventStatus.COMPLETED]),
    ).options(selectinload(MealEvent.offerings)).order_by(MealEvent.id)
    if lock:
        # Checkout locks the same event before validating the menu and adding an order.
        future = future.with_for_update().execution_options(populate_existing=True)
    events = list(await session.scalars(future))
    ordered = set(await session.scalars(select(Order.meal_event_id).where(
        Order.meal_event_id.in_([event.id for event in events]),
    ).distinct()))
    meal_ids = [item.meal_id for item in body.schedules[0].offerings]
    query = select(Meal.id, Meal.name, Meal.price).where(Meal.id.in_(meal_ids), Meal.is_active.is_(True)).order_by(Meal.id)
    if lock:
        query = query.with_for_update(read=True)
    current_meals = {row.id: (row.name, row.price) for row in (await session.execute(query)).all()}
    if len(current_meals) != len(meal_ids):
        raise HTTPException(422, "每日菜單包含無效或停用餐點")
    canonical = [PlannedMeal(**item.model_dump(), price=current_meals[item.meal_id][1]) for item in body.schedules[0].offerings]
    menu = [{**item.model_dump(), "position": index} for index, item in enumerate(canonical)]
    active_menu = [{**item, "is_active": True} for item in menu]
    changes = []
    event_rows = []
    protected_rows = []
    for event in sorted(events, key=lambda event: (_aware(event.pickup_starts_at), event.id)):
        row = ScheduleWizardEvent(id=event.id, title=event.title, service_date=meal_service_date(event), meal_period=meal_period(event))
        if event.id in ordered or any(item.reserved_quantity or item.paid_quantity for item in event.offerings):
            protected_rows.append(row)
            continue
        if body.scope == "empty_future":
            before = _event_menu(event)
            if before != active_menu:
                changes.append((event, before))
                event_rows.append(row)
    snapshot = {
        "request": body.model_dump(mode="json", exclude={"preview_version"}),
        "catalog": current_meals,
        "updated": [{**row.model_dump(mode="json"), "before": before, "version": _aware(event.updated_at).isoformat()}
                    for row, (event, before) in zip(event_rows, changes, strict=True)],
        "protected": [row.model_dump(mode="json") for row in protected_rows],
    }
    version = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    preview = ScheduleWizardPreview(
        preview_version=version, canonical_offerings=canonical, updated_events=event_rows, protected_events=protected_rows,
    )
    return ScheduleWizardPlan(preview, templates, changes, menu)


def _apply_event_menu(event: MealEvent, menu: list[dict], now: datetime) -> None:
    existing = {item.meal_id: item for item in event.offerings}
    next_offerings = []
    for values in menu:
        offering = existing.get(values["meal_id"])
        if offering is None:
            offering = MealEventOffering(**values)
        else:
            for name, value in values.items():
                setattr(offering, name, value)
            offering.is_active = True
        next_offerings.append(offering)
    event.offerings = next_offerings
    event.updated_at = max(now, _aware(event.updated_at) + datetime.resolution)


@router.post("/preview", response_model=ScheduleWizardPreview)
async def preview_wizard(
    body: ScheduleWizardInput, response: Response, _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> ScheduleWizardPreview:
    response.headers["Cache-Control"] = "no-store"
    return (await _plan_wizard(session, body, datetime.now(timezone.utc), lock=False)).preview


@router.post("", response_model=ScheduleWizardSaved)
async def save_wizard(
    body: ScheduleWizardSave, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
) -> ScheduleWizardSaved:
    if session.get_bind().dialect.name == "postgresql":
        # Serialize wizard creation so a repeated submission cannot add the same schedule twice.
        await session.execute(text("SELECT pg_advisory_xact_lock(873402, 1)"))
    now = datetime.now(timezone.utc)
    plan = await _plan_wizard(session, body, now, lock=True)
    if body.preview_version != plan.preview.preview_version:
        raise HTTPException(409, "餐點、場次或訂單已變動，請重新確認後再儲存")

    templates = []
    for entry in body.schedules:
        values = entry.model_dump(mode="json", exclude={"id", "expected_updated_at", "offerings"})
        values.update(
            weekdays=sorted(entry.weekdays), pickup_start_time=entry.pickup_start_time.strftime("%H:%M"),
            pickup_end_time=entry.pickup_end_time.strftime("%H:%M"), cutoff_time=MEAL_ORDER_CUTOFF.strftime("%H:%M"),
            cutoff_days_before=1, advance_days=7, fixed_booking_window=True, offerings=plan.menu,
        )
        template = plan.templates.get(entry.id)
        if template is not None and "pickup_location_ids" not in entry.model_fields_set:
            values.pop("pickup_location_ids")
        if template is None:
            template = MealScheduleTemplate(**values, created_by_id=admin.id)
            session.add(template)
            await session.flush()
            session.add(AdminAudit(actor_id=admin.id, action="meal_schedule.create", aggregate_type="meal_schedule",
                                   aggregate_id=template.id, data=values))
        else:
            before = {name: getattr(template, name) for name in values}
            previous_weekend = weekend_modes(template)
            for name, value in values.items():
                setattr(template, name, value)
            advance_meal_schedule_version(template, now)
            audit_changes(session, admin.id, "meal_schedule", template.id, "meal_schedule.update", before, values)
            await apply_schedule_pickup_locations(session, template, admin.id, now)
            await apply_schedule_weekend_access(session, template, admin.id, now, previous_weekend)
        templates.append(template)

    after = [{**item, "is_active": True} for item in plan.menu]
    for event, before in plan.changes:
        _apply_event_menu(event, plan.menu, now)
        audit_changes(session, admin.id, "meal_event", event.id, "meal_event.schedule_menu_update",
                      {"offerings": before}, {"offerings": after})
    await session.flush()
    await generate_scheduled_meal_events(session, now, template_ids=[template.id for template in templates], convert_legacy=False)
    return ScheduleWizardSaved(schedules=[MealScheduleRead.model_validate(template) for template in templates],
                               updated_event_count=len(plan.changes))
