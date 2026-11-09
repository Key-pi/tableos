"""The Admin start page of a venue (admin step 2, owner 2026-11-09, PQ-050).

«Требует внимания» — money first; every part shows only to whoever may open
the page it leads to and only while its module is on; each link opens the
list of exactly what was counted. The platform keeps Unfold's start page."""

from django.test import TestCase
from django.urls import reverse

from apps.menu.models import MenuItem
from apps.orders.models import Order
from apps.partners.models import PartnerBotSettings
from apps.users.constants import AdminAccessPreset
from apps.users.models import User
from tests.apps.analytics.test_dashboard import ON_RECEIPT, PREPAID, order
from tests.apps.users.test_admin_navigation import admin_user, modules
from tests.webapp.test_guest_api import make_bot, make_venue


class DashboardFixture(TestCase):
    def setUp(self):
        self.partner = make_venue("Kava")
        self.owner = admin_user(self.partner, "dash_owner", AdminAccessPreset.OWNER, owner=True)
        self.client.force_login(self.owner)

    def page(self):
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 200)
        return response, response.context["venue_dashboard"]

    @staticmethod
    def titles(board) -> list[str]:
        return [str(alert["title"]) for alert in board["alerts"]]


class AttentionTests(DashboardFixture):
    def test_money_comes_first(self):
        order(self.partner, channel=Order.Channel.SHIPPING, timing=ON_RECEIPT,
              status=Order.Status.READY)
        order(self.partner, channel=Order.Channel.TABLE, minutes_ago=30)
        order(self.partner, channel=Order.Channel.DELIVERY, timing=ON_RECEIPT,
              status=Order.Status.DELIVERING)
        order(self.partner, timing=PREPAID, minutes_ago=50)
        _response, board = self.page()
        self.assertEqual(self.titles(board)[:4], [
            "Переводы ждут подтверждения: 1",
            "Оплата при получении не отмечена: 1",
            "Новые заказы: 1",
            "Посылки ждут отправки: 1",
        ])
        transfers, _receipts, new, _parcels = board["alerts"][:4]
        self.assertEqual(str(transfers["text"]), "Первый отменится автоматически через 10 мин.")
        self.assertEqual(new["tone"], "danger")  # waits longer than 10 minutes

    def test_each_link_opens_what_was_counted(self):
        order(self.partner, timing=PREPAID)
        order(self.partner, timing=PREPAID, paid=True, status=Order.Status.ACCEPTED)
        order(self.partner, channel=Order.Channel.DELIVERY, timing=ON_RECEIPT,
              status=Order.Status.DELIVERING)
        order(self.partner, channel=Order.Channel.SHIPPING, timing=ON_RECEIPT,
              status=Order.Status.COMPLETED)
        order(self.partner, channel=Order.Channel.TABLE)
        order(self.partner, channel=Order.Channel.SHIPPING, timing=ON_RECEIPT,
              status=Order.Status.PREPARING)
        order(make_venue("Other"), channel=Order.Channel.TABLE)
        _response, board = self.page()
        for alert in board["alerts"]:
            with self.subTest(str(alert["title"])):
                counted = int(str(alert["title"]).rsplit(": ", 1)[1])
                _label, link = alert["links"][0]
                listed = self.client.get(link)
                self.assertEqual(listed.status_code, 200)
                self.assertEqual(listed.context["cl"].result_count, counted)

    def test_stock_links_open_those_items(self):
        modules(self.partner, module_stock_enabled=True)
        category = MenuItem.objects.filter(partner=self.partner).first().category
        for name, quantity in (("Out", 0), ("Minus", -1), ("Low", 2), ("Many", 40)):
            MenuItem.objects.create(partner=self.partner, category=category, name=name,
                                    price="10.00", track_stock=True, stock_quantity=quantity)
        _response, board = self.page()
        stock = next(alert for alert in board["alerts"] if str(alert["title"]) == "Остатки")
        self.assertEqual(stock["tone"], "danger")
        found = {}
        for label, link in stock["links"]:
            listed = self.client.get(link)
            self.assertEqual(listed.status_code, 200)
            found[str(label)] = sorted(item.name for item in listed.context["cl"].result_list)
        self.assertEqual(found, {
            "Ниже нуля — пересчитайте: 1": ["Minus"],
            "Нет в наличии: 2": ["Minus", "Out"],
            "Осталось мало: 1": ["Low"],
        })

    def test_stock_waits_for_its_module(self):
        category = MenuItem.objects.filter(partner=self.partner).first().category
        MenuItem.objects.create(partner=self.partner, category=category, name="Out",
                                price="10.00", track_stock=True, stock_quantity=0)
        _response, board = self.page()
        self.assertNotIn("Остатки", self.titles(board))

    def test_a_bot_that_does_not_answer(self):
        make_bot(self.partner)
        _response, board = self.page()
        bot = next(alert for alert in board["alerts"]
                   if str(alert["title"]) == "Бот не отвечает гостям")
        self.assertIn("@kava_bot: Ещё не проверен", str(bot["text"]))

    def test_all_clear(self):
        make_bot(self.partner).__class__.objects.filter(partner=self.partner).update(
            runtime_state="running"
        )
        response, board = self.page()
        self.assertEqual(board["alerts"], [])
        self.assertTrue(board["all_clear"])
        self.assertContains(response, "Всё в порядке: ничего не ждёт")


