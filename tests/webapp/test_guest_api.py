"""Mini App for guests (PQ-048, stage 1): the API calls the bot's services."""

import json
import time
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.menu.models import MenuCategory, MenuItem
from apps.orders.models import Cart, Order
from apps.partners.models import BotInstance, Network, Partner, PartnerBotSettings
from apps.tables.models import Table
from apps.tables.services import activate_table_session
from apps.users.models import GuestProfile
from webapp.auth import sign_init_data

TG = 7700101
TOKEN = "777000111:AAEwebappexampleexampleexample01"


def make_venue(name, *, network=None, status=Partner.Status.ACTIVE, webapp=True,
               staff_screen=True):
    partner = Partner.objects.create(
        name=name, slug=name.lower().replace(" ", "-"), status=status, network=network,
        timezone="Europe/Kyiv",
    )
    PartnerBotSettings.objects.create(
        partner=partner, module_webapp_enabled=webapp, module_staff_screen_enabled=staff_screen,
        module_menu_enabled=True,
        module_cart_enabled=True, module_orders_enabled=True, module_tables_enabled=True,
        module_pickup_enabled=True, module_delivery_enabled=True,
        module_shipping_enabled=True, shipping_nova_poshta=True,
        transfer_payment_details="Карта 4149 0000 0000 0000",
    )
    category = MenuCategory.objects.create(partner=partner, name="Кава", sort_order=1)
    MenuItem.objects.create(
        partner=partner, category=category, public_id=f"{name[:3].upper()}LATTE",
        name="Лате", price="80.00", image_url="https://example.com/latte.jpg",
    )
    MenuItem.objects.create(
        partner=partner, category=category, public_id=f"{name[:3].upper()}HOOKA",
        name="Кальян", price="400.00", available_for_pickup=False,
        available_for_delivery=False, available_for_shipping=False,
    )
    return partner


def make_bot(partner, token=TOKEN, username="kava_bot"):
    bot = BotInstance.objects.create(
        partner=partner, display_name=partner.name, username=username,
        token_encrypted="", is_active=True,
    )
    bot.set_token(token)
    bot.save()
    return bot


class MiniAppFixture(TestCase):
    def setUp(self):
        self.partner = make_venue("Kava")
        self.bot = make_bot(self.partner)
        self.latte = MenuItem.objects.get(partner=self.partner, name="Лате")

    def init_data(self, *, user_id=TG, language="uk", token=TOKEN, auth_date=None):
        return sign_init_data(
            {
                "auth_date": str(auth_date or int(time.time())),
                "user": {"id": user_id, "first_name": "Оля", "language_code": language},
            },
            token,
        )

    def call(self, name, data=None, *, bot=None, init=None, query="", **init_kwargs):
        url = reverse(f"webapp:{name}", args=[(bot or self.bot).id.hex]) + query
        headers = {"HTTP_X_TELEGRAM_INIT_DATA": init if init is not None
                   else self.init_data(**init_kwargs)}
        if data is None:
            response = self.client.get(url, **headers)
        else:
            response = self.client.post(
                url, json.dumps(data), content_type="application/json", **headers
            )
        return response.status_code, response.json()

    def choose(self, channel):
        status, body = self.call("channel", {"channel": channel})
        self.assertEqual(status, 200, body)
        return body

    def add_latte(self):
        status, body = self.call("cart", {"action": "add", "item": self.latte.public_id})
        self.assertEqual(status, 200, body)
        return body["cart"]


