from __future__ import annotations

from dataclasses import dataclass

from .models import (
    Meal,
    MealOption,
    MealOptionGroup,
    ProductCategory,
    ShippingChannel,
    ShippingTemperature,
    TaxType,
)


@dataclass(frozen=True)
class OptionSpec:
    name: str
    price_delta: int = 0


@dataclass(frozen=True)
class OptionGroupSpec:
    name: str
    min_selections: int
    max_selections: int
    options: tuple[OptionSpec, ...]


@dataclass(frozen=True)
class MealSpec:
    slug: str
    name: str
    description: str
    image_url: str
    price: int
    period: str
    option_groups: tuple[OptionGroupSpec, ...] = ()


@dataclass(frozen=True)
class ProductSpec:
    slug: str
    name: str
    description: str
    category: ProductCategory
    unit: str
    member_price: int
    nonmember_price: int
    stock_quantity: int
    tax_type: TaxType
    image_url: str
    can_ship: bool = False
    shipping_temperature: ShippingTemperature | None = None
    allowed_shipping_channels: tuple[ShippingChannel, ...] = ()


def _options(*items: str | tuple[str, int]) -> tuple[OptionSpec, ...]:
    return tuple(
        OptionSpec(item, 0)
        if isinstance(item, str)
        else OptionSpec(item[0], item[1])
        for item in items
    )


DRINK = OptionGroupSpec(
    name="飲品加購",
    min_selections=0,
    max_selections=1,
    options=_options(("茂谷汽水", 20)),
)
LUNCH_MAIN_ONE = OptionGroupSpec(
    name="主食選擇",
    min_selections=1,
    max_selections=1,
    options=_options(
        "紫米飯",
        ("壽司", 10),
        ("捲餅", 10),
        ("麵包", 10),
        ("豆皮壽司", 10),
        ("不要主食／換菜", 20),
    ),
)
LUNCH_MAIN_TWO = OptionGroupSpec(
    name="主食選擇",
    min_selections=1,
    max_selections=1,
    options=_options(
        "紫米飯",
        "壽司",
        ("捲餅", 10),
        ("麵包", 10),
        ("豆皮壽司", 10),
        ("不要主食／換菜", 20),
    ),
)
LUNCH_MAIN_THREE = OptionGroupSpec(
    name="主食選擇",
    min_selections=1,
    max_selections=1,
    options=_options(
        "紫米飯",
        ("壽司", 10),
        ("捲餅", 10),
        ("麵包", 10),
        "豆皮壽司",
        ("不要主食／換菜", 20),
    ),
)
DINNER_MAIN = OptionGroupSpec(
    name="主食選擇",
    min_selections=1,
    max_selections=1,
    options=_options(
        "紫米飯",
        "壽司",
        "捲餅",
        "麵包",
        "豆皮壽司",
        ("不要主食／換菜", 10),
    ),
)
SALAD_SAUCE = OptionGroupSpec(
    name="醬料選擇",
    min_selections=1,
    max_selections=1,
    options=_options("不要醬料", "芝麻醬", "油醋醬"),
)


