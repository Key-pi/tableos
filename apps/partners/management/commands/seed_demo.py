from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.bonuses.models import BonusProgram
from apps.employees.models import EmployeeProfile
from apps.menu.models import MenuCategory, MenuItem
from apps.partners.models import BotInstance, Partner, PartnerBotSettings
from apps.tables.models import Table
from apps.users.models import TelegramAccount


class Command(BaseCommand):
    help = "Seed a demo partner with tables and menu items for local development."

    def add_arguments(self, parser):
        parser.add_argument("--slug", default="demo-lounge")
        parser.add_argument("--name", default="Demo Lounge")
        parser.add_argument("--bot-username", default="")
        parser.add_argument("--bot-token", default="")
        parser.add_argument("--with-admin", action="store_true")
        parser.add_argument("--admin-username", default="admin")
        parser.add_argument("--admin-email", default="admin@tableos.local")
        parser.add_argument("--admin-password", default="admin12345")
        parser.add_argument("--with-manager", action="store_true")
        parser.add_argument("--manager-username", default="demo_manager")
        parser.add_argument("--manager-email", default="manager@tableos.local")
        parser.add_argument("--manager-password", default="demo12345")
        parser.add_argument("--manager-telegram-id", type=int, default=0)
        parser.add_argument("--manager-telegram-username", default="")

    def handle(self, *args, **options):
        slug = options["slug"].strip()
        name = options["name"].strip()
        bot_username = options["bot_username"].strip().removeprefix("@")
        bot_token = options["bot_token"].strip()
        if not slug:
            raise CommandError("`--slug` must not be empty.")
        if not name:
            raise CommandError("`--name` must not be empty.")
        if bot_token and not bot_username:
            raise CommandError("`--bot-username` is required when `--bot-token` is provided.")

        partner, created = Partner.objects.get_or_create(
            slug=slug,
            defaults={
                "name": name,
                "status": Partner.Status.ACTIVE,
            },
        )
        if not created:
            partner.name = name
            partner.status = Partner.Status.ACTIVE
            partner.save(update_fields=["name", "status", "updated_at"])

        demo_tables: list[Table] = []
        for table_number in range(1, 6):
            Table.objects.get_or_create(
                partner=partner,
                number=table_number,
                defaults={"name": f"Table {table_number}", "seats": 4},
            )
        demo_tables = list(Table.objects.filter(partner=partner).order_by("number"))

        settings, _ = PartnerBotSettings.objects.get_or_create(partner=partner)
        settings.allow_menu_without_session = True
        settings.module_loyalty_enabled = True
        settings.module_menu_enabled = True
        settings.module_tables_enabled = True
        settings.module_cart_enabled = True
        settings.module_orders_enabled = True
        settings.button_my_profile = "Мой профиль"
        settings.save(
            update_fields=[
                "allow_menu_without_session",
                "module_loyalty_enabled",
                "module_menu_enabled",
                "module_tables_enabled",
                "module_cart_enabled",
                "module_orders_enabled",
                "button_my_profile",
                "updated_at",
            ]
        )

        drinks_category, _ = MenuCategory.objects.get_or_create(
            partner=partner,
            name="Напитки",
            defaults={"sort_order": 10},
        )
        hookah_category, _ = MenuCategory.objects.get_or_create(
            partner=partner,
            name="Кальяны",
            defaults={"sort_order": 20},
        )

        demo_items = [
            self._upsert_item(partner, drinks_category, "Лимонад", "150.00", 10),
            self._upsert_item(partner, drinks_category, "Кофе", "90.00", 20),
            self._upsert_item(partner, hookah_category, "Кальян Classic", "450.00", 10),
            self._upsert_item(partner, hookah_category, "Кальян Premium", "650.00", 20),
        ]
        self._ensure_demo_bonus_programs(partner)

        admin_user = None
        if options["with_admin"]:
            admin_user = self._ensure_admin_user(
                username=options["admin_username"].strip(),
                email=options["admin_email"].strip(),
                password=options["admin_password"],
            )

        manager_user = None
        if options["with_manager"]:
            manager_user = self._ensure_manager_user(
                partner=partner,
                username=options["manager_username"].strip(),
                email=options["manager_email"].strip(),
                password=options["manager_password"],
                telegram_id=options["manager_telegram_id"],
                telegram_username=options["manager_telegram_username"].strip(),
            )

        active_bot_username = bot_username
        if bot_username:
            if bot_token:
                bot_instance, _ = BotInstance.objects.get_or_create(
                    partner=partner,
                    username=bot_username,
                    defaults={
                        "display_name": f"{name} Bot",
                        "mode": BotInstance.Mode.POLLING,
                        "is_active": True,
                    },
                )
                bot_instance.display_name = f"{name} Bot"
                bot_instance.is_active = True
                bot_instance.set_token(bot_token)
                bot_instance.save()
            else:
                self.stdout.write(
                    self.style.WARNING(
                        "Bot token was not provided: Telegram links are ready for preview, "
                        "but BotInstance was not created."
                    )
                )
        else:
            active_instance = (
                partner.bot_instances.filter(is_active=True).order_by("created_at").first()
            )
            if active_instance is not None:
                active_bot_username = active_instance.username

        self.stdout.write(self.style.SUCCESS(f"Demo partner `{partner.slug}` is ready."))
        self.stdout.write(f"Tables: {len(demo_tables)}")
        self.stdout.write("Menu items:")
        for item in demo_items:
            self.stdout.write(f"  - #{item.public_id} {item.name}: {item.price} грн")

        if active_bot_username:
            self.stdout.write("Telegram start links:")
            for table in demo_tables:
                self.stdout.write(
                    f"  - table #{table.number}: {table.telegram_start_url(active_bot_username)}"
                )
        else:
            self.stdout.write(
                self.style.WARNING(
                    "No active bot username is available yet, so QR deep links were not printed."
                )
            )
            self.stdout.write("Table payloads:")
            for table in demo_tables:
                self.stdout.write(f"  - table #{table.number}: {table.deep_link_payload}")

        self.stdout.write("Suggested guest flow:")
        self.stdout.write("  - open the bot and scan a table QR link")
        self.stdout.write("  - open menu categories and add items with buttons")
        self.stdout.write("  - submit the order from the cart")

        if admin_user is not None:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Admin ready: {admin_user.username} / {options['admin_password']}"
                )
            )

        if manager_user is not None:
            manager_line = f"Manager ready: {manager_user.username} / {options['manager_password']}"
            if options["manager_telegram_id"]:
                manager_line += f" (telegram_id={options['manager_telegram_id']})"
            self.stdout.write(self.style.SUCCESS(manager_line))

    @staticmethod
    def _upsert_item(
        partner: Partner,
        category: MenuCategory,
        name: str,
        price: str,
        sort_order: int,
    ) -> MenuItem:
        item, created = MenuItem.objects.get_or_create(
            partner=partner,
            category=category,
            name=name,
            defaults={"price": price, "sort_order": sort_order},
        )
        if not created:
            item.price = price
            item.sort_order = sort_order
            item.is_available = True
            item.save(update_fields=["price", "sort_order", "is_available", "updated_at"])
        return item

    @staticmethod
    def _ensure_admin_user(*, username: str, email: str, password: str):
        user_model = get_user_model()
        user, _ = user_model.objects.get_or_create(
            username=username,
            defaults={
                "email": email,
                "is_staff": True,
                "is_superuser": True,
                "role": user_model.Role.ADMIN,
            },
        )
        user.email = email
        user.is_staff = True
        user.is_superuser = True
        user.role = user_model.Role.ADMIN
        user.set_password(password)
        user.save()
        return user

    @staticmethod
    def _ensure_manager_user(
        *,
        partner: Partner,
        username: str,
        email: str,
        password: str,
        telegram_id: int,
        telegram_username: str,
    ):
        user_model = get_user_model()
        user, _ = user_model.objects.get_or_create(
            username=username,
            defaults={
                "email": email,
                "partner": partner,
                "role": user_model.Role.MANAGER,
                "is_staff": True,
            },
        )
        user.email = email
        user.partner = partner
        user.role = user_model.Role.MANAGER
        user.is_staff = True
        user.set_password(password)
        user.save()

        employee_profile, _ = EmployeeProfile.objects.get_or_create(
            partner=partner,
            user=user,
            defaults={"title": "Manager"},
        )
        employee_profile.title = employee_profile.title or "Manager"

        if telegram_id > 0:
            telegram_account, _ = TelegramAccount.objects.get_or_create(
                telegram_id=telegram_id,
                defaults={"username": telegram_username},
            )
            if telegram_username:
                telegram_account.username = telegram_username
                telegram_account.save(update_fields=["username", "updated_at"])
            employee_profile.telegram_account = telegram_account

        employee_profile.save()
        return user

    @staticmethod
    def _ensure_demo_bonus_programs(partner: Partner) -> None:
        BonusProgram.objects.update_or_create(
            partner=partner,
            name="Cashback 5%",
            defaults={
                "trigger_event": BonusProgram.TriggerEvent.ORDER_COMPLETED,
                "program_type": BonusProgram.ProgramType.CASHBACK,
                "percent": "5.00",
                "min_order_total": "100.00",
                "expires_in_days": 90,
                "is_active": True,
            },
        )
        BonusProgram.objects.update_or_create(
            partner=partner,
            name="Walk-in cashback 5%",
            defaults={
                "trigger_event": BonusProgram.TriggerEvent.MANUAL_PURCHASE,
                "program_type": BonusProgram.ProgramType.CASHBACK,
                "percent": "5.00",
                "min_order_total": "100.00",
                "expires_in_days": 90,
                "is_active": True,
            },
        )
        BonusProgram.objects.update_or_create(
            partner=partner,
            name="Visit reward",
            defaults={
                "trigger_event": BonusProgram.TriggerEvent.VISIT,
                "program_type": BonusProgram.ProgramType.VISIT,
                "fixed_amount": "20.00",
                "expires_in_days": 30,
                "config": {"once_per_day": True},
                "is_active": True,
            },
        )
