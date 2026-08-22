from datetime import datetime, timezone

from app.routers.meals import make_meal_order_number


def test_meal_order_numbers_have_more_than_one_thousand_values_per_second() -> None:
    fixed_time = datetime(2026, 8, 22, 14, 30, 0, tzinfo=timezone.utc)
    numbers = {
        make_meal_order_number(f"{index:032x}", fixed_time)
        for index in range(1_001)
    }

    assert len(numbers) == 1_001
