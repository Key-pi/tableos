"""What the demo venues are (``seed_baseline_demo``), owner 2026-11-07.

Four venues set up differently, so every flow can be felt:

- **Night Owl · Поділ** — everything on: tables, pickup, delivery, shipping,
  Mini App, staff screen, kitchen and bar screens, showcase, variants and
  add-ons, staff calls (waiter, bartender, hookah), quick sale, three
  languages. With **Night Owl · Оболонь** it is a network: one bot for both
  points (the guest picks the point), shared bonuses, the network report,
  the network's owners and a manager working in both points.
- **Night Owl · Оболонь** — the second point: no bot of its own, its own
  smaller menu and hours, tables and pickup only, a bar screen only (the
  kitchen part counts as «не указано»).
- **Riverstone Cafe** — a restaurant: tables, pickup and delivery, category
  schedules (breakfast in the morning, a weekday lunch), steak doneness and
  coffee add-ons, kitchen and bar screens, staff work in the bot (no staff
  screen), Ukrainian and English.
- **Зелена полиця** — a house-plant shop: no tables, kitchen or staff calls;
  pickup, courier delivery and shipping by post (large plants are not
  shipped), pot sizes and soil packs as variants, pots and gift wrap as
  add-ons, SKU, barcodes and characteristics, a quick sale at the till and
  the staff screen; Ukrainian only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from decimal import Decimal

PODIL = "night-owl-hookah"
OBOLON = "night-owl-obolon"
CAFE = "riverstone-cafe"
SHOP = "green-shelf-plants"

NETWORK_NAME = "Night Owl Hookah Lounge"
NETWORK_POINTS = (PODIL, OBOLON)

TRANSFER_DETAILS = (
    "Карта 4149 0000 0000 0000 (демо), отримувач ФОП Демо Демо. "
    "У коментарі до переказу вкажіть ваш телефон."
)


def D(value) -> Decimal:  # noqa: N802 - short on purpose: the data below is long
    return Decimal(str(value))


@dataclass(frozen=True)
class VenueSeed:
    slug: str
    name: str
    phone: str
    address: str
    # The owner whose Telegram becomes DEMO_TELEGRAM_ID ("" — none: the
    # network's owner works here through a copy of their profile).
    tester_owner: str = ""
    # Own bot: (option, env, default username, display name); None — the
    # network's bot serves the point.
    bot: tuple[str, str, str, str] | None = None


VENUES = (
    VenueSeed(
        PODIL, "Night Owl · Поділ", "+380671110101", "Київ, вул. Сагайдачного, 25",
        tester_owner="smoke_owner_1",
        bot=("hookah", "DEMO_HOOKAH_BOT_TOKEN", "night_owl_hookah_bot", "Night Owl Bot"),
    ),
    VenueSeed(OBOLON, "Night Owl · Оболонь", "+380671110102", "Київ, Оболонська набережна, 7"),
    VenueSeed(
        CAFE, "Riverstone Cafe", "+380672220202", "Київ, вул. Набережно-Хрещатицька, 15",
        tester_owner="cafe_owner_1",
        bot=("cafe", "DEMO_CAFE_BOT_TOKEN", "riverstone_cafe_bot", "Riverstone Cafe Bot"),
    ),
    VenueSeed(
        SHOP, "Зелена полиця", "+380673330303", "Київ, вул. Ярославів Вал, 14",
        tester_owner="plant_owner_1",
        bot=("shop", "DEMO_SHOP_BOT_TOKEN", "green_shelf_plants_bot", "Зелена полиця"),
    ),
)
VENUE_BY_SLUG = {venue.slug: venue for venue in VENUES}


# --- settings -------------------------------------------------------------------

COMMON_SETTINGS = {
    "allow_menu_without_session": True,
    "module_menu_enabled": True,
    "module_cart_enabled": True,
    "module_orders_enabled": True,
    "module_billing_enabled": True,
    "module_reports_enabled": True,
    "module_quick_sale_enabled": True,
    "module_showcase_enabled": True,
    "module_options_enabled": True,
    "module_webapp_enabled": True,
    "transfer_payment_details": TRANSFER_DETAILS,
    "default_language": "uk",
    "language_uk": True,
}

SETTINGS = {
    # Everything on.
    PODIL: {
        "module_tables_enabled": True,
        "module_pickup_enabled": True,
        "pickup_pay_transfer": True,
        "pickup_pay_on_receipt": True,
        "module_delivery_enabled": True,
        "delivery_fee": D(80), "delivery_free_from": D(1200), "delivery_min_order": D(300),
        "delivery_pay_transfer": True,
        "delivery_pay_on_receipt": True,
        "module_shipping_enabled": True,
        "shipping_nova_poshta": True, "shipping_ukrposhta": True, "shipping_meest": True,
        "shipping_cost_carrier": True, "shipping_cost_fixed": True,
        "shipping_fee": D(70), "shipping_free_from": D(1500), "shipping_min_order": D(0),
        "shipping_pay_transfer": True, "shipping_pay_on_receipt": True,
        "module_staff_screen_enabled": True,
        "module_kitchen_screen_enabled": True,
        "module_staff_call_enabled": True,
        "staff_call_waiter_enabled": True,
        "staff_call_bartender_enabled": True,
        "staff_call_hookah_enabled": True,
        "language_ru": True, "language_en": True,
    },
    # The second point: tables and pickup (paid on the spot), a bar screen.
    OBOLON: {
        "module_tables_enabled": True,
        "module_pickup_enabled": True,
        "pickup_pay_transfer": False,
        "pickup_pay_on_receipt": True,
        "module_delivery_enabled": False,
        "module_shipping_enabled": False,
        "module_staff_screen_enabled": True,
        "module_kitchen_screen_enabled": True,
        "module_staff_call_enabled": True,
        "staff_call_waiter_enabled": True,
        "staff_call_bartender_enabled": False,
        "staff_call_hookah_enabled": True,
        "language_ru": True, "language_en": True,
    },
    # A restaurant: tables, pickup and delivery, kitchen screens; staff in the bot.
    CAFE: {
        "module_tables_enabled": True,
        "module_pickup_enabled": True,
        "pickup_pay_transfer": True,
        "pickup_pay_on_receipt": True,
        "module_delivery_enabled": True,
        "delivery_fee": D(60), "delivery_free_from": D(800), "delivery_min_order": D(200),
        "delivery_pay_transfer": True,
        "delivery_pay_on_receipt": True,
        "module_shipping_enabled": False,
        "module_staff_screen_enabled": False,
        "module_kitchen_screen_enabled": True,
        "module_staff_call_enabled": True,
        "staff_call_waiter_enabled": True,
        "staff_call_bartender_enabled": True,
        "staff_call_hookah_enabled": False,
        "language_ru": False, "language_en": True,
    },
    # A shop: no tables; pickup, courier and post; the till and the staff screen.
    SHOP: {
        "module_tables_enabled": False,
        "module_pickup_enabled": True,
        "pickup_pay_transfer": True,
        "pickup_pay_on_receipt": True,
        "module_delivery_enabled": True,
        "delivery_fee": D(120), "delivery_free_from": D(1500), "delivery_min_order": D(400),
        "delivery_pay_transfer": True,
        "delivery_pay_on_receipt": True,
        "module_shipping_enabled": True,
        "shipping_nova_poshta": True, "shipping_ukrposhta": True, "shipping_meest": False,
        "shipping_cost_carrier": True, "shipping_cost_fixed": True,
        "shipping_fee": D(80), "shipping_free_from": D(2000), "shipping_min_order": D(200),
        "shipping_pay_transfer": True, "shipping_pay_on_receipt": True,
        "module_staff_screen_enabled": True,
        "module_kitchen_screen_enabled": False,
        "module_staff_call_enabled": False,
        "staff_call_waiter_enabled": False,
        "staff_call_bartender_enabled": False,
        "staff_call_hookah_enabled": False,
        "language_ru": False, "language_en": False,
    },
}

# (scope, opens, closes); "" — the venue's own hours, every day.
HOURS = {
    PODIL: [("", time(12), time(4)), ("delivery", time(14), time(2))],
    OBOLON: [("", time(16), time(2))],
    CAFE: [("", time(8), time(23)), ("delivery", time(10), time(22))],
    # An online shop: no common hours; the counter and the courier have theirs.
    SHOP: [("pickup", time(10), time(20)), ("delivery", time(10), time(19))],
}


# --- staff and tables -------------------------------------------------------------


@dataclass(frozen=True)
class StaffSeed:
    username: str
    email: str
    first_name: str
    last_name: str
    role: str
    title: str
    hourly_rate: Decimal
    commission_rate: Decimal
    telegram_id: int
    # A manager with Admin (the manager preset); owners always have it.
    admin: bool = False
    # Other points of the network this manager works in (PQ-047).
    extra_points: tuple[str, ...] = ()

    @property
    def telegram_username(self) -> str:
        return self.username


STAFF = {
    PODIL: [
        StaffSeed("smoke_owner_1", "owner1@nightowl.local", "Ivan", "Moroz", "owner",
                  "Співвласник", D(0), D(0), 700100001),
        StaffSeed("smoke_owner_2", "owner2@nightowl.local", "Oleh", "Kravets", "owner",
                  "Співвласник", D(0), D(0), 700100002),
        StaffSeed("smoke_manager_1", "manager@nightowl.local", "Maksym", "Bondar", "manager",
                  "Керуючий мережі", D(220), D("2.50"), 700100004, admin=True,
                  extra_points=(OBOLON,)),
        StaffSeed("smoke_hookah_1", "hookah@nightowl.local", "Denys", "Tkachenko",
                  "hookah_master", "Кальянник", D(180), D(5), 700100005),
        StaffSeed("smoke_bar_1", "bar@nightowl.local", "Viktoriia", "Hrytsenko", "cashier",
                  "Барменка", D(170), D(3), 700100006),
        StaffSeed("smoke_waiter_1", "waiter@nightowl.local", "Bohdan", "Sydorenko", "waiter",
                  "Офіціант", D(145), D(4), 700100007),
    ],
    OBOLON: [
        StaffSeed("obolon_manager_1", "manager@obolon.nightowl.local", "Kateryna", "Lytvyn",
                  "manager", "Керуюча точки", D(210), D(2), 700400001, admin=True),
        StaffSeed("obolon_hookah_1", "hookah@obolon.nightowl.local", "Andrii", "Polishchuk",
                  "hookah_master", "Кальянник", D(175), D(5), 700400002),
        StaffSeed("obolon_waiter_1", "waiter@obolon.nightowl.local", "Sofiia", "Marchenko",
                  "waiter", "Офіціантка", D(140), D(4), 700400003),
    ],
    CAFE: [
        StaffSeed("cafe_owner_1", "owner1@riverstone.local", "Svitlana", "Petrenko", "owner",
                  "Співвласниця", D(0), D(0), 700200001),
        StaffSeed("cafe_owner_2", "owner2@riverstone.local", "Taras", "Melnyk", "owner",
                  "Співвласник", D(0), D(0), 700200002),
        StaffSeed("cafe_manager_1", "manager@riverstone.local", "Yuliia", "Hnatiuk", "manager",
                  "Менеджерка залу", D(230), D(2), 700200003, admin=True),
        StaffSeed("cafe_barman_1", "barman@riverstone.local", "Roman", "Lysenko", "cashier",
                  "Бармен", D(170), D(3), 700200004),
        StaffSeed("cafe_waiter_1", "waiter1@riverstone.local", "Iryna", "Koval", "waiter",
                  "Офіціантка", D(145), D(4), 700200005),
        StaffSeed("cafe_waiter_2", "waiter2@riverstone.local", "Nazar", "Kushnir", "waiter",
                  "Офіціант", D(145), D(4), 700200006),
        StaffSeed("cafe_waiter_3", "waiter3@riverstone.local", "Olha", "Romanenko", "waiter",
                  "Офіціантка", D(145), D(4), 700200007),
        StaffSeed("cafe_waiter_4", "waiter4@riverstone.local", "Artem", "Savchuk", "waiter",
                  "Офіціант", D(145), D(4), 700200008),
    ],
    SHOP: [
        StaffSeed("plant_owner_1", "owner@greenshelf.local", "Mariia", "Bondarenko", "owner",
                  "Власниця", D(0), D(0), 700300001),
        StaffSeed("plant_manager_1", "manager@greenshelf.local", "Dmytro", "Ivanenko",
                  "manager", "Менеджер магазину", D(200), D(2), 700300002, admin=True),
        StaffSeed("plant_seller_1", "seller1@greenshelf.local", "Alina", "Kostenko", "cashier",
                  "Продавчиня-флористка", D(160), D(3), 700300003),
        StaffSeed("plant_seller_2", "seller2@greenshelf.local", "Yurii", "Hnatenko", "cashier",
                  "Продавець", D(160), D(3), 700300004),
    ],
}

# Staff of an earlier demo who are no longer in it: switched off on a reset
# (not deleted — their history stays).
RETIRED_STAFF = {PODIL: ("smoke_owner_3",)}

# (number, name, seats, QR token)
TABLES = {
    PODIL: [(n, f"Стіл {n}", 4 if n <= 4 else (6 if n <= 6 else 8), f"nightowl_tbl_0{n}")
            for n in range(1, 8)] + [(8, "Бар", 6, "nightowl_bar_08")],
    OBOLON: [(n, f"Стіл {n}", 4, f"obolon_tbl_0{n}") for n in range(1, 5)]
    + [(5, "Тераса", 6, "obolon_tbl_05")],
    CAFE: [(n, f"Стіл {n}", 2 if n <= 3 else (4 if n <= 8 else (6 if n <= 11 else 8)),
            f"river_tbl_{n:02d}") for n in range(1, 13)],
    SHOP: [],
}

# Kitchen and bar screens (name, shows): links are issued once and printed.
SCREENS = {
    PODIL: [("Бар", "bar"), ("Кухня", "kitchen")],
    OBOLON: [("Бар", "bar")],  # the kitchen part counts as «не указано»
    CAFE: [("Кухня", "kitchen"), ("Бар", "bar")],
    SHOP: [],
}

# (code, name, whole network)
SOURCES = {
    PODIL: [("instagram", "Instagram", False), ("flyer", "Листівка біля входу", False),
            ("nightowl", "Instagram мережі", True)],
    OBOLON: [("obolon-flyer", "Листівка на Оболоні", False)],
    CAFE: [("instagram", "Instagram", False), ("google", "Google Maps", False)],
    SHOP: [("instagram", "Instagram", False), ("card", "Візитка в пакеті", False)],
}


# --- bonuses ----------------------------------------------------------------------

VISIT = ("Бонус за візит", "visit", "visit", D(20), D(0), D(0), 0, 30, {"once_per_day": True})
CASHBACK_5 = ("Кешбек 5%", "order_completed", "cashback", D(0), D(5), D(100), 0, 90, {})
WALK_IN_5 = ("Кешбек 5% на касі", "manual_purchase", "cashback", D(0), D(5), D(100), 0, 90, {})
MILESTONE = (
    "Бонус за кожне 5-те замовлення", "order_completed", "milestone", D(75), D(0), D(0), 5, 60,
    {},
)
CASHBACK_3 = ("Кешбек 3%", "order_completed", "cashback", D(0), D(3), D(200), 0, 120, {})
WALK_IN_3 = ("Кешбек 3% на касі", "manual_purchase", "cashback", D(0), D(3), D(200), 0, 120, {})

BONUSES = {
    PODIL: [VISIT, CASHBACK_5, WALK_IN_5, MILESTONE],
    OBOLON: [VISIT, CASHBACK_5, WALK_IN_5],
    CAFE: [VISIT, CASHBACK_5, WALK_IN_5, MILESTONE],
    SHOP: [CASHBACK_3, WALK_IN_3],
}
# Old names of the same programs (an earlier demo): renamed in place.
OLD_BONUS_NAMES = {
    "Visit reward": "Бонус за візит",
    "Cashback 5%": "Кешбек 5%",
    "Walk-in cashback 5%": "Кешбек 5% на касі",
    "Every 5th order bonus": "Бонус за кожне 5-те замовлення",
}


# --- menus ------------------------------------------------------------------------


@dataclass(frozen=True)
class ItemSeed:
    public_id: str
    name: str
    price: Decimal
    description: str = ""
    variants: tuple[tuple[str, Decimal], ...] = ()
    portion: str = ""  # "300 ml", "6 cm"
    old_price: Decimal | None = None
    badges: tuple[str, ...] = ()
    own_badges: tuple[str, ...] = ()
    sku: str = ""
    barcode: str = ""
    attributes: tuple[tuple[str, str], ...] = ()
    shipping: bool | None = None  # None — as the category says


@dataclass(frozen=True)
class CategorySeed:
    name: str
    sort_order: int
    items: tuple[ItemSeed, ...] = ()
    parent: str = ""
    old_name: str = ""
    station: str = ""
    description: str = ""
    schedule: tuple[time, time] | None = None
    weekdays: tuple[int, ...] = ()
    table: bool = True
    pickup: bool = True  # pickup and delivery
    shipping: bool = False


@dataclass(frozen=True)
class GroupSeed:
    name: str
    min_choices: int
    max_choices: int
    options: tuple[tuple[str, Decimal], ...]
    categories: tuple[str, ...] = ()
    items: tuple[str, ...] = ()


@dataclass(frozen=True)
class MenuSeed:
    categories: tuple[CategorySeed, ...]
    groups: tuple[GroupSeed, ...] = ()
    own_badges: tuple[tuple[str, str], ...] = ()  # (name, emoji)


def V(*pairs) -> tuple[tuple[str, Decimal], ...]:  # noqa: N802
    return tuple((name, D(price)) for name, price in pairs)


ADULTS = ("adults",)

PODIL_MENU = MenuSeed(
    own_badges=(("Вибір кальянника", "🦉"),),
    categories=(
        CategorySeed("Кальяни", 10, old_name="Кальяны", pickup=False, items=(
            ItemSeed("SMKHK001", "Класичний кальян", D(450),
                     "Табак під ваш смак; чаша — глиняна або на фрукті.",
                     variants=V(("Глиняна чаша", 450), ("На грейпфруті", 650),
                                ("На ананасі", 750)),
                     badges=("hit",), portion="60 min"),
            ItemSeed("SMKHK002", "Преміум кальян", D(650),
                     "Преміальний табак, довга й щільна сесія.",
                     own_badges=("Вибір кальянника",), portion="90 min"),
            ItemSeed("SMKHK003", "Кальян «Два шланги»", D(900),
                     "Для компанії від чотирьох гостей.", badges=("recommended",)),
        )),
        CategorySeed("Фірмові мікси", 20, parent="Кальяни", old_name="Фирменные миксы",
                     pickup=False, items=(
            ItemSeed("SMKSG001", "Night Owl Mix", D(720), "Темний лист, цитрус, легкі прянощі.",
                     badges=("hit",),
                     attributes=(("Смак", "цитрус, прянощі"), ("Міцність", "середня"))),
            ItemSeed("SMKSG002", "Kyiv Sunset Mix", D(760),
                     "Ягідний смак із вершковим післясмаком.", badges=("new",),
                     attributes=(("Смак", "ягоди, вершки"), ("Міцність", "легка"))),
        )),
        CategorySeed("Напої", 30, old_name="Безалкогольні напої", station="bar"),
        CategorySeed("Лимонади й чай", 31, parent="Напої", items=(
            ItemSeed("SMKDR001", "Домашній лимонад", D(150), "Маракуя, лайм, м'ята.",
                     variants=V(("0,5 л", 150), ("1 л", 260))),
            ItemSeed("SMKDR003", "Чайник крафтового чаю", D(180), portion="800 ml"),
        )),
        CategorySeed("Кава", 32, parent="Напої", items=(
            ItemSeed("SMKDR002", "Еспресо", D(95), portion="30 ml"),
            ItemSeed("SMKDR004", "Капучино", D(120), variants=V(("250 мл", 120), ("350 мл", 145))),
            ItemSeed("SMKDR005", "Лате", D(130), variants=V(("300 мл", 130), ("400 мл", 155))),
        )),
        CategorySeed("Закуски", 40, station="kitchen", items=(
            ItemSeed("SMKFD001", "Начос із сирним соусом", D(210), portion="250 g"),
            ItemSeed("SMKFD002", "Сирна тарілка", D(390), "Чотири види сиру, мед, горіхи.",
                     old_price=D(450), badges=("recommended",), portion="300 g"),
            ItemSeed("SMKFD003", "Брускети з томатами", D(180), badges=("vegetarian",),
                     portion="3 pcs"),
        )),
        CategorySeed("Алкоголь", 50, station="bar", pickup=False,
                     schedule=(time(16), time(4)),
                     description="Алкоголь продаємо з 16:00 до 04:00.", items=(
            ItemSeed("SMKAL001", "Aperol Spritz", D(240), badges=ADULTS),
            ItemSeed("SMKAL002", "Negroni", D(250), badges=ADULTS),
            ItemSeed("SMKAL003", "Whiskey Sour", D(260), badges=ADULTS),
            ItemSeed("SMKAL004", "Келих вина", D(190), badges=ADULTS,
                     variants=V(("Біле сухе", 190), ("Червоне сухе", 190))),
        )),
        CategorySeed("Мерч і аксесуари", 60, shipping=True, items=(
            ItemSeed("SMKMR001", "Футболка Night Owl", D(650), "Чорна, з совою на спині.",
                     variants=V(("S", 650), ("M", 650), ("L", 650), ("XL", 650)), sku="NO-TS",
                     attributes=(("Матеріал", "бавовна 100%"),)),
            ItemSeed("SMKMR002", "Особистий мундштук", D(120), sku="NO-MP"),
            ItemSeed("SMKMR003", "Подарунковий сертифікат", D(500), sku="NO-GC",
                     variants=V(("500 грн", 500), ("1000 грн", 1000), ("2000 грн", 2000))),
        )),
    ),
    groups=(
        GroupSeed("Міцність", 1, 1, V(("Легка", 0), ("Середня", 0), ("Міцна", 0)),
                  categories=("Кальяни",)),
        GroupSeed("Додатково до кальяну", 0, 2,
                  V(("Лід у колбу", 30), ("Молоко в колбу", 50), ("Заміна чаші", 150)),
                  categories=("Кальяни",)),
        GroupSeed("Чай", 1, 1, V(("Пуер", 0), ("Улун", 0), ("Ягідний збір", 0)),
                  items=("SMKDR003",)),
        GroupSeed("Молоко", 0, 1, V(("Вівсяне", 25), ("Кокосове", 30)),
                  items=("SMKDR004", "SMKDR005")),
        GroupSeed("Сироп", 0, 2, V(("Карамель", 20), ("Ваніль", 20), ("Солона карамель", 20)),
                  categories=("Кава",)),
        GroupSeed("Соус", 0, 2, V(("Сальса", 30), ("Гуакамоле", 45), ("Сирний", 30)),
                  items=("SMKFD001",)),
    ),
)

OBOLON_MENU = MenuSeed(
    categories=(
        CategorySeed("Кальяни", 10, pickup=False, items=(
            ItemSeed("OBLHK001", "Класичний кальян", D(420),
                     variants=V(("Глиняна чаша", 420), ("На грейпфруті", 620))),
            ItemSeed("OBLHK002", "Преміум кальян", D(600)),
        )),
        CategorySeed("Напої", 20, station="bar", items=(
            ItemSeed("OBLDR001", "Домашній лимонад, 0,5 л", D(140)),
            ItemSeed("OBLDR002", "Чайник чаю", D(170)),
            ItemSeed("OBLDR003", "Еспресо", D(90)),
        )),
        CategorySeed("Закуски", 30, station="kitchen", items=(
            ItemSeed("OBLFD001", "Начос", D(200)),
            ItemSeed("OBLFD002", "Картопля фрі", D(140)),
        )),
    ),
    groups=(
        GroupSeed("Міцність", 1, 1, V(("Легка", 0), ("Середня", 0), ("Міцна", 0)),
                  categories=("Кальяни",)),
        GroupSeed("Чай", 1, 1, V(("Чорний", 0), ("Зелений", 0)), items=("OBLDR002",)),
        GroupSeed("Соус", 0, 1, V(("Кетчуп", 20), ("Сирний", 30)), items=("OBLFD002",)),
    ),
)

CAFE_MENU = MenuSeed(
    own_badges=(("Шеф рекомендує", "👨‍🍳"),),
    categories=(
        CategorySeed("Сніданки", 10, old_name="Завтраки", station="kitchen",
                     schedule=(time(8), time(12)), description="Щодня з 08:00 до 12:00.", items=(
            ItemSeed("CAFBR001", "Сирники", D(220), portion="250 g", badges=("hit",),
                     attributes=(("Калорійність", "480 ккал"),
                                 ("Алергени", "глютен, молоко, яйця"))),
            ItemSeed("CAFBR002", "Яйця Бенедикт", D(295),
                     variants=V(("З беконом", 295), ("З лососем", 345))),
            ItemSeed("CAFBR003", "Гранола з йогуртом", D(180), badges=("vegetarian",)),
        )),
        CategorySeed("Бізнес-ланч", 15, station="kitchen", schedule=(time(12), time(16)),
                     weekdays=(0, 1, 2, 3, 4), description="Пн–пт з 12:00 до 16:00.", items=(
            ItemSeed("CAFLN001", "Ланч «Суп + салат»", D(230)),
            ItemSeed("CAFLN002", "Ланч «Суп + гаряче»", D(290)),
        )),
        CategorySeed("Салати", 20, old_name="Салаты", station="kitchen", items=(
            ItemSeed("CAFSL001", "Цезар з куркою", D(260), portion="280 g"),
            ItemSeed("CAFSL002", "Грецький салат", D(210), badges=("vegetarian", "gluten_free")),
        )),
        CategorySeed("Основні страви", 30, old_name="Основные блюда", station="kitchen", items=(
            ItemSeed("CAFMN001", "Курячий стейк", D(340)),
            ItemSeed("CAFMN004", "Яловичий стейк", D(520), own_badges=("Шеф рекомендує",),
                     variants=V(("250 г", 520), ("350 г", 690))),
            ItemSeed("CAFMN002", "Паста карбонара", D(285)),
            ItemSeed("CAFMN003", "Лосось з овочами", D(420), old_price=D(470)),
        )),
        CategorySeed("Десерти", 40, old_name="Десерты", station="kitchen", items=(
            ItemSeed("CAFDS001", "Чизкейк", D(170)),
            ItemSeed("CAFDS002", "Наполеон", D(165)),
        )),
        CategorySeed("Напої", 50, old_name="Кава й чай", station="bar"),
        CategorySeed("Кава", 51, parent="Напої", items=(
            ItemSeed("CAFDR005", "Еспресо", D(70), portion="30 ml"),
            ItemSeed("CAFDR001", "Капучино", D(110), variants=V(("250 мл", 110), ("350 мл", 135))),
            ItemSeed("CAFDR002", "Флет вайт", D(125)),
            ItemSeed("CAFDR004", "Лате", D(115), variants=V(("300 мл", 115), ("400 мл", 140))),
        )),
        CategorySeed("Чай і лимонади", 52, parent="Напої", items=(
            ItemSeed("CAFDR003", "Чайник чаю", D(140)),
            ItemSeed("CAFDR006", "Лимонад", D(95), variants=V(("0,4 л", 95), ("1 л", 210))),
        )),
        CategorySeed("Алкоголь", 53, parent="Напої", pickup=False, items=(
            ItemSeed("CAFAL001", "Мімоза", D(210), badges=ADULTS),
            ItemSeed("CAFAL002", "Біле домашнє вино", D(195), badges=ADULTS),
            ItemSeed("CAFAL003", "Крафтове пиво", D(165), badges=ADULTS),
        )),
        # Packs instead of weight (catalog step 2).
        CategorySeed("Кава в зернах", 70, old_name="Кава в зернах і чай", items=(
            ItemSeed("CAFBN001", "Кава в зернах Ефіопія Іргачеффе", D(320),
                     variants=V(("250 г", 320), ("1 кг", 1150))),
            ItemSeed("CAFBN002", "Кава в зернах Бразилія Сантос", D(290),
                     variants=V(("250 г", 290), ("1 кг", 1020))),
            ItemSeed("CAFBN003", "Чай листовий", D(180),
                     variants=V(("100 г", 180), ("250 г", 400))),
        )),
    ),
    groups=(
        GroupSeed("До сирників", 1, 1, V(("Сметана", 0), ("Варення", 0), ("Згущене молоко", 15)),
                  items=("CAFBR001",)),
        GroupSeed("Суп дня", 1, 1,
                  V(("Борщ", 0), ("Крем-суп з гарбуза", 0), ("Курячий бульйон", 0)),
                  categories=("Бізнес-ланч",)),
        GroupSeed("Напій до ланчу", 1, 1, V(("Морс", 0), ("Чай", 0), ("Американо", 20)),
                  categories=("Бізнес-ланч",)),
        GroupSeed("Додати до салату", 0, 2, V(("Креветки", 90), ("Бекон", 45), ("Пармезан", 35)),
                  items=("CAFSL001",)),
        GroupSeed("Прожарка", 1, 1,
                  V(("Rare", 0), ("Medium rare", 0), ("Medium", 0), ("Well done", 0)),
                  items=("CAFMN004",)),
        GroupSeed("Молоко", 0, 1, V(("Вівсяне", 20), ("Мигдальне", 25), ("Безлактозне", 15)),
                  items=("CAFDR001", "CAFDR002", "CAFDR004")),
        GroupSeed("Сироп", 0, 2, V(("Карамель", 15), ("Ваніль", 15), ("Лісовий горіх", 15)),
                  categories=("Кава",)),
        GroupSeed("Чай", 1, 1, V(("Чорний", 0), ("Зелений", 0), ("Трав'яний", 0)),
                  items=("CAFDR003",)),
    ),
)


def _plant(public_id, name, price, *, code, number, **extra) -> ItemSeed:
    return ItemSeed(public_id, name, D(price), sku=f"GS-{code}",
                    barcode=f"48200000{number:05d}", **extra)


SHOP_MENU = MenuSeed(
    own_badges=(("Для початківців", "🌱"), ("Безпечна для тварин", "🐾")),
    categories=(
        CategorySeed("Рослини", 10, table=False, shipping=True,
                     description="Догляд — у характеристиках кожної рослини."),
        CategorySeed("Невибагливі", 11, parent="Рослини", table=False, shipping=True, items=(
            _plant("PLTEZ001", "Заміокулькас", 390, code="ZZ", number=1,
                   description="Пробачить забутий полив і тінь.",
                   variants=V(("S, горщик Ø12 см", 390), ("M, горщик Ø17 см", 690),
                              ("L, горщик Ø24 см", 1290)),
                   badges=("hit",), own_badges=("Для початківців",),
                   attributes=(("Світло", "напівтінь"), ("Полив", "раз на 2–3 тижні"),
                               ("Для тварин", "токсична"))),
            _plant("PLTEZ002", "Сансев'єрія", 290, code="SN", number=2,
                   variants=V(("S, горщик Ø12 см", 290), ("M, горщик Ø17 см", 520),
                              ("L, горщик Ø24 см", 980)),
                   own_badges=("Для початківців",),
                   attributes=(("Світло", "будь-яке"), ("Полив", "раз на 3 тижні"))),
            _plant("PLTEZ003", "Потос золотий", 250, code="PT", number=3,
                   variants=V(("S, горщик Ø12 см", 250), ("M, горщик Ø17 см", 450)),
                   badges=("new",),
                   attributes=(("Світло", "розсіяне"), ("Полив", "щотижня"))),
        )),
        # Large plants travel by courier only, not by post.
        CategorySeed("Великі рослини", 12, parent="Рослини", table=False, shipping=False, items=(
            _plant("PLTBG001", "Монстера делікатесна", 1450, code="MN", number=11,
                   variants=V(("M, горщик Ø21 см", 1450), ("L, горщик Ø27 см", 2600)),
                   attributes=(("Висота", "70–120 см"), ("Світло", "яскраве розсіяне"))),
            _plant("PLTBG002", "Фікус ліровидний", 2900, code="FL", number=12,
                   old_price=D(3400), attributes=(("Висота", "120 см"),)),
            _plant("PLTBG003", "Стреліція Ніколая", 3200, code="ST", number=13,
                   badges=("limited",), attributes=(("Висота", "140 см"),)),
        )),
        CategorySeed("Сукуленти й кактуси", 13, parent="Рослини", table=False, shipping=True,
                     items=(
            _plant("PLTSC001", "Ехеверія", 120, code="EH", number=21, portion="6 cm",
                   own_badges=("Безпечна для тварин",)),
            _plant("PLTSC002", "Набір сукулентів, 3 шт", 330, code="SET3", number=22,
                   badges=("hit",)),
            _plant("PLTSC003", "Маміллярія", 150, code="MM", number=23, portion="8 cm"),
        )),
        CategorySeed("Догляд", 20, table=False, shipping=True),
        CategorySeed("Ґрунти й дренаж", 21, parent="Догляд", table=False, shipping=True, items=(
            _plant("PLTSL001", "Ґрунт універсальний", 95, code="SOIL", number=31,
                   variants=V(("2 л", 95), ("5 л", 190), ("10 л", 330)), badges=("eco",)),
            _plant("PLTSL002", "Ґрунт для сукулентів", 110, code="SOILS", number=32,
                   variants=V(("2 л", 110), ("5 л", 220))),
            _plant("PLTSL003", "Керамзит дренажний", 60, code="DRN", number=33,
                   variants=V(("1 л", 60), ("3 л", 150))),
        )),
        CategorySeed("Добрива й засоби", 22, parent="Догляд", table=False, shipping=True, items=(
            _plant("PLTCR001", "Добриво для декоративно-листяних", 180, code="FRT", number=41,
                   portion="250 ml"),
            _plant("PLTCR002", "Обприскувач", 220, code="SPR", number=42, portion="500 ml"),
        )),
        CategorySeed("Горщики й кашпо", 30, table=False, shipping=True, items=(
            _plant("PLTPT001", "Кашпо керамічне", 450, code="POTC", number=51,
                   variants=V(("Ø12 см", 450), ("Ø17 см", 650), ("Ø21 см", 890)),
                   badges=("handmade",)),
            _plant("PLTPT002", "Кашпо бетонне", 520, code="POTB", number=52,
                   variants=V(("Ø15 см", 520), ("Ø20 см", 760))),
        )),
        CategorySeed("Подарункові сертифікати", 40, table=False, shipping=True, items=(
            _plant("PLTGC001", "Подарунковий сертифікат", 500, code="GC", number=61,
                   variants=V(("500 грн", 500), ("1000 грн", 1000), ("2000 грн", 2000))),
        )),
    ),
    groups=(
        GroupSeed("Кашпо", 0, 1,
                  V(("Керамічне біле", 250), ("Бетонне сіре", 320), ("Плетений кошик", 280)),
                  categories=("Рослини",)),
        GroupSeed("Пересадка", 0, 1, V(("Пересадити в кашпо", 150),), categories=("Рослини",)),
        GroupSeed("Подарунок", 0, 2, V(("Листівка з побажанням", 40),
                                        ("Подарункова упаковка", 90)),
                  categories=("Рослини", "Горщики й кашпо")),
    ),
)

MENUS = {PODIL: PODIL_MENU, OBOLON: OBOLON_MENU, CAFE: CAFE_MENU, SHOP: SHOP_MENU}

MODULE_NAMES = (
    ("module_tables_enabled", "столы"),
    ("module_pickup_enabled", "самовывоз"),
    ("module_delivery_enabled", "доставка"),
    ("module_shipping_enabled", "отправка почтой"),
    ("module_webapp_enabled", "Mini App"),
    ("module_staff_screen_enabled", "экран персонала"),
    ("module_kitchen_screen_enabled", "экран кухни и бара"),
    ("module_showcase_enabled", "витрина"),
    ("module_options_enabled", "варианты и добавки"),
    ("module_staff_call_enabled", "вызов персонала"),
    ("module_quick_sale_enabled", "быстрая продажа"),
    ("module_billing_enabled", "счета"),
    ("module_reports_enabled", "отчёты"),
)