OFFICIAL_MEALS = (
    MealSpec(
        slug="energy-chicken-leg",
        name="能量腿",
        description="烤雞腿、地瓜、花椰菜、秋葵、番茄與蛋羹。",
        image_url="/assets/meals/energy-chicken-leg.webp",
        price=120,
        period="lunch",
        option_groups=(LUNCH_MAIN_TWO, DRINK),
    ),
    MealSpec(
        slug="aromatic-pork",
        name="盈香豚",
        description="低脂豬肉、水煮蛋、馬鈴薯、花椰菜、番茄與時蔬。",
        image_url="/assets/meals/aromatic-pork.webp",
        price=120,
        period="lunch",
        option_groups=(LUNCH_MAIN_ONE, DRINK),
    ),
    MealSpec(
        slug="comeback-beef",
        name="逆襲牛",
        description="煎牛肉、地瓜、馬鈴薯、花椰菜、秋葵、水煮蛋與番茄。",
        image_url="/assets/meals/comeback-beef.webp",
        price=120,
        period="lunch",
        option_groups=(LUNCH_MAIN_ONE, DRINK),
    ),
    MealSpec(
        slug="full-power-chicken",
        name="滿血雞",
        description="雞胸肉、水煮蛋、地瓜、花椰菜、番茄與紅蘿蔔。",
        image_url="/assets/meals/full-power-chicken.webp",
        price=120,
        period="lunch",
        option_groups=(LUNCH_MAIN_ONE, DRINK),
    ),
    MealSpec(
        slug="memory-shrimp",
        name="記憶蝦",
        description="鮮蝦、水煮蛋、馬鈴薯、紅蘿蔔、花椰菜與番茄。",
        image_url="/assets/meals/memory-shrimp.webp",
        price=120,
        period="lunch",
        option_groups=(LUNCH_MAIN_ONE, DRINK),
    ),
    MealSpec(
        slug="battle-chicken",
        name="戰鬥雞",
        description="烤雞腿、捲餅、地瓜、花椰菜、秋葵、番茄與水煮蛋。",
        image_url="/assets/meals/battle-chicken.webp",
        price=120,
        period="lunch",
        option_groups=(
            OptionGroupSpec(
                name="客製化",
                min_selections=0,
                max_selections=1,
                options=_options(("水煮蛋換蒸蛋", 10)),
            ),
            DRINK,
        ),
    ),
    MealSpec(
        slug="clear-mind-salad-small",
        name="清醒蔬（小份）",
        description="雞絲、生菜、番茄、蔓越莓乾與水煮蛋。",
        image_url="/assets/meals/clear-mind-salad-small.webp",
        price=85,
        period="lunch",
        option_groups=(
            OptionGroupSpec(
                name="客製化",
                min_selections=0,
                max_selections=1,
                options=_options("不要雞絲／換菜"),
            ),
            SALAD_SAUCE,
            DRINK,
        ),
    ),
    MealSpec(
        slug="vegetable-box",
        name="豐蔬盒（蛋奶素）",
        description="豆皮壽司、馬鈴薯沙拉、小番茄、玉米與多樣時蔬。",
        image_url="/assets/meals/vegetable-box.webp",
        price=120,
        period="lunch",
        option_groups=(LUNCH_MAIN_THREE, DRINK),
    ),
    MealSpec(
        slug="lemon-salmon",
        name="鮭檸鮮",
        description="烤鮭魚佐檸檬、玉米筍、花椰菜、馬鈴薯沙拉與水煮蛋。",
        image_url="/assets/meals/lemon-salmon.webp",
        price=140,
        period="lunch",
        option_groups=(LUNCH_MAIN_TWO, DRINK),
    ),
    MealSpec(
        slug="multigrain-salad-box",
        name="五穀沙拉盒",
        description="五穀三明治搭配火腿、起司、生菜、水煮蛋與堅果。",
        image_url="/assets/meals/multigrain-salad-box.webp",
        price=120,
        period="lunch",
        option_groups=(DRINK,),
    ),
    MealSpec(
        slug="clear-mind-salad-large",
        name="清醒蔬（大份）",
        description="雞絲、生菜、小番茄、蔓越莓乾、水煮蛋與馬鈴薯。",
        image_url="/assets/meals/clear-mind-salad-large.webp",
        price=130,
        period="lunch",
        option_groups=(
            OptionGroupSpec(
                name="客製化",
                min_selections=0,
                max_selections=1,
                options=_options("不要雞絲／換菜"),
            ),
            SALAD_SAUCE,
            DRINK,
        ),
    ),
    MealSpec(
        slug="meeting-bread-box",
        name="會議麵包餐盒",
        description="任選三個麵包與一個蛋糕的會議餐盒。",
        image_url="/assets/meals/meeting-bread-box.webp",
        price=120,
        period="lunch",
        option_groups=(
            OptionGroupSpec(
                name="麵包口味",
                min_selections=3,
                max_selections=3,
                options=_options(
                    "奶油",
                    "紅豆",
                    "花生",
                    "芋頭",
                    "菠蘿奶酥",
                    "熱狗卷",
                    "玉米培根",
                    "蔥花",
                    "肉鬆沙拉",
                    "肉鬆卷",
                    "起司培根",
                ),
            ),
            OptionGroupSpec(
                name="蛋糕口味",
                min_selections=1,
                max_selections=1,
                options=_options(
                    "蜂蜜蛋糕",
                    "起酥蛋糕",
                    "巧克力蛋糕",
                    "瑞士卷",
                ),
            ),
            DRINK,
        ),
    ),
    MealSpec(
        slug="lemon-salmon-dinner",
        name="檸香鮭魚",
        description="低溫烤製挪威鮭魚，以檸檬提鮮。",
        image_url="/assets/meals/lemon-salmon-dinner.webp",
        price=179,
        period="dinner",
        option_groups=(DINNER_MAIN, DRINK),
    ),
    MealSpec(
        slug="sous-vide-chicken-breast",
        name="嫩煮雞胸",
        description="低溫烹調雞胸，保留柔嫩多汁口感。",
        image_url="/assets/meals/sous-vide-chicken-breast.webp",
        price=149,
        period="dinner",
        option_groups=(DINNER_MAIN, DRINK),
    ),
    MealSpec(
        slug="warm-pork",
        name="溫香豚肉",
        description="精選豚肉搭配季節時蔬。",
        image_url="/assets/meals/warm-pork.webp",
        price=149,
        period="dinner",
        option_groups=(DINNER_MAIN, DRINK),
    ),
    MealSpec(
        slug="grilled-chicken-leg",
        name="香烤雞腿",
        description="炙烤雞腿搭配胡椒調味與季節時蔬。",
        image_url="/assets/meals/grilled-chicken-leg.webp",
        price=149,
        period="dinner",
        option_groups=(DINNER_MAIN, DRINK),
    ),
    MealSpec(
        slug="steamed-shrimp",
        name="清蒸鮮蝦",
        description="去殼白蝦原味蒸煮，保留自然鮮甜。",
        image_url="/assets/meals/steamed-shrimp.webp",
        price=149,
        period="dinner",
        option_groups=(DINNER_MAIN, DRINK),
    ),
    MealSpec(
        slug="beef-dinner",
        name="醇香牛肉",
        description="精選牛肉片香煎入味，搭配季節時蔬。",
        image_url="/assets/meals/beef-dinner.webp",
        price=149,
        period="dinner",
        option_groups=(DINNER_MAIN, DRINK),
    ),
    MealSpec(
        slug="chicken-salad",
        name="雞肉沙拉",
        description="雞肉搭配水耕生菜與季節時蔬。",
        image_url="/assets/meals/chicken-salad.webp",
        price=139,
        period="dinner",
        option_groups=(DRINK,),
    ),
    MealSpec(
        slug="vegetarian-dinner",
        name="素食主義",
        description="蛋奶素餐盒，搭配季節時蔬與自選主食。",
        image_url="/assets/meals/vegetarian-dinner.webp",
        price=139,
        period="dinner",
        option_groups=(DINNER_MAIN, DRINK),
    ),
    MealSpec(
        slug="sandwich-salad",
        name="三明治沙拉",
        description="火腿蛋三明治、生菜沙拉、水煮蛋與獨立醬包。",
        image_url="/assets/meals/sandwich-salad.webp",
        price=139,
        period="dinner",
        option_groups=(DRINK,),
    ),
)


