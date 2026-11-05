"""How the bot opens the Mini App (PQ-048): a button in the menu message and
the chat menu button, only where the module is on; the bot stays the same."""

import asyncio

from aiogram.methods import SetChatMenuButton
from asgiref.sync import async_to_sync, sync_to_async
from django.test import TestCase, override_settings

from apps.partners.mini_app import menu_buttons, mini_app_link
from apps.partners.models import BotInstance, Network, Partner, PartnerBotSettings
from bot.supervisor import BotSupervisor
from bot.tests_guest_flow import GuestFlowFixture, _buttons, _data
from bot.tests_supervisor import FakeBot, make_bot, make_partner

BASE = "https://shop.example.com"


class MenuMessageTests(GuestFlowFixture):
    def bot_hex(self):
        return BotInstance.objects.get(partner=self.partner).id.hex

    def test_no_button_while_the_module_is_off(self):
        with override_settings(WEBAPP_BASE_URL=BASE):
            _text, markup = self._menu()
        self.assertFalse(any(button.web_app for button in _buttons(markup)))

    def test_the_button_opens_the_mini_app_and_the_rest_stays(self):
        with override_settings(WEBAPP_BASE_URL=BASE):
            _text, before = self._menu()
            PartnerBotSettings.objects.filter(partner=self.partner).update(
                module_webapp_enabled=True
            )
            _text, after = self._menu()
        first = after.inline_keyboard[0][0]
        self.assertEqual(first.web_app.url, f"{BASE}/app/{self.bot_hex()}/")
        self.assertEqual(after.inline_keyboard[1:], before.inline_keyboard)
        self.assertEqual(_data(after)[1:], _data(before))

    def test_without_a_public_https_address_nothing_is_offered(self):
        PartnerBotSettings.objects.filter(partner=self.partner).update(module_webapp_enabled=True)
        for base in ("", "http://shop.example.com"):
            with override_settings(WEBAPP_BASE_URL=base):
                _text, markup = self._menu()
            self.assertFalse(any(button.web_app for button in _buttons(markup)), base)

    def test_the_cart_module_is_needed(self):
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            module_webapp_enabled=True, module_cart_enabled=False
        )
        with override_settings(WEBAPP_BASE_URL=BASE):
            self.assertEqual(
                mini_app_link(BotInstance.objects.get(partner=self.partner).id, self.partner.id),
                "",
            )


class MenuButtonFakeBot(FakeBot):
    def __init__(self, token):
        super().__init__(token)
        self.menu_buttons = []

    async def __call__(self, method, request_timeout=None):
        if isinstance(method, SetChatMenuButton):
            self.menu_buttons.append(method.menu_button)
            return True
        return await super().__call__(method, request_timeout)


def settings_for(partner, **fields):
    return PartnerBotSettings.objects.create(
        partner=partner, module_cart_enabled=True, **fields
    )


@override_settings(WEBAPP_BASE_URL=BASE)
class ChatMenuButtonTests(TestCase):
    def setUp(self):
        FakeBot.behaviour = {}
        self.made = {}

    def factory(self, token):
        bot = MenuButtonFakeBot(token)
        self.made[token] = bot
        return bot

    def run_checks(self, *steps):
        async def scenario():
            supervisor = BotSupervisor(bot_factory=self.factory)
            for step in (None, *steps):
                if step is not None:
                    await sync_to_async(step)()
                await supervisor.reconcile()
                await asyncio.sleep(0.05)
            for instance_id in list(supervisor.running):
                await supervisor._stop_bot(instance_id)

        async_to_sync(scenario)()

    def calls(self, bot):
        return self.made[bot.token].menu_buttons

    def test_set_where_on_reset_when_turned_off_never_touched_elsewhere(self):
        with_app = make_bot(1)
        settings = settings_for(with_app.partner, module_webapp_enabled=True)
        without = make_bot(2)
        settings_for(without.partner)

        def turn_off():
            settings.module_webapp_enabled = False
            settings.save()

        self.run_checks(lambda: None, turn_off)
        kinds = [button.type for button in self.calls(with_app)]
        self.assertEqual(kinds, ["web_app", "default"])  # once each, not every minute
        self.assertEqual(
            self.calls(with_app)[0].web_app.url, f"{BASE}/app/{with_app.id.hex}/"
        )
        self.assertEqual(self.calls(without), [])

    def test_a_network_bot_offers_it_when_a_working_venue_has_it(self):
        network = Network.objects.create(name="Ночь")
        home = make_partner(10)
        other = make_partner(11)
        Partner.objects.filter(id__in=[home.id, other.id]).update(network=network)
        bot = make_bot(10, partner=home)
        network.bot = bot
        network.save()
        settings_for(home)
        settings_for(other, module_webapp_enabled=True)
        self.assertIsNotNone(menu_buttons([bot.id])[str(bot.id)])
        Partner.objects.filter(id=other.id).update(status=Partner.Status.SUSPENDED)
        self.assertIsNone(menu_buttons([bot.id])[str(bot.id)])
