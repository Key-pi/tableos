"""Kitchen and bar screen (PQ-048, stage 3): a shared tablet without Telegram."""

import json
import threading
import unittest
from decimal import Decimal
from unittest import mock

from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from apps.billing.staff_actions import staff_order_actions
from apps.menu.models import MenuCategory, MenuItem
from apps.orders.kitchen import issue_screen_link, mark_ready
from apps.orders.models import KitchenScreen, Order, OrderItem, OrderStatusHistory
from apps.partners.models import Partner, PartnerBotSettings
from apps.tables.models import Table
from apps.users.constants import AdminAccessPreset
from apps.users.models import AdminAccessProfile, GuestProfile, TelegramAccount, User
from webapp.tests_guest_api import make_venue

NOTIFY = "apps.notifications.tasks.send_guest_order_status_update_task.delay"


def kitchen_venue(name="Kava"):
    partner = make_venue(name)
    PartnerBotSettings.objects.filter(partner=partner).update(
        module_kitchen_screen_enabled=True, module_webapp_enabled=False
    )
    bar = MenuCategory.objects.create(
        partner=partner, name="Бар", station=MenuCategory.Station.BAR
    )
    kitchen = MenuCategory.objects.create(
        partner=partner, name="Кухня", station=MenuCategory.Station.KITCHEN
    )
    MenuItem.objects.create(
        partner=partner, category=bar, public_id=f"{name[:3].upper()}BEER", name="Пиво",
        price="90.00",
    )
    MenuItem.objects.create(
        partner=partner, category=kitchen, public_id=f"{name[:3].upper()}PAST", name="Паста",
        price="210.00",
    )
    return partner


class KitchenFixture:
    def setUp(self):
        super().setUp()
        self.partner = kitchen_venue()
        self.screen, self.token = self.make_screen("Экран", KitchenScreen.Shows.ALL)
        self.guest = GuestProfile.objects.create(
            partner=self.partner,
            telegram_account=TelegramAccount.objects.create(telegram_id=7700301),
            phone="+380501112233",
        )

    def make_screen(self, name, shows, partner=None):
        screen = KitchenScreen.objects.create(
            partner=partner or self.partner, name=name, shows=shows
        )
        return screen, issue_screen_link(screen)

    def order(self, *names, status=Order.Status.ACCEPTED, channel=Order.Channel.PICKUP,
              partner=None, **fields):
        partner = partner or self.partner
        guest = self.guest if partner == self.partner else GuestProfile.objects.create(
            partner=partner,
            telegram_account=TelegramAccount.objects.get_or_create(telegram_id=7700399)[0],
        )
        order = Order.objects.create(
            partner=partner, guest=guest, channel=channel, status=status,
            total_amount=Decimal("100.00"), **fields,
        )
        for name in names:
            item = MenuItem.objects.get(partner=partner, name=name)
            OrderItem.objects.create(
                partner=partner, order=order, menu_item=item, item_name=item.name,
                unit_price=item.price, quantity=2 if name == "Лате" else 1,
            )
        return order

    def call(self, name, data=None, *, token=None, query=""):
        url = reverse(f"webapp:kitchen_{name}") + query
        headers = {"HTTP_X_SCREEN_TOKEN": self.token if token is None else token}
        if data is None:
            response = self.client.get(url, **headers)
        else:
            response = self.client.post(
                url, json.dumps(data), content_type="application/json", **headers
            )
        return response.status_code, response.json()

    def board(self, token=None):
        status, body = self.call("state", token=token)
        self.assertEqual(status, 200, body)
        return body

    def ids(self, token=None):
        return [order["id"] for order in self.board(token)["orders"]]

    def press(self, order, action, token=None):
        with self.captureOnCommitCallbacks(execute=True):
            return self.call("action", {"order": order.public_id, "action": action}, token=token)