class SignInTests(MiniAppFixture):
    def test_without_a_valid_signature_nothing_opens(self):
        self.assertEqual(self.call("state", init="")[0], 401)
        self.assertEqual(self.call("state", token="1:another-bot")[0], 401)
        old = int(time.time()) - 3 * 86400
        self.assertEqual(self.call("state", auth_date=old)[0], 401)
        self.assertFalse(GuestProfile.objects.exists())

    def test_an_old_opening_asks_to_open_the_menu_again(self):
        old = int(time.time()) - 3 * 86400
        status, body = self.call("state", auth_date=old)
        self.assertEqual(status, 401)
        self.assertIn("бота", body["error"])  # a sentence, not a code

    def test_the_page_of_an_unknown_or_switched_off_bot_is_not_found(self):
        self.assertEqual(self.client.get("/app/0123/").status_code, 404)
        self.bot.is_active = False
        self.bot.save()
        response = self.client.get(reverse("webapp:guest", args=[self.bot.id.hex]))
        self.assertEqual(response.status_code, 404)

    def test_a_switched_off_bot_or_unknown_address_is_not_found(self):
        self.bot.is_active = False
        self.bot.save()
        self.assertEqual(self.call("state")[0], 404)
        response = self.client.get("/app/not-a-bot/api/state")
        self.assertEqual(response.status_code, 404)

    def test_the_module_and_the_venue_are_checked_on_every_request(self):
        PartnerBotSettings.objects.filter(partner=self.partner).update(module_webapp_enabled=False)
        status, body = self.call("state", language="en")
        self.assertEqual((status, body["code"]), (403, "module_off"))
        self.assertIn("app", body["error"])  # in the guest's language
        PartnerBotSettings.objects.filter(partner=self.partner).update(module_webapp_enabled=True)
        Partner.objects.filter(id=self.partner.id).update(status=Partner.Status.SUSPENDED)
        self.assertEqual(self.call("state")[1]["code"], "suspended")

    def test_the_first_opening_makes_the_guest_known(self):
        status, body = self.call("state", language="en")
        self.assertEqual(status, 200, body)
        self.assertTrue(
            GuestProfile.objects.filter(
                partner=self.partner, telegram_account__telegram_id=TG
            ).exists()
        )
        self.assertEqual(body["venue"]["name"], "Kava")
        self.assertEqual(body["texts"]["cart"], "Cart")
        self.assertIsNone(body["cart"]["channel"])
        self.assertEqual(
            [way["code"] for way in body["ways"]], ["table", "pickup", "delivery", "shipping"]
        )
        self.assertFalse(body["ways"][0]["enabled"])  # the table needs its QR code

    def test_the_page_opens_inside_telegram_web(self):
        response = self.client.get(reverse("webapp:guest", args=[self.bot.id.hex]))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("X-Frame-Options", response)
        self.assertIn("telegram.org", response["Content-Security-Policy"])
        self.assertContains(response, "telegram-web-app.js")

    def test_styles_and_script_are_fetched_anew_after_an_update(self):
        # Telegram keeps old files in its cache: without a version in the
        # link a new guest.js ran with the old guest.css (owner, 2026-11-07).
        response = self.client.get(reverse("webapp:guest", args=[self.bot.id.hex]))
        html = response.content.decode()
        for name in ("guest.css", "guest.js"):
            self.assertRegex(html, rf"webapp/{name}\?v=[0-9a-f]{{10}}\"")


