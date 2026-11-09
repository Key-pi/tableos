"""Admin menu by the venue's tasks (admin step 1, owner 2026-11-09).

The owner asked for comfortable navigation on a tablet: pages grouped by
what a venue does, «+» next to pages one adds to, service tables out of the
menu (they open as tabs of their page), the venue's own card opening as a
form, and no «Заведение» column in a venue's own lists."""

from django.test import TestCase
from django.urls import reverse

from apps.orders.models import Order
from apps.partners.models import Network, Partner, PartnerBotSettings
from apps.users.constants import AdminAccessPreset
from apps.users.models import AdminAccessProfile, GuestProfile, TelegramAccount, User
from tests.webapp.test_guest_api import make_venue


def admin_user(partner, username, preset, *, owner=False):
    user = User.objects.create_user(
        username=username, password="x", partner=partner,
        role=User.Role.OWNER if owner else User.Role.CASHIER, is_staff=True, language="ru",
    )
    AdminAccessProfile.objects.create(
        user=user, partner=partner, preset=preset, is_partner_owner=owner,
        can_access_admin=True,
    )
    return user


def order(partner, status, telegram_id):
    account = TelegramAccount.objects.create(telegram_id=telegram_id)
    guest = GuestProfile.objects.create(partner=partner, telegram_account=account)
    return Order.objects.create(
        partner=partner, guest=guest, status=status, subtotal_amount="80.00",
        total_amount="80.00",
    )


def modules(partner, **flags):
    PartnerBotSettings.objects.filter(partner=partner).update(**flags)


def url(name, *args):
    return reverse(f"admin:{name}", args=args)


class NavigationFixture(TestCase):
    def setUp(self):
        self.partner = make_venue("Kava")
        self.owner = admin_user(self.partner, "nav_owner", AdminAccessPreset.OWNER, owner=True)
        self.client.force_login(self.owner)

    def sidebar(self, path=None):
        response = self.client.get(path or url("index"))
        self.assertEqual(response.status_code, 200)
        return response, response.context["sidebar_navigation"]

    @staticmethod
    def groups(navigation) -> dict[str, list[dict]]:
        return {str(group.get("title", "")): group["items"] for group in navigation}

    def links(self, navigation) -> list[str]:
        return [item["link"] for group in navigation for item in group["items"]]


