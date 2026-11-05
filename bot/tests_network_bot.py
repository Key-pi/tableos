"""One bot for a network of venues (PQ-047, stage 2): the guest picks the
venue after /start, links pick it themselves, a button acts for the venue
its message was sent for, notifications of a venue without its own bot go
through the network's bot."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.types import CallbackQuery, Chat, Message, Update, User
from asgiref.sync import async_to_sync, sync_to_async
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.analytics.models import TrafficSource
from apps.employees.invites import create_staff_invite
from apps.employees.models import EmployeeProfile
from apps.notifications.services import _active_partner_bot, _remember_points
from apps.partners.models import (
    BotInstance,
    BotMessagePoint,
    Network,
    NetworkGuestChoice,
    Partner,
    PartnerBotSettings,
)
from apps.partners.networks import bot_for_partner, remember_message_point, serves_network
from apps.tables.models import Table
from apps.users.constants import AdminAccessPreset
from apps.users.models import AdminAccessProfile, TelegramAccount
from apps.users.models import User as DjangoUser
from bot.handlers import points as points_handlers
from bot.middlewares.message_points import MessagePointMiddleware
from bot.middlewares.partner_runtime import PartnerRuntimeMiddleware
from bot.services.content import BotContent
from bot.services.context import partner_for_this_update, resolve_partner_for_bot_token
from bot.services.navigation import GuestAction, resolve_guest_navigation_state
from bot.services.network import network_update
from bot.services.runtime import BotRuntimeConfig, register_runtime_configs

TOKEN = "network-bot-token"
OWN_TOKEN = "own-bot-token"
GUEST = 7740001
CHAT = GUEST


def venue(name, network, status=Partner.Status.ACTIVE, address=""):
    partner = Partner.objects.create(
        name=name, slug=name.lower().replace(" ", "-"), status=status, network=network,
        address=address,
    )
    PartnerBotSettings.objects.create(partner=partner, module_menu_enabled=True)
    return partner


def _data(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


class NetworkBotFixture(TestCase):
    def setUp(self):
        self.network = Network.objects.create(name="Кава Мережа")
        self.a = venue("Kava A", self.network, address="Хрещатик, 1")
        self.b = venue("Kava B", self.network, address="Подол, 5")
        self.c = venue("Kava C", self.network, status=Partner.Status.SUSPENDED)
        self.bot_instance = BotInstance.objects.create(
            partner=self.a, display_name="Kava", username="kava_net_bot",
            token_encrypted="", is_active=True,
        )
        self.bot_instance.set_token("123456789:AAEexampleexampleexampleexample")
        self.bot_instance.save()
        # The owner picks it in the network's card (Admin → Сети заведений).
        self.network.bot = self.bot_instance
        self.network.save()
        register_runtime_configs(
            [
                BotRuntimeConfig(
                    partner_id=str(self.a.id),
                    bot_instance_id=str(self.bot_instance.id),
                    username="kava_net_bot",
                    token=TOKEN,
                )
            ]
        )
        self.bot = SimpleNamespace(
            token=TOKEN, send_message=AsyncMock(), answer_callback_query=AsyncMock()
        )

    def tearDown(self):
        register_runtime_configs([])

    # --- updates through the middleware ---------------------------------------

    @staticmethod
    def _user():
        return User(id=GUEST, is_bot=False, first_name="Оля", language_code="uk")

    def _message(self, text, *, reply_to=None):
        return Message(
            message_id=500, date=datetime.now(), chat=Chat(id=CHAT, type="private"),
            from_user=self._user(), text=text, reply_to_message=reply_to,
        )

    def _bot_message(self, message_id):
        return Message(
            message_id=message_id, date=datetime.now(), chat=Chat(id=CHAT, type="private"),
            from_user=User(id=1, is_bot=True, first_name="bot"), text="…",
        )

    def _callback(self, data, *, message_id=900):
        return CallbackQuery(
            id="cb1", from_user=self._user(), chat_instance="ci", data=data,
            message=self._bot_message(message_id),
        )

    def run_update(self, *, message=None, callback=None):
        """The venue the handler ran for, or None if it did not run."""

        seen = []

        async def handler(event, data):
            seen.append(await sync_to_async(resolve_partner_for_bot_token)(TOKEN))

        update = Update(update_id=1, message=message, callback_query=callback)
        data = {"bot": self.bot, "event_from_user": self._user()}
        async_to_sync(PartnerRuntimeMiddleware())(handler, update, data)
        return seen[0] if seen else None

    def asked_to_pick(self):
        call = self.bot.send_message.call_args
        if call is None:
            return None
        return _data(call.kwargs["reply_markup"])


class PickingTheVenueTests(NetworkBotFixture):
    def test_a_new_guest_is_asked_which_venue_and_suspended_ones_are_not_offered(self):
        self.assertIsNone(self.run_update(message=self._message("/start")))
        self.assertEqual(
            self.asked_to_pick(), [f"point:{self.a.id.hex}", f"point:{self.b.id.hex}"]
        )
        text = self.bot.send_message.call_args.args[1]
        self.assertIn("У який заклад?", text)  # the guest's Telegram language
        labels = [
            button.text
            for row in self.bot.send_message.call_args.kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertEqual(labels, ["Kava A — Хрещатик, 1", "Kava B — Подол, 5"])

    def test_the_pick_is_remembered(self):
        picked = self.run_update(callback=self._callback(f"point:{self.b.id.hex}"))
        self.assertEqual(picked, self.b)
        self.assertEqual(NetworkGuestChoice.objects.get().partner, self.b)
        self.assertEqual(self.run_update(message=self._message("Меню")), self.b)

    def test_a_pick_of_a_venue_outside_the_network_is_ignored(self):
        stranger = venue("Stranger", None)
        self.assertIsNone(self.run_update(callback=self._callback(f"point:{stranger.id.hex}")))
        self.assertFalse(NetworkGuestChoice.objects.exists())

    def test_one_active_venue_needs_no_question(self):
        Partner.objects.filter(id=self.b.id).update(status=Partner.Status.SUSPENDED)
        self.assertEqual(self.run_update(message=self._message("/start")), self.a)
        self.bot.send_message.assert_not_called()

    def test_a_table_qr_picks_its_venue(self):
        table = Table.objects.create(partner=self.b, number=4)
        payload = table.deep_link_payload
        self.assertEqual(self.run_update(message=self._message(f"/start {payload}")), self.b)
        self.assertEqual(NetworkGuestChoice.objects.get().partner, self.b)

    def test_a_staff_invite_picks_its_venue(self):
        owner = DjangoUser.objects.create_user(
            username="kava_owner", password="x", partner=self.b, role=DjangoUser.Role.OWNER
        )
        EmployeeProfile.objects.create(
            partner=self.b, user=owner,
            telegram_account=TelegramAccount.objects.create(telegram_id=7740099),
        )
        invite = create_staff_invite(partner_id=self.b.id, actor=owner, role="waiter")
        picked = self.run_update(message=self._message(f"/start inv_{invite.code}"))
        self.assertEqual(picked, self.b)

    def test_a_source_code_picks_the_venue_only_when_one_venue_has_it(self):
        TrafficSource.objects.create(partner=self.b, name="Insta", code="insta")
        self.assertEqual(self.run_update(message=self._message("/start src_insta")), self.b)
        NetworkGuestChoice.objects.all().delete()
        TrafficSource.objects.create(partner=self.a, name="Insta", code="insta")
        self.assertIsNone(self.run_update(message=self._message("/start src_insta")))

    def test_a_suspended_chosen_venue_says_so_and_offers_the_others(self):
        account = TelegramAccount.objects.create(telegram_id=GUEST)
        NetworkGuestChoice.objects.create(
            network=self.network, telegram_account=account, partner=self.c
        )
        with patch.object(
            PartnerRuntimeMiddleware, "_answer_suspended_update", new=AsyncMock()
        ) as suspended:
            self.assertIsNone(self.run_update(message=self._message("Меню")))
        suspended.assert_awaited_once()
        self.assertEqual(
            self.asked_to_pick(), [f"point:{self.a.id.hex}", f"point:{self.b.id.hex}"]
        )

    def test_a_venues_own_bot_works_as_before(self):
        own = BotInstance.objects.create(
            partner=self.b, display_name="B", username="kava_b_bot", token_encrypted="",
        )
        register_runtime_configs(
            [BotRuntimeConfig(str(self.b.id), str(own.id), "kava_b_bot", OWN_TOKEN)]
        )
        seen = []

        async def handler(event, data):
            seen.append(await sync_to_async(resolve_partner_for_bot_token)(OWN_TOKEN))

        bot = SimpleNamespace(token=OWN_TOKEN, send_message=AsyncMock())
        update = Update(update_id=1, message=self._message("/start"))
        async_to_sync(PartnerRuntimeMiddleware())(
            handler, update, {"bot": bot, "event_from_user": self._user()}
        )
        self.assertEqual(seen, [self.b])
        bot.send_message.assert_not_called()


class ButtonsActForTheirVenueTests(NetworkBotFixture):
    def test_a_button_acts_for_the_venue_its_message_was_sent_for(self):
        account = TelegramAccount.objects.create(telegram_id=GUEST)
        NetworkGuestChoice.objects.create(
            network=self.network, telegram_account=account, partner=self.a
        )
        remember_message_point(self.bot_instance.id, CHAT, 901, self.b.id)
        # A notification of venue B pressed while the guest browses venue A.
        pressed = self._callback("staffacc:x", message_id=901)
        self.assertEqual(self.run_update(callback=pressed), self.b)
        # The guest's choice stays.
        self.assertEqual(NetworkGuestChoice.objects.get().partner, self.a)
        # A reply to that message, too (e.g. a ТТН typed in answer).
        reply = self._message("20450000000000", reply_to=self._bot_message(901))
        self.assertEqual(self.run_update(message=reply), self.b)

    def test_messages_sent_in_an_update_remember_its_venue(self):
        sent = self._bot_message(902)

        async def make_request(bot, method):
            return sent

        async def scenario():
            with network_update(self.bot_instance.id, 2, self.b.id):
                await MessagePointMiddleware()(make_request, self.bot, object())
            # Outside a network bot's update nothing is stored.
            await MessagePointMiddleware()(make_request, self.bot, object())

        async_to_sync(scenario)()
        self.assertEqual(
            list(BotMessagePoint.objects.values_list("message_id", "partner_id")),
            [(902, self.b.id)],
        )


class SpeakingForAVenueWithoutItsOwnBotTests(NetworkBotFixture):
    def test_notifications_and_links_use_the_networks_bot(self):
        self.assertEqual(bot_for_partner(self.b.id), self.bot_instance)
        self.assertEqual(_active_partner_bot(self.b.id), self.bot_instance)
        _remember_points(self.bot_instance, self.b.id, [(CHAT, True, "", False, False, 903)])
        self.assertEqual(BotMessagePoint.objects.get(message_id=903).partner, self.b)

    def test_a_suspended_venue_gets_no_messages_through_the_network_bot(self):
        from apps.notifications.services import NotificationDeliveryError

        with self.assertRaises(NotificationDeliveryError):
            _active_partner_bot(self.c.id)

    def test_a_venue_outside_networks_has_no_borrowed_bot(self):
        self.assertIsNone(bot_for_partner(venue("Alone", None).id))


class KeyboardAndHandlersTests(NetworkBotFixture):
    def _state(self, points):
        content = BotContent.for_partner(self.b)
        with network_update(self.bot_instance.id, points, self.b.id):
            return resolve_guest_navigation_state(
                partner_id=self.b.id, telegram_id=GUEST, content=content
            )

    def test_the_venue_button_is_there_only_with_several_venues(self):
        self.assertTrue(self._state(2).has_action(GuestAction.POINT))
        self.assertFalse(self._state(1).has_action(GuestAction.POINT))

    def test_picking_greets_as_the_venue(self):
        callback = SimpleNamespace(
            data=f"point:{self.b.id.hex}",
            from_user=SimpleNamespace(
                id=GUEST, username="", first_name="Оля", last_name="", language_code=""
            ),
            answer=AsyncMock(),
            message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock()),
        )

        async def scenario():
            with network_update(self.bot_instance.id, 2, self.b.id), partner_for_this_update(
                TOKEN, self.b
            ):
                await points_handlers.point_picked_handler(callback, self.bot)

        async_to_sync(scenario)()
        self.assertIn("Kava B", callback.message.edit_text.call_args.args[0])
        greeting = callback.message.answer.call_args_list[0].args[0]
        self.assertIn("Kava B", greeting)


class StaffSeeTheVenueTests(NetworkBotFixture):
    def test_a_staff_notification_of_the_network_bot_names_the_venue(self):
        from apps.notifications.models import StaffNotification
        from apps.notifications.services import _venue_name, staff_notification_text

        notification = StaffNotification(title="Стол #1 зовёт", message="Гость: Оля")
        self.assertEqual(_venue_name(self.bot_instance, self.b.id), "Kava B")
        text = staff_notification_text(notification, venue_name="Kava B")
        self.assertTrue(text.startswith("📍 Kava B\n<b>Стол #1 зовёт</b>"))
        own = BotInstance(partner=self.b)
        self.assertEqual(_venue_name(own, self.b.id), "")


class ChoosingTheNetworkBotTests(NetworkBotFixture):
    def test_only_a_bot_of_the_networks_venues_can_be_its_bot(self):
        stranger = venue("Stranger", None)
        foreign = BotInstance.objects.create(
            partner=stranger, display_name="S", username="stranger_bot", token_encrypted=""
        )
        self.network.bot = foreign
        with self.assertRaises(ValidationError):
            self.network.full_clean()

    def test_a_venue_moved_out_takes_its_bot_along_as_its_own(self):
        self.assertTrue(serves_network(self.bot_instance))
        Partner.objects.filter(id=self.a.id).update(network=None)
        self.bot_instance.refresh_from_db()
        self.assertFalse(serves_network(self.bot_instance))
        self.assertIsNone(bot_for_partner(self.b.id))

    def test_the_owner_of_the_network_picks_its_bot_in_admin(self):
        def admin_user(username, owner):
            user = DjangoUser.objects.create_user(
                username=username, password="x", partner=self.a, is_staff=True,
                role=DjangoUser.Role.OWNER if owner else DjangoUser.Role.MANAGER,
            )
            AdminAccessProfile.objects.create(
                user=user, partner=self.a, can_access_admin=True, is_partner_owner=owner,
                preset=AdminAccessPreset.OWNER if owner else AdminAccessPreset.MANAGER,
            )
            return user

        Network.objects.filter(id=self.network.id).update(bot=None)
        url = reverse("admin:partners_network_change", args=[self.network.id])
        manager = admin_user("kava_manager", owner=False)
        AdminAccessProfile.objects.get(user=manager).extra_partners.add(self.b)
        self.client.force_login(manager)
        self.client.post(url, {"name": "Кава Мережа", "bot": str(self.bot_instance.id)})
        self.network.refresh_from_db()
        self.assertIsNone(self.network.bot_id)

        self.client.force_login(admin_user("kava_owner2", owner=True))
        report = self.client.get(reverse("admin:partners_network_report"))
        self.assertContains(report, url)
        response = self.client.post(
            url, {"name": "Кава Мережа", "bot": str(self.bot_instance.id)}
        )
        self.assertEqual(response.status_code, 302)
        self.network.refresh_from_db()
        self.assertEqual(self.network.bot, self.bot_instance)


class NetworkWideBroadcastAndSourceTests(NetworkBotFixture):
    """Step 2b: a campaign to every guest of the network, once per person; a
    source link for the whole network, counted at the venue the guest picks."""

    def _guest(self, partner, telegram_id, *, subscribed=True):
        from apps.users.models import GuestProfile

        account, _created = TelegramAccount.objects.get_or_create(telegram_id=telegram_id)
        return GuestProfile.objects.create(
            partner=partner, telegram_account=account, is_subscribed=subscribed
        )

    def _campaign(self, **fields):
        from apps.notifications.models import BroadcastCampaign

        return BroadcastCampaign.objects.create(
            partner=self.a, name="Новое меню", message="Новое меню во всех кофейнях", **fields
        )

    def _send(self, campaign):
        from apps.notifications.services import send_broadcast_campaign_now
        from apps.notifications.test_delivery import RecordingSender

        sender = RecordingSender()
        with patch("apps.notifications.services._send_telegram_messages", new=sender):
            send_broadcast_campaign_now(campaign)
        campaign.refresh_from_db()
        return sender.sent

    def test_a_network_campaign_reaches_each_person_once_and_respects_opt_outs(self):
        self._guest(self.a, 7741001)
        self._guest(self.b, 7741001)  # the same person in two venues
        self._guest(self.b, 7741002)
        self._guest(self.a, 7741003)
        self._guest(self.b, 7741003, subscribed=False)  # opted out in one venue
        stranger = venue("Stranger", None)
        self._guest(stranger, 7741004)
        campaign = self._campaign(whole_network=True)
        sent = self._send(campaign)
        self.assertEqual(sorted(sent), [7741001, 7741002])
        # One delivery per person: a second one would be resent in a later batch.
        self.assertEqual(campaign.deliveries.count(), 2)

    def test_a_venue_campaign_stays_in_its_venue(self):
        self._guest(self.a, 7741001)
        self._guest(self.b, 7741002)
        self.assertEqual(self._send(self._campaign()), [7741001])

    def test_a_network_campaign_needs_the_network_bot(self):
        from apps.notifications.models import BroadcastCampaign

        Network.objects.filter(id=self.network.id).update(bot=None)
        self._guest(self.b, 7741002)
        campaign = self._campaign(whole_network=True)
        self.assertEqual(self._send(campaign), [])
        self.assertEqual(campaign.status, BroadcastCampaign.Status.FAILED)
        self.assertIn("бота сети", campaign.last_error)

    def test_a_network_source_counts_at_the_venue_the_guest_picks(self):
        from apps.analytics.models import SourceVisit

        TrafficSource.objects.create(
            partner=self.a, name="Instagram сети", code="netinsta", whole_network=True
        )
        # A new guest: the link waits for the pick.
        self.assertIsNone(self.run_update(message=self._message("/start src_netinsta")))
        self.assertEqual(NetworkGuestChoice.objects.get().pending_start, "src_netinsta")
        seen = {}

        async def handler(event, data):
            seen["partner"] = await sync_to_async(resolve_partner_for_bot_token)(TOKEN)
            seen["pending"] = data["pending_start"]

        update = Update(update_id=2, callback_query=self._callback(f"point:{self.b.id.hex}"))
        async_to_sync(PartnerRuntimeMiddleware())(
            handler, update, {"bot": self.bot, "event_from_user": self._user()}
        )
        self.assertEqual((seen["partner"], seen["pending"]), (self.b, "src_netinsta"))
        self.assertEqual(NetworkGuestChoice.objects.get().pending_start, "")

        # The pick handler greets with that link: the visit counts at venue B.
        callback = SimpleNamespace(
            data=f"point:{self.b.id.hex}",
            from_user=SimpleNamespace(
                id=GUEST, username="", first_name="Оля", last_name="", language_code=""
            ),
            answer=AsyncMock(),
            message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock()),
        )

        async def pick():
            with network_update(self.bot_instance.id, 2, self.b.id), partner_for_this_update(
                TOKEN, self.b
            ):
                await points_handlers.point_picked_handler(
                    callback, self.bot, pending_start="src_netinsta"
                )

        async_to_sync(pick)()
        visit = SourceVisit.objects.get()
        self.assertEqual(
            (visit.partner, visit.source.code, visit.is_new_guest), (self.b, "netinsta", True)
        )

    def test_codes_of_network_sources_do_not_repeat_in_the_network(self):
        from apps.analytics.admin import TrafficSourceForm

        TrafficSource.objects.create(partner=self.b, name="Листовка", code="flyer")

        def form(partner, code, whole_network):
            instance = TrafficSourceForm(
                data={"partner": str(partner.id), "name": "X", "code": code,
                      "is_active": "on", "note": "",
                      **({"whole_network": "on"} if whole_network else {})},
            )
            instance.scope_partner_id = partner.id
            return instance

        self.assertFalse(form(self.a, "flyer", True).is_valid())
        self.assertTrue(form(self.a, "flyer", False).is_valid())  # another venue's own code
        TrafficSource.objects.create(partner=self.a, name="Сеть", code="net", whole_network=True)
        self.assertFalse(form(self.b, "net", False).is_valid())
        self.assertFalse(form(venue("Alone", None), "solo", True).is_valid())