class RightsTests(DashboardFixture):
    def test_a_cashier_sees_their_work_not_the_money_of_the_day(self):
        modules(self.partner, module_quick_sale_enabled=True)
        order(self.partner, channel=Order.Channel.TABLE)
        cashier = admin_user(self.partner, "dash_cashier", AdminAccessPreset.CASHIER)
        self.client.force_login(cashier)
        _response, board = self.page()
        self.assertEqual(self.titles(board), ["Новые заказы: 1"])
        self.assertIsNone(board["today"])
        self.assertEqual([str(action["title"]) for action in board["actions"]],
                         ["Быстрая продажа"])
        # Setting up the venue is not a cashier's job.
        self.assertEqual(board["setup"], [])

    def test_quick_actions_follow_modules(self):
        modules(self.partner, module_quick_sale_enabled=False, module_tables_enabled=False)
        _response, board = self.page()
        titles = [str(action["title"]) for action in board["actions"]]
        self.assertNotIn("Быстрая продажа", titles)
        self.assertNotIn("Новый стол", titles)
        self.assertIn("Новая позиция", titles)

    def test_payment_details_are_the_owners_step(self):
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            transfer_payment_details=""
        )
        _response, board = self.page()
        steps = [str(step["title"]) for step in board["setup"]]
        self.assertIn("Укажите реквизиты для перевода", steps)
        manager = admin_user(self.partner, "dash_manager", AdminAccessPreset.MANAGER)
        self.client.force_login(manager)
        _response, board = self.page()
        steps = [str(step["title"]) for step in board["setup"]]
        self.assertNotIn("Укажите реквизиты для перевода", steps)


class TodayAndSetupTests(DashboardFixture):
    def test_the_days_numbers(self):
        _response, board = self.page()
        today = board["today"]
        self.assertEqual(str(today["revenue"]), "0,00 грн")
        self.assertEqual(today["average"], "—")
        self.assertEqual(today["orders"], 0)

    def test_a_new_venue_is_told_what_to_set_up(self):
        response, board = self.page()
        steps = [str(step["title"]) for step in board["setup"]]
        self.assertEqual(steps[0], "Подключите бота")
        self.assertIn("Добавьте сотрудников", steps)
        self.assertContains(response, "Что ещё настроить")
        for step in board["setup"]:
            if step["link"]:
                with self.subTest(str(step["title"])):
                    self.assertEqual(self.client.get(step["link"]).status_code, 200)


class PlatformTests(TestCase):
    def test_the_platform_keeps_its_start_page(self):
        root = User.objects.create_superuser(username="dash_root", password="x", email="",
                                             language="ru")
        self.client.force_login(root)
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("venue_dashboard", response.context)
        self.assertTrue(response.context["app_list"])