ALL_AMBIENT_CHANNELS = (
    ShippingChannel.HOME_DELIVERY,
    ShippingChannel.SEVEN_ELEVEN,
    ShippingChannel.FAMILY_MART,
    ShippingChannel.HILIFE,
)


OFFICIAL_PRODUCTS = (
    ProductSpec(
        slug="cold-brew-lemon-tea",
        name="經典檸檬茶 冷泡瓶",
        description="紅茶、無籽檸檬與甜菊葉調製，350ml／瓶。",
        category=ProductCategory.DRINKS,
        unit="瓶",
        member_price=60,
        nonmember_price=60,
        stock_quantity=30,
        tax_type=TaxType.TAXABLE,
        image_url="/assets/products/cold-brew-lemon-tea.webp",
    ),
    ProductSpec(
        slug="hydroponic-lettuce-bouquet-300g",
        name="水耕生菜花束 300g",
        description="無毒潔淨栽培的水耕生菜花束。",
        category=ProductCategory.SEASONAL_PRODUCE,
        unit="束",
        member_price=250,
        nonmember_price=250,
        stock_quantity=20,
        tax_type=TaxType.TAX_EXEMPT,
        image_url="/assets/products/hydroponic-lettuce-bouquet.webp",
    ),
    ProductSpec(
        slug="hydroponic-lettuce-bouquet-500g",
        name="水耕生菜花束 500g",
        description="無毒潔淨栽培的水耕生菜花束。",
        category=ProductCategory.SEASONAL_PRODUCE,
        unit="束",
        member_price=400,
        nonmember_price=400,
        stock_quantity=20,
        tax_type=TaxType.TAX_EXEMPT,
        image_url="/assets/products/hydroponic-lettuce-bouquet.webp",
    ),
    ProductSpec(
        slug="golden-fruit-grade-a",
        name="黃金果 A級 一盒四顆",
        description="新竹地區滿六盒免配送費。",
        category=ProductCategory.SEASONAL_PRODUCE,
        unit="盒",
        member_price=220,
        nonmember_price=220,
        stock_quantity=24,
        tax_type=TaxType.TAX_EXEMPT,
        image_url="/assets/products/golden-fruit-grade-a.webp",
        can_ship=True,
        shipping_temperature=ShippingTemperature.AMBIENT,
        allowed_shipping_channels=(ShippingChannel.HOME_DELIVERY,),
    ),
    ProductSpec(
        slug="golden-fruit-grade-b",
        name="黃金果 B級 一盒五顆",
        description="新竹地區滿六盒免配送費。",
        category=ProductCategory.SEASONAL_PRODUCE,
        unit="盒",
        member_price=220,
        nonmember_price=220,
        stock_quantity=24,
        tax_type=TaxType.TAX_EXEMPT,
        image_url="/assets/products/golden-fruit-grade-b.webp",
        can_ship=True,
        shipping_temperature=ShippingTemperature.AMBIENT,
        allowed_shipping_channels=(ShippingChannel.HOME_DELIVERY,),
    ),
    ProductSpec(
        slug="shilifangyuan-canvas-bag",
        name="十里方圓帆布袋",
        description="十里方圓合作社帆布袋。",
        category=ProductCategory.DAILY_GOODS,
        unit="個",
        member_price=60,
        nonmember_price=60,
        stock_quantity=40,
        tax_type=TaxType.TAXABLE,
        image_url="/assets/products/shilifangyuan-canvas-bag.webp",
        can_ship=True,
        shipping_temperature=ShippingTemperature.AMBIENT,
        allowed_shipping_channels=ALL_AMBIENT_CHANNELS,
    ),
    ProductSpec(
        slug="pasture-colored-eggs",
        name="放牧彩色土雞蛋",
        description="自然散養雞隻所產彩色雞蛋，依當日產量供應。",
        category=ProductCategory.EGGS,
        unit="盒",
        member_price=180,
        nonmember_price=180,
        stock_quantity=20,
        tax_type=TaxType.TAX_EXEMPT,
        image_url="/assets/products/pasture-colored-eggs.webp",
    ),
    ProductSpec(
        slug="murcott-soda",
        name="茂谷汽水",
        description="友善耕作茂谷柑製成的氣泡飲，330ml／罐。",
        category=ProductCategory.DRINKS,
        unit="罐",
        member_price=20,
        nonmember_price=20,
        stock_quantity=60,
        tax_type=TaxType.TAXABLE,
        image_url="/assets/products/murcott-soda.webp",
        can_ship=True,
        shipping_temperature=ShippingTemperature.AMBIENT,
        allowed_shipping_channels=ALL_AMBIENT_CHANNELS,
    ),
    ProductSpec(
        slug="roselle-malt-drink",
        name="洛神黑麥汁",
        description="新竹洛神花與德國黑麥釀製，無額外添加糖。",
        category=ProductCategory.DRINKS,
        unit="罐",
        member_price=25,
        nonmember_price=25,
        stock_quantity=60,
        tax_type=TaxType.TAXABLE,
        image_url="/assets/products/roselle-malt-drink.webp",
        can_ship=True,
        shipping_temperature=ShippingTemperature.AMBIENT,
        allowed_shipping_channels=ALL_AMBIENT_CHANNELS,
    ),
    ProductSpec(
        slug="traditional-black-bean-soy-sauce",
        name="古早味黑豆蔭油",
        description="在地黑豆發酵 180 天以上，500ml／瓶。",
        category=ProductCategory.PROCESSED,
        unit="瓶",
        member_price=280,
        nonmember_price=300,
        stock_quantity=30,
        tax_type=TaxType.TAXABLE,
        image_url="/assets/products/traditional-black-bean-soy-sauce.webp",
        can_ship=True,
        shipping_temperature=ShippingTemperature.AMBIENT,
        allowed_shipping_channels=ALL_AMBIENT_CHANNELS,
    ),
    ProductSpec(
        slug="sugar-free-black-bean-soy-sauce",
        name="古早味黑豆蔭油（無糖）",
        description="無添加糖黑豆蔭油，發酵 180 天以上，500ml／瓶。",
        category=ProductCategory.PROCESSED,
        unit="瓶",
        member_price=300,
        nonmember_price=320,
        stock_quantity=30,
        tax_type=TaxType.TAXABLE,
        image_url="/assets/products/sugar-free-black-bean-soy-sauce.webp",
        can_ship=True,
        shipping_temperature=ShippingTemperature.AMBIENT,
        allowed_shipping_channels=ALL_AMBIENT_CHANNELS,
    ),
    ProductSpec(
        slug="single-bottle-soy-sauce-gift-bag",
        name="單瓶醬油禮袋",
        description="單瓶醬油適用禮袋，請與醬油一同選購。",
        category=ProductCategory.DAILY_GOODS,
        unit="個",
        member_price=20,
        nonmember_price=20,
        stock_quantity=100,
        tax_type=TaxType.TAXABLE,
        image_url="/assets/products/traditional-black-bean-soy-sauce.webp",
        can_ship=True,
        shipping_temperature=ShippingTemperature.AMBIENT,
        allowed_shipping_channels=ALL_AMBIENT_CHANNELS,
    ),
    ProductSpec(
        slug="double-bottle-soy-sauce-gift-bag",
        name="雙瓶醬油禮袋",
        description="雙瓶醬油適用禮袋，請與醬油一同選購。",
        category=ProductCategory.DAILY_GOODS,
        unit="個",
        member_price=20,
        nonmember_price=20,
        stock_quantity=100,
        tax_type=TaxType.TAXABLE,
        image_url="/assets/products/sugar-free-black-bean-soy-sauce.webp",
        can_ship=True,
        shipping_temperature=ShippingTemperature.AMBIENT,
        allowed_shipping_channels=ALL_AMBIENT_CHANNELS,
    ),
)


