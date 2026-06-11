from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.bonuses.models import BonusProgram
from apps.employees.models import EmployeeProfile
from apps.menu.models import MenuCategory, MenuItem
from apps.partners.models import BotInstance, Partner, PartnerBotSettings
from apps.tables.models import Table
from apps.users.constants import AdminAccessPreset
from apps.users.models import AdminAccessProfile, TelegramAccount


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
    telegram_username: str


@dataclass(frozen=True)
class TableSeed:
    number: int
    name: str
    seats: int
    qr_token: str


@dataclass(frozen=True)
class MenuItemSeed:
    public_id: str
    name: str
    price: Decimal
    sort_order: int
    description: str = ""


class Command(BaseCommand):
    help = (
        "Seed a full two-partner demo baseline with staff, bots, tables, menu, "
        "and loyalty rules for local QA."
    )

    def add_arguments(self, parser):
        parser.add_argument("--admin-username", default="platform_admin")
        parser.add_argument("--admin-email", default="admin@tableos.local")
        parser.add_argument("--admin-password", default="tableos12345")
        parser.add_argument(
            "--staff-password",
            default="tableos12345",
            help="Password assigned to all seeded venue staff users.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        admin_user = self._ensure_platform_admin(
            username=options["admin_username"].strip(),
            email=options["admin_email"].strip(),
            password=options["admin_password"],
        )

        smoke_partner = self._ensure_partner(
            slug="night-owl-hookah",
            name="Night Owl Hookah Lounge",
            phone="+380671110101",
            bot_username="night_owl_hookah_bot",
            bot_display_name="Night Owl Hookah Bot",
        )
        cafe_partner = self._ensure_partner(
            slug="riverstone-cafe",
            name="Riverstone Cafe",
            phone="+380672220202",
            bot_username="riverstone_cafe_bot",
            bot_display_name="Riverstone Cafe Bot",
        )

        staff_password = options["staff_password"]
        self._seed_staff(smoke_partner, self._smoke_staff(), staff_password)
        self._seed_staff(cafe_partner, self._cafe_staff(), staff_password)
        self._seed_tables(smoke_partner, self._smoke_tables())
        self._seed_tables(cafe_partner, self._cafe_tables())
        self._seed_smoke_menu(smoke_partner)
        self._seed_cafe_menu(cafe_partner)
        self._seed_bonus_programs(smoke_partner, include_milestone=False)
        self._seed_bonus_programs(cafe_partner, include_milestone=True)

        self.stdout.write(self.style.SUCCESS("Baseline demo dataset is ready."))
        self.stdout.write(
            self.style.SUCCESS(
                f"Platform admin: {admin_user.username} / {options['admin_password']}"
            )
        )
        self.stdout.write(
            self.style.SUCCESS(f"Venue staff password: {options['staff_password']}")
        )
        self.stdout.write("Partners:")
        self._print_partner_summary(smoke_partner)
        self._print_partner_summary(cafe_partner)
        self.stdout.write("Staff accounts:")
        self._print_staff_summary("night-owl-hookah", self._smoke_staff())
        self._print_staff_summary("riverstone-cafe", self._cafe_staff())

    @staticmethod
    def _ensure_platform_admin(*, username: str, email: str, password: str):
        user_model = get_user_model()
        user, _ = user_model.objects.get_or_create(
            username=username,
            defaults={
                "email": email,
                "first_name": "Platform",
                "last_name": "Admin",
                "role": user_model.Role.ADMIN,
                "is_staff": True,
                "is_superuser": True,
                "is_active": True,
            },
        )
        user.email = email
        user.first_name = "Platform"
        user.last_name = "Admin"
        user.role = user_model.Role.ADMIN
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.set_password(password)
        user.save()
        return user

    @staticmethod
    def _ensure_partner(
        *,
        slug: str,
        name: str,
        phone: str,
        bot_username: str,
        bot_display_name: str,
    ) -> Partner:
        partner, _ = Partner.objects.get_or_create(
            slug=slug,
            defaults={
                "name": name,
                "timezone": "Europe/Kiev",
                "contact_phone": phone,
                "status": Partner.Status.ACTIVE,
            },
        )
        partner.name = name
        partner.timezone = "Europe/Kiev"
        partner.contact_phone = phone
        partner.status = Partner.Status.ACTIVE
        partner.save()

        settings, _ = PartnerBotSettings.objects.get_or_create(partner=partner)
        settings.allow_menu_without_session = True
        settings.show_help_button = True
        settings.show_session_button = True
        settings.show_cart_button = True
        settings.show_checkout_button = True
        settings.show_loyalty_button = True
        settings.show_call_staff_button = True
        settings.show_request_bill_button = True
        settings.button_loyalty_label = "Мой профиль"
        settings.staff_call_waiter_enabled = True
        settings.staff_call_bartender_enabled = True
        settings.staff_call_hookah_enabled = slug == "night-owl-hookah"
        settings.save()

        bot_instance = partner.bot_instances.order_by("created_at").first()
        if bot_instance is None:
            bot_instance = BotInstance(
                partner=partner,
                username=bot_username,
                display_name=bot_display_name,
                mode=BotInstance.Mode.POLLING,
                is_active=False,
            )
            # Fresh seed bots stay inactive and use a placeholder token until
            # a real BotFather token is added in admin.
            bot_instance.set_token(f"seed-placeholder-token-{slug}")
        else:
            if not bot_instance.username:
                bot_instance.username = bot_username
            bot_instance.display_name = bot_display_name
            bot_instance.mode = BotInstance.Mode.POLLING
            if not bot_instance.has_usable_token:
                bot_instance.is_active = False
                bot_instance.set_token(f"seed-placeholder-token-{slug}")
        bot_instance.save()
        return partner

    @staticmethod
    def _seed_staff(partner: Partner, staff_rows: list[StaffSeed], password: str) -> None:
        user_model = get_user_model()
        for staff in staff_rows:
            user, _ = user_model.objects.get_or_create(
                username=staff.username,
                defaults={
                    "email": staff.email,
                    "first_name": staff.first_name,
                    "last_name": staff.last_name,
                    "partner": partner,
                    "role": staff.role,
                    "is_staff": False,
                    "is_active": True,
                },
            )
            user.email = staff.email
            user.first_name = staff.first_name
            user.last_name = staff.last_name
            user.partner = partner
            user.role = staff.role
            user.is_staff = False
            user.is_active = True
            user.set_password(password)
            user.save()

            telegram_account, _ = TelegramAccount.objects.get_or_create(
                telegram_id=staff.telegram_id,
                defaults={
                    "username": staff.telegram_username,
                    "first_name": staff.first_name,
                    "last_name": staff.last_name,
                    "language_code": "ru",
                },
            )
            telegram_account.username = staff.telegram_username
            telegram_account.first_name = staff.first_name
            telegram_account.last_name = staff.last_name
            telegram_account.language_code = "ru"
            telegram_account.save()

            EmployeeProfile.objects.update_or_create(
                partner=partner,
                user=user,
                defaults={
                    "telegram_account": telegram_account,
                    "title": staff.title,
                    "hourly_rate": staff.hourly_rate,
                    "commission_rate": staff.commission_rate,
                    "bot_notifications_enabled": staff.role
                    in {"owner", "manager", "cashier", "waiter", "hookah_master"},
                    "notify_on_order_created": staff.role in {"owner", "manager", "cashier"},
                    "notify_on_order_status_changed": staff.role in {"owner", "manager", "cashier"},
                    "notify_on_guest_calls": staff.role
                    in {"owner", "manager", "cashier", "waiter", "hookah_master"},
                    "notify_on_billing_requests": staff.role
                    in {"owner", "manager", "cashier", "waiter"},
                    "is_active": True,
                },
            )
            AdminAccessProfile.objects.update_or_create(
                user=user,
                defaults={
                    "partner": partner,
                    "preset": (
                        AdminAccessPreset.OWNER
                        if staff.role == "owner"
                        else AdminAccessPreset.CUSTOM
                    ),
                    "is_partner_owner": staff.role == "owner",
                    "can_access_admin": staff.role == "owner",
                    "is_active": True,
                },
            )

    @staticmethod
    def _seed_tables(partner: Partner, table_rows: list[TableSeed]) -> None:
        for table in table_rows:
            Table.objects.update_or_create(
                partner=partner,
                number=table.number,
                defaults={
                    "name": table.name,
                    "seats": table.seats,
                    "qr_token": table.qr_token,
                    "is_active": True,
                },
            )

    @staticmethod
    def _ensure_category(partner: Partner, name: str, sort_order: int) -> MenuCategory:
        category, _ = MenuCategory.objects.get_or_create(
            partner=partner,
            name=name,
            defaults={"sort_order": sort_order, "is_active": True},
        )
        category.sort_order = sort_order
        category.is_active = True
        category.save()
        return category

    @staticmethod
    def _seed_category_items(
        partner: Partner,
        category: MenuCategory,
        items: list[MenuItemSeed],
    ) -> None:
        for item in items:
            MenuItem.objects.update_or_create(
                partner=partner,
                public_id=item.public_id,
                defaults={
                    "category": category,
                    "name": item.name,
                    "description": item.description,
                    "price": item.price,
                    "sort_order": item.sort_order,
                    "item_type": MenuItem.ItemType.INTERNAL,
                    "is_available": True,
                },
            )

    def _seed_smoke_menu(self, partner: Partner) -> None:
        hookahs = self._ensure_category(partner, "Кальяны", 10)
        signatures = self._ensure_category(partner, "Фирменные миксы", 20)
        soft_drinks = self._ensure_category(partner, "Безалкогольные напитки", 30)
        alcohol = self._ensure_category(partner, "Алкоголь", 40)

        self._seed_category_items(
            partner,
            hookahs,
            [
                MenuItemSeed("SMKHK001", "Classic Hookah", Decimal("450.00"), 10),
                MenuItemSeed("SMKHK002", "Premium Hookah", Decimal("650.00"), 20),
                MenuItemSeed("SMKHK003", "Fruit Bowl Hookah", Decimal("820.00"), 30),
            ],
        )
        self._seed_category_items(
            partner,
            signatures,
            [
                MenuItemSeed(
                    "SMKSG001",
                    "Night Owl Mix",
                    Decimal("720.00"),
                    10,
                    "Dark leaf, citrus, light spice.",
                ),
                MenuItemSeed(
                    "SMKSG002",
                    "Kyiv Sunset Mix",
                    Decimal("760.00"),
                    20,
                    "Berry profile with creamy finish.",
                ),
            ],
        )
        self._seed_category_items(
            partner,
            soft_drinks,
            [
                MenuItemSeed("SMKDR001", "Homemade Lemonade", Decimal("150.00"), 10),
                MenuItemSeed("SMKDR002", "Espresso", Decimal("95.00"), 20),
                MenuItemSeed("SMKDR003", "Craft Tea Pot", Decimal("180.00"), 30),
            ],
        )
        self._seed_category_items(
            partner,
            alcohol,
            [
                MenuItemSeed("SMKAL001", "Aperol Spritz", Decimal("240.00"), 10),
                MenuItemSeed("SMKAL002", "Negroni", Decimal("250.00"), 20),
                MenuItemSeed("SMKAL003", "Whiskey Sour", Decimal("260.00"), 30),
                MenuItemSeed("SMKAL004", "House Wine Glass", Decimal("190.00"), 40),
            ],
        )

    def _seed_cafe_menu(self, partner: Partner) -> None:
        breakfast = self._ensure_category(partner, "Завтраки", 10)
        salads = self._ensure_category(partner, "Салаты", 20)
        mains = self._ensure_category(partner, "Основные блюда", 30)
        desserts = self._ensure_category(partner, "Десерты", 40)
        soft_drinks = self._ensure_category(partner, "Кофе и чай", 50)
        alcohol = self._ensure_category(partner, "Алкоголь", 60)

        self._seed_category_items(
            partner,
            breakfast,
            [
                MenuItemSeed("CAFBR001", "Syrniki", Decimal("220.00"), 10),
                MenuItemSeed("CAFBR002", "Eggs Benedict", Decimal("295.00"), 20),
                MenuItemSeed("CAFBR003", "Granola Bowl", Decimal("180.00"), 30),
            ],
        )
        self._seed_category_items(
            partner,
            salads,
            [
                MenuItemSeed("CAFSL001", "Caesar with Chicken", Decimal("260.00"), 10),
                MenuItemSeed("CAFSL002", "Greek Salad", Decimal("210.00"), 20),
            ],
        )
        self._seed_category_items(
            partner,
            mains,
            [
                MenuItemSeed("CAFMN001", "Chicken Steak", Decimal("340.00"), 10),
                MenuItemSeed("CAFMN002", "Pasta Carbonara", Decimal("285.00"), 20),
                MenuItemSeed("CAFMN003", "Salmon with Vegetables", Decimal("420.00"), 30),
            ],
        )
        self._seed_category_items(
            partner,
            desserts,
            [
                MenuItemSeed("CAFDS001", "Cheesecake", Decimal("170.00"), 10),
                MenuItemSeed("CAFDS002", "Napoleon", Decimal("165.00"), 20),
            ],
        )
        self._seed_category_items(
            partner,
            soft_drinks,
            [
                MenuItemSeed("CAFDR001", "Cappuccino", Decimal("110.00"), 10),
                MenuItemSeed("CAFDR002", "Flat White", Decimal("125.00"), 20),
                MenuItemSeed("CAFDR003", "Tea Pot", Decimal("140.00"), 30),
            ],
        )
        self._seed_category_items(
            partner,
            alcohol,
            [
                MenuItemSeed("CAFAL001", "Mimosa", Decimal("210.00"), 10),
                MenuItemSeed("CAFAL002", "House White Wine", Decimal("195.00"), 20),
                MenuItemSeed("CAFAL003", "Craft Beer", Decimal("165.00"), 30),
            ],
        )

    @staticmethod
    def _seed_bonus_programs(partner: Partner, *, include_milestone: bool) -> None:
        BonusProgram.objects.update_or_create(
            partner=partner,
            name="Visit reward",
            defaults={
                "trigger_event": BonusProgram.TriggerEvent.VISIT,
                "program_type": BonusProgram.ProgramType.VISIT,
                "strategy_code": "",
                "fixed_amount": Decimal("20.00"),
                "percent": Decimal("0.00"),
                "min_order_total": Decimal("0.00"),
                "milestone_order_count": 0,
                "max_redeem_share": Decimal("30.00"),
                "expires_in_days": 30,
                "config": {"once_per_day": True},
                "is_active": True,
            },
        )
        BonusProgram.objects.update_or_create(
            partner=partner,
            name="Cashback 5%",
            defaults={
                "trigger_event": BonusProgram.TriggerEvent.ORDER_COMPLETED,
                "program_type": BonusProgram.ProgramType.CASHBACK,
                "strategy_code": "",
                "fixed_amount": Decimal("0.00"),
                "percent": Decimal("5.00"),
                "min_order_total": Decimal("100.00"),
                "milestone_order_count": 0,
                "max_redeem_share": Decimal("30.00"),
                "expires_in_days": 90,
                "config": {},
                "is_active": True,
            },
        )
        BonusProgram.objects.update_or_create(
            partner=partner,
            name="Walk-in cashback 5%",
            defaults={
                "trigger_event": BonusProgram.TriggerEvent.MANUAL_PURCHASE,
                "program_type": BonusProgram.ProgramType.CASHBACK,
                "strategy_code": "",
                "fixed_amount": Decimal("0.00"),
                "percent": Decimal("5.00"),
                "min_order_total": Decimal("100.00"),
                "milestone_order_count": 0,
                "max_redeem_share": Decimal("30.00"),
                "expires_in_days": 90,
                "config": {},
                "is_active": True,
            },
        )
        if include_milestone:
            BonusProgram.objects.update_or_create(
                partner=partner,
                name="Every 5th order bonus",
                defaults={
                    "trigger_event": BonusProgram.TriggerEvent.ORDER_COMPLETED,
                    "program_type": BonusProgram.ProgramType.MILESTONE,
                    "strategy_code": "",
                    "fixed_amount": Decimal("75.00"),
                    "percent": Decimal("0.00"),
                    "min_order_total": Decimal("0.00"),
                    "milestone_order_count": 5,
                    "max_redeem_share": Decimal("30.00"),
                    "expires_in_days": 60,
                    "config": {},
                    "is_active": True,
                },
            )

    def _print_partner_summary(self, partner: Partner) -> None:
        bot_instance = partner.bot_instances.order_by("created_at").first()
        table_count = partner.tables_tables.count()
        employee_count = partner.employees_employeeprofiles.count()
        owner_count = partner.employees_employeeprofiles.filter(user__role="owner").count()
        menu_item_count = partner.menu_menuitems.count()
        bot_username = bot_instance.username if bot_instance is not None else "n/a"
        print_line = (
            f"  - {partner.slug}: bot=@{bot_username}, tables={table_count}, "
            f"employees={employee_count}, owners={owner_count}, menu_items={menu_item_count}"
        )
        # Summary is intentionally compact so the command can be reused in QA scripts and CI logs.
        self.stdout.write(print_line)

    def _print_staff_summary(self, partner_slug: str, staff_rows: list[StaffSeed]) -> None:
        usernames = ", ".join(staff.username for staff in staff_rows)
        self.stdout.write(f"  - {partner_slug}: {usernames}")

    @staticmethod
    def _smoke_staff() -> list[StaffSeed]:
        return [
            StaffSeed(
                "smoke_owner_1",
                "owner1@nightowl.local",
                "Ivan",
                "Moroz",
                "owner",
                "Co-owner",
                Decimal("0.00"),
                Decimal("0.00"),
                700100001,
                "smoke_owner_1",
            ),
            StaffSeed(
                "smoke_owner_2",
                "owner2@nightowl.local",
                "Oleh",
                "Kravets",
                "owner",
                "Co-owner",
                Decimal("0.00"),
                Decimal("0.00"),
                700100002,
                "smoke_owner_2",
            ),
            StaffSeed(
                "smoke_owner_3",
                "owner3@nightowl.local",
                "Anna",
                "Shevchenko",
                "owner",
                "Co-owner",
                Decimal("0.00"),
                Decimal("0.00"),
                700100003,
                "smoke_owner_3",
            ),
            StaffSeed(
                "smoke_manager_1",
                "manager@nightowl.local",
                "Maksym",
                "Bondar",
                "manager",
                "General Manager",
                Decimal("220.00"),
                Decimal("2.50"),
                700100004,
                "smoke_manager_1",
            ),
            StaffSeed(
                "smoke_hookah_1",
                "hookah@nightowl.local",
                "Denys",
                "Tkachenko",
                "hookah_master",
                "Hookah Master",
                Decimal("180.00"),
                Decimal("5.00"),
                700100005,
                "smoke_hookah_1",
            ),
        ]

    @staticmethod
    def _cafe_staff() -> list[StaffSeed]:
        return [
            StaffSeed(
                "cafe_owner_1",
                "owner1@riverstone.local",
                "Svitlana",
                "Petrenko",
                "owner",
                "Co-owner",
                Decimal("0.00"),
                Decimal("0.00"),
                700200001,
                "cafe_owner_1",
            ),
            StaffSeed(
                "cafe_owner_2",
                "owner2@riverstone.local",
                "Taras",
                "Melnyk",
                "owner",
                "Co-owner",
                Decimal("0.00"),
                Decimal("0.00"),
                700200002,
                "cafe_owner_2",
            ),
            StaffSeed(
                "cafe_manager_1",
                "manager@riverstone.local",
                "Yuliia",
                "Hnatiuk",
                "manager",
                "Hall Manager",
                Decimal("230.00"),
                Decimal("2.00"),
                700200003,
                "cafe_manager_1",
            ),
            StaffSeed(
                "cafe_barman_1",
                "barman@riverstone.local",
                "Roman",
                "Lysenko",
                "cashier",
                "Bartender",
                Decimal("170.00"),
                Decimal("3.00"),
                700200004,
                "cafe_barman_1",
            ),
            StaffSeed(
                "cafe_waiter_1",
                "waiter1@riverstone.local",
                "Iryna",
                "Koval",
                "waiter",
                "Waiter",
                Decimal("145.00"),
                Decimal("4.00"),
                700200005,
                "cafe_waiter_1",
            ),
            StaffSeed(
                "cafe_waiter_2",
                "waiter2@riverstone.local",
                "Nazar",
                "Kushnir",
                "waiter",
                "Waiter",
                Decimal("145.00"),
                Decimal("4.00"),
                700200006,
                "cafe_waiter_2",
            ),
            StaffSeed(
                "cafe_waiter_3",
                "waiter3@riverstone.local",
                "Olha",
                "Romanenko",
                "waiter",
                "Waiter",
                Decimal("145.00"),
                Decimal("4.00"),
                700200007,
                "cafe_waiter_3",
            ),
            StaffSeed(
                "cafe_waiter_4",
                "waiter4@riverstone.local",
                "Artem",
                "Savchuk",
                "waiter",
                "Waiter",
                Decimal("145.00"),
                Decimal("4.00"),
                700200008,
                "cafe_waiter_4",
            ),
        ]

    @staticmethod
    def _smoke_tables() -> list[TableSeed]:
        return [
            TableSeed(1, "Table 1", 4, "nightowl_tbl_01"),
            TableSeed(2, "Table 2", 4, "nightowl_tbl_02"),
            TableSeed(3, "Table 3", 4, "nightowl_tbl_03"),
            TableSeed(4, "Table 4", 4, "nightowl_tbl_04"),
            TableSeed(5, "Table 5", 6, "nightowl_tbl_05"),
            TableSeed(6, "Table 6", 6, "nightowl_tbl_06"),
            TableSeed(7, "Table 7", 8, "nightowl_tbl_07"),
            TableSeed(8, "Bar", 6, "nightowl_bar_08"),
        ]

    @staticmethod
    def _cafe_tables() -> list[TableSeed]:
        return [
            TableSeed(1, "Table 1", 2, "river_tbl_01"),
            TableSeed(2, "Table 2", 2, "river_tbl_02"),
            TableSeed(3, "Table 3", 2, "river_tbl_03"),
            TableSeed(4, "Table 4", 4, "river_tbl_04"),
            TableSeed(5, "Table 5", 4, "river_tbl_05"),
            TableSeed(6, "Table 6", 4, "river_tbl_06"),
            TableSeed(7, "Table 7", 4, "river_tbl_07"),
            TableSeed(8, "Table 8", 4, "river_tbl_08"),
            TableSeed(9, "Table 9", 6, "river_tbl_09"),
            TableSeed(10, "Table 10", 6, "river_tbl_10"),
            TableSeed(11, "Table 11", 6, "river_tbl_11"),
            TableSeed(12, "Table 12", 8, "river_tbl_12"),
        ]
