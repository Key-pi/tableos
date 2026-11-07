"""Demo data used by Docker and for manual testing (`seed_baseline_demo`).

Four venues set up differently (owner, 2026-11-07): Night Owl · Поділ with
everything on, Night Owl · Оболонь in its network, Riverstone Cafe and the
plant shop «Зелена полиця»."""

import re
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from apps.analytics.models import TrafficSource
from apps.bonuses.models import BonusProgram, BonusTransaction
from apps.bonuses.services import get_bonus_ledger_balance
from apps.employees.models import EmployeeProfile
from apps.menu.models import MenuCategory, MenuItem, MenuItemVariant, ModifierGroup
from apps.menu.options import choices_for, needs_choice
from apps.orders.kitchen import screen_by_token
from apps.orders.models import KitchenScreen
from apps.partners.models import BotInstance, Network, OpeningHours, Partner, PartnerBotSettings
from apps.tables.models import Table
from apps.users.models import GuestProfile, TelegramAccount, User
from bot.services.runtime import load_active_bot_configs

CAFE_TOKEN = "111111111:AAEcafecafecafecafecafecafecafe"
HOOKAH_TOKEN = "222222222:AAEhookahhookahhookahhookahhook"
SHOP_TOKEN = "333333333:AAEshopshopshopshopshopshopshops"
TESTER_ID = 555000111
PODIL, OBOLON, CAFE, SHOP = (
    "night-owl-hookah", "night-owl-obolon", "riverstone-cafe", "green-shelf-plants"
)


def _seed(**options) -> str:
    out = StringIO()
    defaults = {
        "hookah_bot_token": "",
        "hookah_bot_username": "",
        "cafe_bot_token": "",
        "cafe_bot_username": "",
        "shop_bot_token": "",
        "shop_bot_username": "",
        "telegram_id": "",
        "telegram_username": "",
    }
    defaults.update(options)
    call_command("seed_baseline_demo", stdout=out, **defaults)
    return out.getvalue()


def _settings(slug) -> PartnerBotSettings:
    return PartnerBotSettings.objects.get(partner__slug=slug)