class KitchenAccessTests(KitchenFixture, TestCase):
    def test_the_page_opens_without_telegram_and_cannot_be_framed(self):
        response = self.client.get(reverse("webapp:kitchen"))
        self.assertContains(response, "kitchen.js")
        self.assertNotContains(response, "telegram-web-app.js")
        self.assertEqual(response["X-Frame-Options"], "DENY")

    def test_an_unknown_or_replaced_link_opens_nothing(self):
        self.assertEqual(self.call("state", token="")[0], 401)
        self.assertEqual(self.call("state", token="made-up")[1]["code"], "unknown_screen")
        new_token = issue_screen_link(self.screen)
        self.assertEqual(self.call("state")[0], 401)  # the old link stopped working
        self.assertEqual(self.call("state", token=new_token)[0], 200)
        self.screen.is_active = False
        self.screen.save()
        self.assertEqual(self.call("state", token=new_token)[0], 401)

    def test_the_secret_is_not_kept(self):
        self.screen.refresh_from_db()
        self.assertNotIn(self.token, self.screen.token_hash)
        self.assertEqual(len(self.screen.token_hash), 64)

    def test_the_module_and_the_venue_are_checked_every_time(self):
        settings = PartnerBotSettings.objects.filter(partner=self.partner)
        settings.update(module_kitchen_screen_enabled=False)
        self.assertEqual(self.call("state")[1]["code"], "module_off")
        settings.update(module_kitchen_screen_enabled=True, module_orders_enabled=False)
        self.assertEqual(self.call("state")[1]["code"], "module_off")
        settings.update(module_orders_enabled=True)
        Partner.objects.filter(id=self.partner.id).update(status=Partner.Status.SUSPENDED)
        status, body = self.call("state")
        self.assertEqual((status, body["code"]), (403, "suspended"))

    def test_only_this_venues_orders(self):
        other = kitchen_venue("Other")
        foreign = self.order("Лате", partner=other)
        mine = self.order("Лате")
        self.assertEqual(self.ids(), [mine.public_id])
        status, body = self.press(foreign, "ready")
        self.assertEqual((status, body), (200, {"stale": True}))
        foreign.refresh_from_db()
        self.assertEqual(foreign.status, Order.Status.ACCEPTED)


class KitchenBoardTests(KitchenFixture, TestCase):
    def test_accepted_orders_only_and_no_guest_data(self):
        self.order("Лате", status=Order.Status.NEW)  # not accepted yet
        self.order("Лате", status=Order.Status.READY)
        table = Table.objects.create(partner=self.partner, number=5)
        accepted = self.order(
            "Лате", "Паста", channel=Order.Channel.TABLE, table=table, comment="без сыра",
        )
        body = self.board()
        self.assertEqual([order["id"] for order in body["orders"]], [accepted.public_id])
        card = body["orders"][0]
        self.assertEqual(card["place"], "Стіл 5")  # the screen's language, uk by default
        self.assertEqual(card["comment"], "без сыра")
        self.assertEqual(
            [(item["name"], item["quantity"]) for item in card["items"]],
            [("Лате", 2), ("Паста", 1)],
        )
        text = json.dumps(body, ensure_ascii=False)
        for private in ("+380501112233", "100.00", "total", "phone", "address"):
            self.assertNotIn(private, text)

    def test_start_then_ready_through_the_order_service(self):
        order = self.order("Лате")
        with mock.patch(NOTIFY) as notify:
            self.assertEqual(self.press(order, "start")[1], {"ok": True})
            order.refresh_from_db()
            self.assertEqual(order.status, Order.Status.PREPARING)
            self.assertEqual(self.press(order, "ready")[1], {"ok": True, "ready": True})
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.READY)
        # The guest is told as from the bot: once per status.
        self.assertEqual(
            [call.args[2] for call in notify.call_args_list], ["preparing", "ready"]
        )
        history = OrderStatusHistory.objects.filter(order=order).order_by("created_at")
        self.assertEqual([row.to_status for row in history], ["preparing", "ready"])
        self.assertTrue(all(row.note == "🖥 Экран" for row in history))
        body = self.board()
        self.assertEqual(body["orders"], [])
        self.assertEqual([done["id"] for done in body["done"]], [order.public_id])

    def test_ready_straight_from_accepted_and_pressing_again(self):
        order = self.order("Лате")
        self.assertTrue(self.press(order, "ready")[1]["ready"])
        self.assertEqual(self.press(order, "ready")[1], {"stale": True})
        self.assertEqual(self.press(order, "start")[1], {"stale": True})
        self.assertEqual(
            OrderStatusHistory.objects.filter(order=order, to_status="ready").count(), 1
        )

    def test_a_short_answer_while_nothing_changed(self):
        version = self.board()["version"]
        quiet = self.call("state", query=f"?since={version}")[1]
        self.assertEqual(quiet, {"unchanged": True, "version": version})
        order = self.order("Лате")
        fresh = self.call("state", query=f"?since={version}")[1]
        self.assertEqual([card["id"] for card in fresh["orders"]], [order.public_id])
        self.press(order, "start")
        self.assertNotIn("unchanged", self.call("state", query=f"?since={fresh['version']}")[1])

    def test_the_screens_language(self):
        self.screen.language = "en"
        self.screen.save()
        self.assertEqual(self.board()["texts"]["start"], "Take it")