LUNCH_MEAL_SLUGS = tuple(
    meal.slug for meal in OFFICIAL_MEALS if meal.period == "lunch"
)
DINNER_MEAL_SLUGS = tuple(
    meal.slug for meal in OFFICIAL_MEALS if meal.period == "dinner"
)


def build_meal(spec: MealSpec) -> Meal:
    return Meal(
        slug=spec.slug,
        name=spec.name,
        description=spec.description,
        image_url=spec.image_url,
        price=spec.price,
        tax_type=TaxType.TAXABLE,
        option_groups=build_option_groups(spec),
    )


def build_option_groups(spec: MealSpec) -> list[MealOptionGroup]:
    return [
        MealOptionGroup(
            name=group.name,
            min_selections=group.min_selections,
            max_selections=group.max_selections,
            position=group_position,
            options=[
                MealOption(
                    name=option.name,
                    price_delta=option.price_delta,
                    position=option_position,
                )
                for option_position, option in enumerate(group.options)
            ],
        )
        for group_position, group in enumerate(spec.option_groups)
    ]


def product_record(spec: ProductSpec) -> dict[str, object]:
    return {
        "slug": spec.slug,
        "name": spec.name,
        "description": spec.description,
        "category": spec.category,
        "unit": spec.unit,
        "member_price": spec.member_price,
        "nonmember_price": spec.nonmember_price,
        "stock_quantity": spec.stock_quantity,
        "tax_type": spec.tax_type,
        "image_url": spec.image_url,
        "can_ship": spec.can_ship,
        "shipping_temperature": spec.shipping_temperature,
        "allowed_shipping_channels": [
            channel.value for channel in spec.allowed_shipping_channels
        ],
    }