# The seed hashes a password for every demo user; a fast hasher keeps this quick.
@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class SeedBaselineDemoTests(TestCase):
    def test_demo_menu_is_ukrainian_and_old_names_are_renamed_in_place(self):
        _seed()
        cafe = Partner.objects.get(slug=CAFE)
        MenuCategory.objects.filter(partner=cafe, name="Сніданки").update(name="Завтраки")
        BonusProgram.objects.filter(partner=cafe, name="Кешбек 5%").update(name="Cashback 5%")

        _seed()

        self.assertFalse(MenuCategory.objects.filter(partner=cafe, name="Завтраки").exists())
        breakfast = MenuCategory.objects.get(partner=cafe, name="Сніданки")
        self.assertIn("Сирники", breakfast.items.values_list("name", flat=True))
        self.assertEqual(
            BonusProgram.objects.filter(partner=cafe, name__in=["Кешбек 5%", "Cashback 5%"])
            .values_list("name", flat=True)
            .get(),
            "Кешбек 5%",
        )

    def test_four_venues_set_up_differently(self):
        output = _seed()

        self.assertEqual(
            set(Partner.objects.values_list("slug", flat=True)), {PODIL, OBOLON, CAFE, SHOP}
        )
        for slug in (PODIL, OBOLON, CAFE, SHOP):
            _settings(slug).full_clean()
        # Night Owl · Поділ: everything on.
        podil = _settings(PODIL)
        modules = [f.name for f in podil._meta.concrete_fields if f.name.startswith("module_")]
        self.assertEqual([name for name in modules if not getattr(podil, name)], [])
        self.assertTrue(
            podil.staff_call_waiter_enabled and podil.staff_call_bartender_enabled
            and podil.staff_call_hookah_enabled
        )
        # The cafe: tables and delivery, no post, staff in the bot, no Russian.
        cafe = _settings(CAFE)
        self.assertTrue(cafe.module_tables_enabled and cafe.module_delivery_enabled)
        self.assertFalse(cafe.module_shipping_enabled or cafe.module_staff_screen_enabled)
        self.assertEqual((cafe.language_ru, cafe.language_en), (False, True))
        lunch = MenuCategory.objects.get(partner__slug=CAFE, name="Бізнес-ланч")
        self.assertEqual(lunch.available_weekdays, [0, 1, 2, 3, 4])
        # The shop: no tables, kitchen or calls; pickup, courier and post.
        shop = _settings(SHOP)
        self.assertFalse(
            shop.module_tables_enabled or shop.module_kitchen_screen_enabled
            or shop.module_staff_call_enabled
        )
        self.assertTrue(shop.module_shipping_enabled and shop.module_quick_sale_enabled)
        self.assertFalse(Table.objects.filter(partner__slug=SHOP).exists())
        self.assertFalse(OpeningHours.objects.filter(partner__slug=SHOP, scope="").exists())
        self.assertEqual(OpeningHours.objects.filter(partner__slug=PODIL).count(), 14)
        self.assertIn(
            "доставка: ежедневно 10:00–22:00, 60 грн, бесплатно от 800 грн", output
        )
        # Bots: none runnable yet; the second point has none of its own.
        for slug in (PODIL, CAFE, SHOP):
            bot = BotInstance.objects.get(partner__slug=slug)
            self.assertFalse(bot.is_active)
            self.assertEqual(bot.token_status_label, "placeholder")
        self.assertFalse(BotInstance.objects.filter(partner__slug=OBOLON).exists())
        self.assertEqual(load_active_bot_configs(), [])
        self.assertTrue(User.objects.get(username="platform_admin").is_superuser)
        for env_name in ("DEMO_CAFE_BOT_TOKEN", "DEMO_SHOP_BOT_TOKEN", "DEMO_TELEGRAM_ID"):
            self.assertIn(env_name, output)

    def test_the_night_owl_network(self):
        _seed()
        network = Network.objects.get()
        self.assertEqual(
            set(network.points.values_list("slug", flat=True)), {PODIL, OBOLON}
        )
        self.assertEqual(network.bonus_mode, Network.BonusMode.SHARED)
        self.assertEqual(network.bot.partner.slug, PODIL)
        # The network's owners and its manager work in the second point too.
        copies = EmployeeProfile.objects.filter(
            partner__slug=OBOLON, mirrored_from__isnull=False, is_active=True
        )
        self.assertEqual(
            sorted(copies.values_list("mirrored_from__user__username", flat=True)),
            ["smoke_manager_1", "smoke_owner_1", "smoke_owner_2"],
        )
        self.assertTrue(
            TrafficSource.objects.get(partner__slug=PODIL, code="nightowl").whole_network
        )
        # Its own menu; the kitchen part has no screen there.
        self.assertTrue(MenuItem.objects.filter(partner__slug=OBOLON, public_id="OBLHK001"))
        self.assertEqual(
            list(KitchenScreen.objects.filter(partner__slug=OBOLON).values_list("shows",
                                                                               flat=True)),
            ["bar"],
        )

    def test_the_catalogue_shows_every_step(self):
        _seed()
        # Subcategories and a schedule past midnight.
        mixes = MenuCategory.objects.get(partner__slug=PODIL, name="Фірмові мікси")
        self.assertEqual(mixes.parent.name, "Кальяни")
        alcohol = MenuCategory.objects.get(partner__slug=PODIL, name="Алкоголь")
        self.assertGreater(alcohol.available_from, alcohol.available_until)
        # Variants and add-ons, through a category and its parent.
        zz = MenuItem.objects.select_related("category__parent").get(public_id="PLTEZ001")
        choices = choices_for(zz)
        self.assertEqual(len(choices.variants), 3)
        self.assertEqual(
            [entry.group.name for entry in choices.groups], ["Кашпо", "Пересадка", "Подарунок"]
        )
        steak = MenuItem.objects.select_related("category").get(public_id="CAFMN004")
        self.assertTrue(needs_choice(steak))  # doneness is required
        # The shop's showcase: SKU and barcode everywhere, large plants not by post.
        shop_items = MenuItem.objects.filter(partner__slug=SHOP)
        self.assertFalse(shop_items.filter(sku="").exists() or shop_items.filter(barcode=""))
        self.assertFalse(MenuItem.objects.get(public_id="PLTBG001").available_for_shipping)
        self.assertTrue(MenuItem.objects.get(public_id="PLTEZ001").available_for_shipping)
        for item in MenuItem.objects.all():
            item.full_clean()

    def test_rerun_is_idempotent(self):
        _seed()
        counts = (
            MenuItemVariant.objects.count(), ModifierGroup.objects.count(),
            KitchenScreen.objects.count(), EmployeeProfile.objects.count(),
        )
        _seed()
        cafe = Partner.objects.get(slug=CAFE)
        self.assertEqual(Table.objects.filter(partner=cafe).count(), 12)
        self.assertEqual(EmployeeProfile.objects.filter(partner=cafe).count(), 8)
        self.assertEqual(BotInstance.objects.filter(partner=cafe).count(), 1)
        self.assertEqual(Network.objects.count(), 1)
        self.assertEqual(
            (
                MenuItemVariant.objects.count(), ModifierGroup.objects.count(),
                KitchenScreen.objects.count(), EmployeeProfile.objects.count(),
            ),
            counts,
        )

    def test_kitchen_screen_links_are_shown_once_and_work(self):
        output = _seed()
        tokens = re.findall(r"/app/screen/#(\S+)", output)
        self.assertEqual(len(tokens), KitchenScreen.objects.count())
        self.assertTrue(all(screen_by_token(token) for token in tokens))
        output = _seed()
        self.assertNotIn("/app/screen/#", output)
        self.assertIn("ссылка выдана раньше", output)

    def test_tokens_and_tester_make_everything_testable(self):
        output = _seed(
            cafe_bot_token=CAFE_TOKEN,
            cafe_bot_username="@my_cafe_test_bot",
            hookah_bot_token=HOOKAH_TOKEN,
            hookah_bot_username="my_hookah_test_bot",
            shop_bot_token=SHOP_TOKEN,
            shop_bot_username="my_shop_test_bot",
            telegram_id=str(TESTER_ID),
            telegram_username="@tester",
        )

        configs = load_active_bot_configs()
        self.assertEqual(
            sorted(config.username for config in configs),
            ["my_cafe_test_bot", "my_hookah_test_bot", "my_shop_test_bot"],
        )
        tester = TelegramAccount.objects.get(telegram_id=TESTER_ID)
        self.assertEqual(tester.username, "tester")
        for slug, owner in ((CAFE, "cafe_owner_1"), (PODIL, "smoke_owner_1"),
                            (SHOP, "plant_owner_1")):
            employee = EmployeeProfile.objects.get(partner__slug=slug, user__username=owner)
            self.assertEqual(employee.telegram_account, tester)
            self.assertTrue(employee.notify_on_order_created)
            guest = GuestProfile.objects.get(partner__slug=slug, telegram_account=tester)
            self.assertEqual(
                get_bonus_ledger_balance(partner_id=guest.partner_id, guest_id=guest.id),
                Decimal("500.00"),
            )
        # In the second point the tester works as the network's owner.
        copy = EmployeeProfile.objects.get(partner__slug=OBOLON, telegram_account=tester)
        self.assertEqual(copy.mirrored_from.user.username, "smoke_owner_1")
        self.assertTrue(copy.is_active)
        self.assertIn("https://t.me/my_cafe_test_bot?start=table_1_river_tbl_01", output)
        self.assertIn("https://t.me/my_cafe_test_bot?start=src_instagram", output)
        # The second point's tables open the network's bot.
        self.assertIn("https://t.me/my_hookah_test_bot?start=table_1_obolon_tbl_01", output)

        # Rerun: no second credit, binding kept.
        _seed(
            cafe_bot_token=CAFE_TOKEN,
            cafe_bot_username="my_cafe_test_bot",
            telegram_id=str(TESTER_ID),
        )
        self.assertEqual(BonusTransaction.objects.filter(guest__telegram_account=tester).count(), 3)
        self.assertEqual(
            EmployeeProfile.objects.get(partner__slug=CAFE, user__username="cafe_owner_1")
            .telegram_account,
            tester,
        )
        # A real token is not dropped when it is no longer passed.
        self.assertTrue(BotInstance.objects.get(username="my_hookah_test_bot").is_active)

    def test_bot_username_is_looked_up_when_not_given(self):
        with patch(
            "apps.partners.management.commands.seed_baseline_demo.fetch_bot_username",
            return_value="looked_up_bot",
        ) as lookup:
            _seed(cafe_bot_token=CAFE_TOKEN)
        lookup.assert_called_once_with(CAFE_TOKEN)
        self.assertTrue(BotInstance.objects.get(username="looked_up_bot").is_active)

    def test_keep_existing_preserves_admin_changes_but_applies_bots(self):
        _seed()
        item = MenuItem.objects.get(public_id="CAFBR001")
        item.price = Decimal("999.00")
        item.save()
        admin = User.objects.get(username="platform_admin")
        admin.set_password("changed-in-admin")
        admin.save()

        _seed(keep_existing=True, cafe_bot_token=CAFE_TOKEN, cafe_bot_username="kept_cafe_bot")

        item.refresh_from_db()
        self.assertEqual(item.price, Decimal("999.00"))
        admin.refresh_from_db()
        self.assertTrue(admin.check_password("changed-in-admin"))
        self.assertTrue(BotInstance.objects.get(username="kept_cafe_bot").is_active)

    def test_keep_existing_on_empty_database_seeds_everything(self):
        _seed(keep_existing=True)
        self.assertEqual(Partner.objects.count(), 4)
        self.assertTrue(MenuItem.objects.filter(public_id="CAFBR001").exists())
        self.assertEqual(Network.objects.get().points.count(), 2)

    def test_keep_existing_adds_a_missing_venue_and_its_network(self):
        # A database seeded before the shop and the second point existed:
        # Docker's start adds them and makes the network.
        _seed()
        Partner.objects.filter(slug__in=[OBOLON, SHOP]).delete()
        Partner.objects.filter(slug=PODIL).update(network=None)
        Network.objects.all().delete()

        output = _seed(keep_existing=True)

        self.assertIn(f"({OBOLON}, создано)", output)
        self.assertIn(f"({SHOP}, создано)", output)
        self.assertIn(f"({PODIL}, уже было)", output)
        network = Network.objects.get()
        self.assertEqual(set(network.points.values_list("slug", flat=True)), {PODIL, OBOLON})
        self.assertEqual(network.bot.partner.slug, PODIL)

    def test_same_token_for_two_venues_is_refused_before_any_change(self):
        with self.assertRaisesMessage(CommandError, "Один и тот же токен"):
            _seed(
                cafe_bot_token=CAFE_TOKEN,
                cafe_bot_username="a_cafe_bot",
                shop_bot_token=CAFE_TOKEN,
                shop_bot_username="a_shop_bot",
            )
        self.assertFalse(Partner.objects.exists())

    def test_invalid_telegram_id_is_refused(self):
        with self.assertRaisesMessage(CommandError, "DEMO_TELEGRAM_ID"):
            _seed(telegram_id="@tester")

    def test_tester_already_bound_to_another_employee_keeps_that_binding(self):
        _seed()
        tester = TelegramAccount.objects.create(telegram_id=TESTER_ID)
        waiter = EmployeeProfile.objects.get(user__username="cafe_waiter_1")
        waiter.telegram_account = tester
        waiter.save()

        output = _seed(keep_existing=True, telegram_id=str(TESTER_ID))

        waiter.refresh_from_db()
        self.assertEqual(waiter.telegram_account, tester)
        self.assertNotEqual(
            EmployeeProfile.objects.get(user__username="cafe_owner_1").telegram_account, tester
        )
        self.assertIn("cafe_waiter_1", output)