class SplitScreensTests(KitchenFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.screen.delete()
        self.bar, self.bar_token = self.make_screen("Бар", KitchenScreen.Shows.BAR)
        self.kitchen, self.kitchen_token = self.make_screen("Кухня", KitchenScreen.Shows.KITCHEN)

    def test_each_screen_sees_its_part_and_what_else_is_in_the_order(self):
        order = self.order("Пиво", "Паста", "Лате")
        bar_card = self.board(self.bar_token)["orders"][0]
        self.assertEqual([item["name"] for item in bar_card["items"]], ["Пиво", "Лате"])
        self.assertEqual(
            [(item["name"], item["station"], item["done"]) for item in bar_card["elsewhere"]],
            [("Паста", "kitchen", False)],
        )
        kitchen_card = self.board(self.kitchen_token)["orders"][0]
        self.assertEqual([item["name"] for item in kitchen_card["items"]], ["Паста", "Лате"])
        self.assertEqual(order.public_id, kitchen_card["id"])

    def test_the_order_is_ready_when_every_part_is_done(self):
        order = self.order("Пиво", "Паста")
        with mock.patch(NOTIFY) as notify:
            self.assertEqual(
                self.press(order, "ready", token=self.bar_token)[1], {"ok": True, "ready": False}
            )
            order.refresh_from_db()
            self.assertEqual(order.status, Order.Status.ACCEPTED)
            self.assertEqual(self.ids(self.bar_token), [])  # the bar's part is done
            kitchen_card = self.board(self.kitchen_token)["orders"][0]
            self.assertEqual(kitchen_card["elsewhere"][0]["done"], True)
            self.assertEqual(
                self.press(order, "ready", token=self.kitchen_token)[1],
                {"ok": True, "ready": True},
            )
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.READY)
        self.assertEqual([call.args[2] for call in notify.call_args_list], ["ready"])

    def test_a_screen_without_a_part_in_the_order_does_not_show_it(self):
        order = self.order("Паста")
        self.assertEqual(self.ids(self.bar_token), [])
        self.assertEqual(self.press(order, "ready", token=self.bar_token)[1], {"stale": True})
        self.assertTrue(self.press(order, "ready", token=self.kitchen_token)[1]["ready"])

    def test_a_part_not_said_is_made_by_any_screen(self):
        order = self.order("Лате")
        self.assertEqual(self.ids(self.bar_token), [order.public_id])
        self.assertEqual(self.ids(self.kitchen_token), [order.public_id])
        self.assertTrue(self.press(order, "ready", token=self.kitchen_token)[1]["ready"])
        self.assertEqual(self.ids(self.bar_token), [])

    def test_a_screen_for_everything_finishes_the_order_at_once(self):
        _screen, all_token = self.make_screen("Все", KitchenScreen.Shows.ALL)
        order = self.order("Пиво", "Паста")
        self.press(order, "ready", token=self.bar_token)
        self.assertTrue(self.press(order, "ready", token=all_token)[1]["ready"])
        self.assertEqual(self.ids(self.kitchen_token), [])


class KitchenModuleIndependenceTests(KitchenFixture, TestCase):
    """PQ-033: the module works alone and does not change the others."""

    def test_it_works_with_only_orders_and_itself(self):
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            module_webapp_enabled=False, module_cart_enabled=False, module_tables_enabled=False,
            module_pickup_enabled=False, module_delivery_enabled=False,
            module_shipping_enabled=False, module_billing_enabled=False,
            module_staff_call_enabled=False, module_quick_sale_enabled=False,
            module_reports_enabled=False,
        )
        order = self.order("Лате")
        self.assertEqual(self.ids(), [order.public_id])
        self.assertTrue(self.press(order, "ready")[1]["ready"])

    def test_switching_it_changes_nothing_for_staff(self):
        order = self.order("Лате")
        settings = PartnerBotSettings.objects.filter(partner=self.partner)
        with_screen = staff_order_actions(Order.objects.get(id=order.id))
        settings.update(module_kitchen_screen_enabled=False)
        self.assertEqual(staff_order_actions(Order.objects.get(id=order.id)), with_screen)
        # Off: the screen stops, the order stays as it was.
        self.assertEqual(self.press(order, "ready")[0], 403)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.ACCEPTED)