class OwnerMenuTests(NavigationFixture):
    def test_groups_follow_the_venues_tasks(self):
        _response, navigation = self.sidebar()
        self.assertEqual(
            list(self.groups(navigation)),
            ["", "Заказы и счета", "Меню", "Гости и бонусы", "Маркетинг", "Персонал",
             "Отчёты", "Настройки"],
        )
        first = navigation[0]["items"][0]
        self.assertEqual(str(first["title"]), "Сегодня")
        self.assertEqual(first["link"], url("index"))

    def test_service_tables_are_not_in_the_menu(self):
        _response, navigation = self.sidebar()
        links = self.links(navigation)
        for name in (
            "orders_orderitem", "orders_cart", "orders_cartitem", "orders_orderstatushistory",
            "billing_billitem", "billing_billorder", "billing_billadjustment",
            "billing_billingrequest", "tables_tablesession", "tables_tablevisit",
            "notifications_broadcastdelivery", "notifications_staffnotification",
            "notifications_notificationpreference", "users_telegramaccount", "auth_group",
        ):
            with self.subTest(name):
                self.assertNotIn(url(f"{name}_changelist"), links)
        self.assertIn(url("orders_order_changelist"), links)
        self.assertIn(url("billing_bill_changelist"), links)

    def test_service_tables_stay_out_of_the_index_but_open_by_url(self):
        response = self.client.get(url("index"))
        models = {
            model["object_name"] for app in response.context["app_list"]
            for model in app["models"]
        }
        self.assertNotIn("OrderItem", models)
        self.assertNotIn("BillItem", models)
        self.assertIn("Order", models)
        self.assertEqual(self.client.get(url("orders_orderitem_changelist")).status_code, 200)
        self.assertEqual(self.client.get(url("billing_billitem_changelist")).status_code, 200)

    def test_plus_next_to_pages_one_adds_to(self):
        _response, navigation = self.sidebar()
        items = {item["link"]: item for group in navigation for item in group["items"]}
        self.assertEqual(
            items[url("menu_menuitem_changelist")].get("add_link"), url("menu_menuitem_add")
        )
        self.assertEqual(
            items[url("bonuses_bonusprogram_changelist")].get("add_link"),
            url("bonuses_bonusprogram_add"),
        )
        # Orders come from guests; nobody adds them here.
        self.assertNotIn("add_link", items[url("orders_order_changelist")])

    def test_plus_is_drawn_next_to_the_item(self):
        response, _navigation = self.sidebar()
        html = response.content.decode()
        self.assertIn(f'href="{url("menu_menuitem_add")}" class="sidebar-add-link', html)
        self.assertIn('aria-label="Добавить: Позиции меню"', html)

    def test_module_pages_follow_the_modules(self):
        modules(self.partner, module_options_enabled=False, module_showcase_enabled=False,
                module_stock_enabled=False, module_quick_sale_enabled=False,
                module_kitchen_screen_enabled=False, module_tables_enabled=False)
        _response, navigation = self.sidebar()
        links = self.links(navigation)
        for name in ("menu_modifiergroup", "menu_menubadge", "menu_stockmovement",
                     "bonuses_walkinsale", "orders_kitchenscreen", "tables_table"):
            with self.subTest(name, on=False):
                self.assertNotIn(url(f"{name}_changelist"), links)

        modules(self.partner, module_options_enabled=True, module_showcase_enabled=True,
                module_stock_enabled=True, module_quick_sale_enabled=True,
                module_kitchen_screen_enabled=True, module_tables_enabled=True)
        _response, navigation = self.sidebar()
        links = self.links(navigation)
        for name in ("menu_modifiergroup", "menu_menubadge", "menu_stockmovement",
                     "bonuses_walkinsale", "orders_kitchenscreen", "tables_table"):
            with self.subTest(name, on=True):
                self.assertIn(url(f"{name}_changelist"), links)

    def test_venue_card_and_bot_settings_open_as_forms(self):
        settings = PartnerBotSettings.objects.get(partner=self.partner)
        _response, navigation = self.sidebar()
        links = self.links(navigation)
        self.assertIn(url("partners_partner_change", self.partner.pk), links)
        self.assertIn(url("partners_partnerbotsettings_change", settings.pk), links)

        response = self.client.get(url("partners_partner_changelist"))
        self.assertRedirects(response, url("partners_partner_change", self.partner.pk))
        response = self.client.get(url("partners_partnerbotsettings_changelist"))
        self.assertRedirects(response, url("partners_partnerbotsettings_change", settings.pk))

    def test_no_venue_column_in_the_venues_own_lists(self):
        for name in ("orders_order", "menu_menuitem", "billing_bill", "users_guestprofile"):
            with self.subTest(name):
                response = self.client.get(url(f"{name}_changelist"))
                self.assertNotIn("partner", response.context["cl"].list_display)

    def test_one_page_is_lit_up_also_on_its_tabs(self):
        _response, navigation = self.sidebar(url("menu_menucategory_changelist"))
        active = [item["link"] for group in navigation for item in group["items"]
                  if item["active"]]
        self.assertEqual(active, [url("menu_menucategory_changelist")])

        # A service table lights up the page it is a tab of.
        _response, navigation = self.sidebar(url("billing_billitem_changelist"))
        active = [item["link"] for group in navigation for item in group["items"]
                  if item["active"]]
        self.assertEqual(active, [url("billing_bill_changelist")])

    def test_related_pages_are_tabs(self):
        response = self.client.get(url("billing_bill_changelist"))
        html = response.content.decode()
        for name in ("billing_billingrequest", "billing_billitem", "billing_billadjustment"):
            with self.subTest(name):
                self.assertIn(f'href="{url(f"{name}_changelist")}"', html)

    def test_counters_next_to_orders_and_bills(self):
        order(self.partner, Order.Status.NEW, 9001)
        order(self.partner, Order.Status.NEW, 9002)
        order(self.partner, Order.Status.ACCEPTED, 9003)
        order(make_venue("Other"), Order.Status.NEW, 9004)
        response, navigation = self.sidebar()
        items = {item["link"]: item for group in navigation for item in group["items"]}
        self.assertEqual(str(items[url("orders_order_changelist")]["badge_callback"]), "2")
        self.assertEqual(str(items[url("billing_bill_changelist")]["badge_callback"]), "")

    def test_no_all_applications_for_a_venue(self):
        response = self.client.get(url("index"))
        self.assertFalse(response.context["sidebar_show_all_applications"])