class MenuAndCartTests(MiniAppFixture):
    def test_the_page_speaks_to_any_business(self):
        # A shop, not only a cafe (owner, 2026-11-07): no «menu» in the
        # words every venue sees; an owner can still put their own.
        texts = self.call("state")[1]["texts"]
        self.assertEqual(texts["search"], "Пошук")
        for key in ("search", "menu_empty", "open_in_telegram"):
            self.assertNotIn("меню", texts[key].lower(), key)

    def test_the_menu_with_photos_and_where_items_are_sold(self):
        status, body = self.call("menu")
        self.assertEqual(status, 200)
        items = {item["name"]: item for item in body["categories"][0]["items"]}
        self.assertEqual(items["Лате"]["photo"], "https://example.com/latte.jpg")
        self.assertEqual(items["Кальян"]["sold_in"], ["table"])

    def test_a_way_first_then_the_cart_like_in_the_bot(self):
        status, body = self.call("cart", {"action": "add", "item": self.latte.public_id})
        self.assertEqual((status, body["code"]), (400, "refused"))
        self.choose("pickup")
        cart = self.add_latte()
        self.assertEqual((cart["channel"], cart["count"], cart["total"]), ("pickup", 1, "80.00"))
        status, body = self.call(
            "cart", {"action": "change", "item": self.latte.public_id, "delta": 2}
        )
        self.assertEqual(body["cart"]["total"], "240.00")
        status, body = self.call("cart", {"action": "remove", "item": self.latte.public_id})
        self.assertEqual(body["cart"]["items"], [])

    def test_items_not_sold_for_the_way_and_other_venues_items_are_refused(self):
        self.choose("pickup")
        hookah = MenuItem.objects.get(partner=self.partner, name="Кальян")
        status, body = self.call("cart", {"action": "add", "item": hookah.public_id})
        self.assertEqual(status, 400)
        other = make_venue("Other")
        foreign = MenuItem.objects.get(partner=other, name="Лате")
        status, body = self.call("cart", {"action": "add", "item": foreign.public_id})
        self.assertEqual(status, 400)
        self.assertFalse(Cart.objects.filter(items__menu_item=foreign).exists())

    def test_bad_requests_are_refused_without_changes(self):
        self.choose("pickup")
        self.add_latte()
        for data in (
            {"action": "change", "item": self.latte.public_id, "delta": "5"},
            {"action": "change", "item": self.latte.public_id, "delta": 500},
            {"action": "fly"},
        ):
            self.assertEqual(self.call("cart", data)[0], 400, data)
        self.assertEqual(self.call("state")[1]["cart"]["count"], 1)
        self.assertEqual(self.call("channel", {"channel": "teleport"})[0], 400)


class CheckoutTests(MiniAppFixture):
    def details(self, **data):
        status, body = self.call("details", {"phone": "+380501112233", **data})
        self.assertEqual(status, 200, body)

    def quote(self, query=""):
        status, body = self.call("checkout", query=query)
        self.assertEqual(status, 200, body)
        return body

    def test_pickup_paid_by_transfer(self):
        self.choose("pickup")
        self.add_latte()
        self.details()
        quote = self.quote()
        self.assertEqual(quote["to_pay"], "80.00")
        self.assertIn("4149", quote["payment_details"])
        self.assertEqual([slot["code"] for slot in quote["slots"]][:1], ["now"])
        status, body = self.call("place", {
            "slot": "now", "use_bonuses": False, "payment": "prepaid",
            "expected_total": quote["total"], "expected_to_pay": quote["to_pay"],
        })
        self.assertEqual(status, 200, body)
        order = Order.objects.get(public_id=body["order"])
        self.assertEqual(
            (order.channel, order.payment_timing, order.partner_id, order.total_amount),
            ("pickup", "prepaid", self.partner.id, Decimal("80.00")),
        )
        self.assertIn(order.public_id, body["text"])

    def test_a_changed_amount_places_nothing(self):
        self.choose("pickup")
        self.add_latte()
        self.details()
        quote = self.quote()
        self.call("cart", {"action": "change", "item": self.latte.public_id, "delta": 1})
        status, body = self.call("place", {
            "slot": "now", "payment": "prepaid",
            "expected_total": quote["total"], "expected_to_pay": quote["to_pay"],
        })
        self.assertEqual(status, 400)
        self.assertFalse(Order.objects.exists())

    def test_delivery_on_receipt_with_the_address(self):
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            delivery_pay_on_receipt=True, delivery_fee=Decimal("50.00")
        )
        self.choose("delivery")
        self.add_latte()
        self.assertEqual(self.call("details", {"phone": "+380501112233"})[0], 400)
        self.details(address="Хрещатик, 1, кв. 5")
        quote = self.quote()
        self.assertEqual((quote["delivery_fee"], quote["total"]), ("50.00", "130.00"))
        self.assertEqual(quote["delivery_address"], "Хрещатик, 1, кв. 5")
        self.assertIn("on_receipt", [mode["code"] for mode in quote["payment_modes"]])
        status, body = self.call("place", {
            "slot": "now", "payment": "on_receipt",
            "expected_total": quote["total"], "expected_to_pay": quote["to_pay"],
        })
        self.assertEqual(status, 200, body)
        order = Order.objects.get(public_id=body["order"])
        self.assertEqual((order.delivery_address, order.payment_timing),
                         ("Хрещатик, 1, кв. 5", "on_receipt"))

    def test_shipping_to_a_branch(self):
        self.choose("shipping")
        self.add_latte()
        self.details(carrier="nova_poshta", destination="Київ, відділення 12, Оля, 0501112233")
        quote = self.quote()
        self.assertEqual(quote["carriers"][0]["code"], "nova_poshta")
        # No «when» by post: a parcel has no pickup time (PQ-034).
        self.assertEqual((quote["slots"], quote["closed"]), ([], ""))
        status, body = self.call("place", {
            "payment": "prepaid", "shipping_cost": quote["shipping_cost"],
            "expected_total": quote["total"], "expected_to_pay": quote["to_pay"],
        })
        self.assertEqual(status, 200, body)
        order = Order.objects.get(public_id=body["order"])
        self.assertEqual(order.shipping_carrier, "nova_poshta")

    def test_at_the_table(self):
        table = Table.objects.create(partner=self.partner, number=5)
        activate_table_session(
            partner_id=self.partner.id, telegram_id=TG, payload=table.deep_link_payload
        )
        state = self.call("state")[1]
        self.assertEqual(state["cart"]["channel"], "table")
        self.assertTrue(state["ways"][0]["enabled"])
        hookah = MenuItem.objects.get(partner=self.partner, name="Кальян")
        self.assertEqual(self.call("cart", {"action": "add", "item": hookah.public_id})[0], 200)
        status, body = self.call("place", {})
        self.assertEqual(status, 200, body)
        order = Order.objects.get(public_id=body["order"])
        self.assertEqual((order.channel, order.table_id), ("table", table.id))


