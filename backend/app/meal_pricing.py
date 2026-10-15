from __future__ import annotations

from dataclasses import dataclass

from .domain import DomainError
from .models import MealEventOffering, MealOption, MealOptionGroup


@dataclass(frozen=True)
class PricedMealOption:
    group_id: str
    group_name: str
    option_id: str
    option_name: str
    price_delta: int
    position: int


@dataclass(frozen=True)
class PricedMealLine:
    base_price: int
    option_price: int
    unit_price: int
    subtotal: int
    selections: tuple[PricedMealOption, ...]


def price_meal_line(
    offering: MealEventOffering,
    quantity: int,
    selected_option_ids: list[str],
) -> PricedMealLine:
    if len(selected_option_ids) != len(set(selected_option_ids)):
        raise DomainError("同一個餐點選項不可重複")

    groups = [group for group in offering.meal.option_groups if group.is_active]
    selectable = {
        option.id: (group, option)
        for group in groups
        for option in group.options
        if option.is_active
    }
    unknown = set(selected_option_ids) - set(selectable)
    if unknown:
        raise DomainError("餐點包含無效或已停用的選項")

    selected_by_group: dict[str, list[MealOption]] = {
        group.id: [] for group in groups
    }
    for option_id in selected_option_ids:
        group, option = selectable[option_id]
        selected_by_group[group.id].append(option)

    for group in groups:
        count = len(selected_by_group[group.id])
        if count < group.min_selections or count > group.max_selections:
            raise DomainError(_selection_error(group))

    selections = tuple(
        PricedMealOption(
            group_id=group.id,
            group_name=group.name,
            option_id=option.id,
            option_name=option.name,
            price_delta=option.price_delta,
            position=group.position * 1000 + option.position,
        )
        for group in sorted(groups, key=lambda item: item.position)
        for option in sorted(
            selected_by_group[group.id],
            key=lambda item: item.position,
        )
    )
    option_price = sum(option.price_delta for option in selections)
    unit_price = offering.price + option_price
    return PricedMealLine(
        base_price=offering.price,
        option_price=option_price,
        unit_price=unit_price,
        subtotal=unit_price * quantity,
        selections=selections,
    )


def _selection_error(group: MealOptionGroup) -> str:
    if group.min_selections == group.max_selections:
        return f"{group.name}必須選擇 {group.min_selections} 項"
    if group.min_selections == 0:
        return f"{group.name}最多可選 {group.max_selections} 項"
    return (
        f"{group.name}需選擇 {group.min_selections} 至 "
        f"{group.max_selections} 項"
    )