class CashierMenuTests(NavigationFixture):
    def setUp(self):
        super().setUp()
        modules(self.partner, module_quick_sale_enabled=True)
        self.cashier = admin_user(self.partner, "nav_cashier", AdminAccessPreset.CASHIER)
        self.client.force_login(self.cashier)

    def test_only_pages_of_their_rights(self):
        _response, navigation = self.sidebar()
        self.assertEqual(list(self.groups(navigation)), ["", "Заказы и счета", "Гости и бонусы"])
        items = {item["link"]: item for group in navigation for item in group["items"]}
        self.assertEqual(
            items[url("bonuses_walkinsale_changelist")].get("add_link"),
            url("bonuses_walkinsale_add"),
        )
        # «+» follows the right to add: orders are only changed.
        self.assertNotIn("add_link", items[url("orders_order_changelist")])
        self.assertNotIn(url("menu_menuitem_changelist"), items)
        self.assertNotIn(url("tables_table_changelist"), items)

    def test_tabs_show_only_pages_they_may_open(self):
        response = self.client.get(url("users_guestprofile_changelist"))
        html = response.content.decode()
        self.assertIn(f'href="{url("bonuses_walkinsale_changelist")}"', html)
        self.assertNotIn(f'href="{url("notifications_notificationpreference_changelist")}"', html)


class ContentManagerMenuTests(NavigationFixture):
    def test_plus_only_where_they_may_add(self):
        manager = admin_user(self.partner, "nav_content", AdminAccessPreset.CONTENT_MANAGER)
        self.client.force_login(manager)
        _response, navigation = self.sidebar()
        items = {item["link"]: item for group in navigation for item in group["items"]}
        self.assertIn("add_link", items[url("menu_menuitem_changelist")])
        # Bot texts: they change them, but adding is not theirs.
        self.assertIn(url("partners_bottext_changelist"), items)
        self.assertNotIn("add_link", items[url("partners_bottext_changelist")])


class NetworkMenuTests(TestCase):
    def test_network_pages_for_the_networks_owner_only(self):
        network = Network.objects.create(name="Ночь")
        first = make_venue("Night One", network=network)
        make_venue("Night Two", network=network)
        owner = admin_user(first, "net_owner", AdminAccessPreset.OWNER, owner=True)
        self.client.force_login(owner)
        navigation = self.client.get(url("index")).context["sidebar_navigation"]
        links = [item["link"] for group in navigation for item in group["items"]]
        self.assertIn(url("partners_network_change", network.pk), links)
        self.assertIn(url("partners_network_changelist"), links)

        # A manager given both points sees the report, not the network's card.
        second = Partner.objects.get(name="Night Two")
        manager = admin_user(first, "net_manager", AdminAccessPreset.MANAGER)
        manager.admin_access_profile.extra_partners.add(second)
        self.client.force_login(manager)
        navigation = self.client.get(url("index")).context["sidebar_navigation"]
        links = [item["link"] for group in navigation for item in group["items"]]
        self.assertIn(url("partners_network_changelist"), links)
        self.assertNotIn(url("partners_network_change", network.pk), links)
        self.assertEqual([link for link in links if "/partners/network/" in link], [
            url("partners_network_changelist"),
        ])

        single = make_venue("Alone")
        alone = admin_user(single, "alone_owner", AdminAccessPreset.OWNER, owner=True)
        self.client.force_login(alone)
        navigation = self.client.get(url("index")).context["sidebar_navigation"]
        links = [item["link"] for group in navigation for item in group["items"]]
        self.assertFalse([link for link in links if "/partners/network/" in link])


class PlatformMenuTests(TestCase):
    def setUp(self):
        self.root = User.objects.create_superuser(
            username="nav_root", password="x", email="", language="ru"
        )
        self.client.force_login(self.root)

    def test_platform_sees_service_tables_and_all_applications(self):
        response = self.client.get(url("index"))
        navigation = response.context["sidebar_navigation"]
        titles = [str(group.get("title", "")) for group in navigation]
        self.assertEqual(titles[-1], "Платформа")
        links = [item["link"] for item in navigation[-1]["items"]]
        self.assertIn(url("orders_orderitem_changelist"), links)
        self.assertIn(url("auth_group_changelist"), links)
        self.assertTrue(response.context["sidebar_show_all_applications"])

    def test_platform_keeps_lists_and_the_venue_column(self):
        make_venue("Kava")
        response = self.client.get(url("partners_partnerbotsettings_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("partner", response.context["cl"].list_display)
        response = self.client.get(url("orders_orderitem_changelist"))
        self.assertEqual(response.status_code, 200)
