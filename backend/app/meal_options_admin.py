from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import require_admin
from .database import get_session
from .domain import aware
from .meal_photos import photo_target
from .models import AdminAudit, MealOption, MealOptionGroup, User, new_id, utcnow
from .schemas import MealOptionsUpdate, MealRead

router = APIRouter(tags=["meals"])


@router.put("/v1/admin/meals/{meal_id}/options", response_model=MealRead)
async def update_options(
    meal_id: str, body: MealOptionsUpdate, admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    meal, _ = await photo_target(session, meal_id, None)
    if aware(body.expected_updated_at) != aware(meal.updated_at):
        raise HTTPException(409, "餐點已被更新，請重新載入後再編輯")
    groups = {group.id: group for group in meal.option_groups}
    requested = [group.id for group in body.option_groups if group.id is not None]
    if len(set(requested)) != len(requested) or set(requested) != set(groups):
        raise HTTPException(422, "選項群組不屬於此餐點、重複或遺漏；請保留舊群組並改為停用")
    for draft in body.option_groups:
        old_options = {option.id for option in groups[draft.id].options} if draft.id else set()
        ids = [option.id for option in draft.options if option.id is not None]
        if len(set(ids)) != len(ids) or set(ids) != old_options:
            raise HTTPException(422, "選項不屬於此群組、重複或遺漏；請保留舊選項並改為停用")
    # Free unique names first so two existing choices can exchange their names.
    for group in groups.values():
        group.name = f"editing-{new_id()}"
        for option in group.options:
            option.name = f"editing-{new_id()}"
    await session.flush()
    for position, draft in enumerate(body.option_groups):
        group = groups.get(draft.id)
        if group is None:
            group = MealOptionGroup(id=new_id(), options=[])
            meal.option_groups.append(group)
        options = {option.id: option for option in group.options}
        group.name, group.position = draft.name, position
        group.min_selections, group.max_selections = draft.min_selections, draft.max_selections
        group.is_active = draft.is_active
        for option_position, item in enumerate(draft.options):
            option = options.get(item.id)
            if option is None:
                option = MealOption(id=new_id())
                group.options.append(option)
            option.name, option.position = item.name, option_position
            option.price_delta, option.is_active = item.price_delta, item.is_active
    meal.updated_at = utcnow()
    session.add(AdminAudit(actor_id=admin.id, action="meal.options_update", aggregate_type="meal", aggregate_id=meal.id,
                           data={"group_count": len(body.option_groups)}))
    await session.commit()
    return MealRead.model_validate(meal)