class NetworkBotTests(MiniAppFixture):
    def setUp(self):
        super().setUp()
        self.network = Network.objects.create(name="Ночь")
        Partner.objects.filter(id=self.partner.id).update(network=self.network)
        self.second = make_venue("Second", network=self.network)
        self.network.bot = self.bot
        self.network.save()

    def test_the_guest_picks_a_venue_first_and_it_is_remembered(self):
        status, body = self.call("state")
        self.assertEqual(status, 200)
        self.assertTrue(body["needs_point"])
        self.assertEqual({point["name"] for point in body["points"]}, {"Kava", "Second"})
        self.assertEqual(self.call("menu")[0], 409)
        status, _body = self.call("point", {"point": self.second.id.hex})
        self.assertEqual(status, 200)
        state = self.call("state")[1]
        self.assertEqual(state["venue"]["name"], "Second")
        names = {item["id"] for item in self.call("menu")[1]["categories"][0]["items"]}
        self.assertTrue(all(name.startswith("SEC") for name in names))

    def test_a_closed_venue_offers_the_other_venues(self):
        self.call("point", {"point": self.second.id.hex})
        Partner.objects.filter(id=self.second.id).update(status=Partner.Status.SUSPENDED)
        # The saved choice is kept: the guest is told and offered the others.
        PartnerBotSettings.objects.filter(partner=self.partner).update(module_webapp_enabled=True)
        from apps.partners.models import NetworkGuestChoice
        NetworkGuestChoice.objects.update(partner=self.second)
        status, body = self.call("state", language="en")
        self.assertEqual((status, body["code"]), (403, "suspended"))
        self.assertEqual([point["name"] for point in body["points"]], ["Kava"])
        self.assertEqual(body["texts"]["change_point"], "Change venue")
        self.assertEqual(self.call("point", {"point": self.partner.id.hex})[0], 200)
        self.assertEqual(self.call("state")[1]["venue"]["name"], "Kava")

    def test_a_venue_of_another_network_cannot_be_picked(self):
        stranger = make_venue("Stranger")
        self.assertEqual(self.call("point", {"point": stranger.id.hex})[0], 409)