class KitchenAdminTests(KitchenFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.owner = User.objects.create_user(
            username="kava_owner", password="x", partner=self.partner,
            role=User.Role.OWNER, is_staff=True,
        )
        AdminAccessProfile.objects.create(
            user=self.owner, partner=self.partner, preset=AdminAccessPreset.OWNER,
            is_partner_owner=True, can_access_admin=True,
        )
        self.client.force_login(self.owner)

    def _link_token(self, response) -> str:
        links = [
            str(message) for message in response.context["messages"]
            if "/app/screen/#" in str(message)
        ]
        self.assertEqual(len(links), 1, links)
        return links[0].split('href="')[1].split('"')[0].split("#", 1)[1]

    def test_a_new_screen_shows_its_link_once(self):
        response = self.client.post(
            reverse("admin:orders_kitchenscreen_add"),
            {"partner": str(self.partner.id), "name": "Бар", "shows": "bar",
             "language": "uk", "is_active": "on"},
            follow=True,
        )
        token = self._link_token(response)
        self.assertEqual(self.board(token)["screen"]["name"], "Бар")
        page = self.client.get(
            reverse("admin:orders_kitchenscreen_change",
                    args=[KitchenScreen.objects.get(name="Бар").pk])
        )
        self.assertNotContains(page, token)

    def test_a_new_link_switches_the_old_one_off(self):
        response = self.client.post(
            reverse("admin:orders_kitchenscreen_changelist"),
            {"action": "issue_link_action", "_selected_action": [str(self.screen.pk)]},
            follow=True,
        )
        token = self._link_token(response)
        self.assertEqual(self.call("state")[0], 401)
        self.assertEqual(self.call("state", token=token)[0], 200)

    def test_another_venues_screens_are_not_seen(self):
        other = kitchen_venue("Other")
        foreign, _token = self.make_screen("Чужой", KitchenScreen.Shows.ALL, partner=other)
        listing = self.client.get(reverse("admin:orders_kitchenscreen_changelist"))
        self.assertContains(listing, "Экран")
        self.assertNotContains(listing, "Чужой")
        page = self.client.get(reverse("admin:orders_kitchenscreen_change", args=[foreign.pk]))
        self.assertIn(page.status_code, (302, 404))

    def test_where_a_category_is_made(self):
        category = MenuCategory.objects.get(partner=self.partner, name="Бар")
        page = self.client.get(reverse("admin:menu_menucategory_change", args=[category.pk]))
        self.assertContains(page, 'name="station"')


@unittest.skipUnless(connection.vendor == "postgresql", "row locks need PostgreSQL")
class SplitScreensConcurrencyTests(KitchenFixture, TransactionTestCase):
    """Bar and kitchen press «Готово» at the same moment: the order becomes
    «Готов» exactly once, never stays unfinished."""

    def test_both_parts_at_once(self):
        bar, _bar_token = self.make_screen("Бар", KitchenScreen.Shows.BAR)
        kitchen, _kitchen_token = self.make_screen("Кухня", KitchenScreen.Shows.KITCHEN)
        patcher = mock.patch(NOTIFY)
        patcher.start()
        self.addCleanup(patcher.stop)
        for _attempt in range(5):
            order = self.order("Пиво", "Паста")
            start = threading.Barrier(2)
            errors = []

            def press(screen, order=order, start=start, errors=errors):
                try:
                    start.wait()
                    mark_ready(screen, order.public_id)
                except Exception as exc:  # noqa: BLE001 — reported below
                    errors.append(exc)
                finally:
                    close_old_connections()

            threads = [threading.Thread(target=press, args=(screen,)) for screen in (bar, kitchen)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(errors, [])
            order.refresh_from_db()
            self.assertEqual(order.status, Order.Status.READY)
            self.assertEqual(
                OrderStatusHistory.objects.filter(order=order, to_status="ready").count(), 1
            )
