"""The bot speaks the person's language (PQ-040)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from asgiref.sync import async_to_sync
from django.test import TestCase

from apps.employees.models import EmployeeProfile
from apps.partners.models import BotInstance, BotText, Partner, PartnerBotSettings
from apps.users.models import GuestProfile, TelegramAccount, User
from apps.users.services import get_or_create_guest_profile
from bot.handlers import profile
from bot.keyboards.profile import build_profile_summary_keyboard
from bot.middlewares.partner_runtime import PartnerRuntimeMiddleware
from bot.services.runtime import BotRuntimeConfig, register_runtime_configs
from core.i18n import _

GUEST = 7500001
LANGUAGE = "🌐 Язык"


def _buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


class LanguageFixture(TestCase):
    token = "language-bot-token"

    def setUp(self):
        self.partner = Partner.objects.create(
            name="Мова", slug="lang-bot", status=Partner.Status.ACTIVE
        )
        self.settings = PartnerBotSettings.objects.create(
            partner=self.partner, default_language="uk"
        )
        instance = BotInstance.objects.create(
            partner=self.partner, display_name="L", username="lang_bot", token_encrypted=""
        )
        register_runtime_configs(
            [
                BotRuntimeConfig(
                    partner_id=str(self.partner.id),
                    bot_instance_id=str(instance.id),
                    username=instance.username,
                    token=self.token,
                )
            ]
        )
        self.bot = SimpleNamespace(token=self.token)

    def tearDown(self):
        register_runtime_configs([])


class MiddlewareLanguageTests(LanguageFixture):
    def _run(self, telegram_id=GUEST, telegram_language=""):
        seen = {}

        async def handler(event, data):
            seen["text"] = _(LANGUAGE)
            seen["language"] = data["language"]

        async_to_sync(PartnerRuntimeMiddleware().__call__)(
            handler,
            SimpleNamespace(message=None, callback_query=None),
            {
                "bot": self.bot,
                "event_from_user": SimpleNamespace(
                    id=telegram_id, language_code=telegram_language
                ),
            },
        )
        return seen

    def test_telegram_language_when_the_venue_offers_it(self):
        self.assertEqual(
            self._run(telegram_language="en"), {"text": "🌐 Language", "language": "en"}
        )
        self.assertEqual(self._run(telegram_language="de")["language"], "uk")

    def test_main_language_when_telegram_language_is_off(self):
        PartnerBotSettings.objects.filter(id=self.settings.id).update(language_en=False)
        self.assertEqual(self._run(telegram_language="en")["text"], "🌐 Мова")

    def test_the_guests_own_choice_wins(self):
        get_or_create_guest_profile(partner_id=self.partner.id, telegram_id=GUEST)
        GuestProfile.objects.update(language="ru")
        self.assertEqual(self._run(telegram_language="en")["language"], "ru")

    def test_staff_member_gets_their_own_language(self):
        user = User.objects.create_user(
            username="lang_staff", password="x", partner=self.partner, language="en"
        )
        EmployeeProfile.objects.create(
            partner=self.partner,
            user=user,
            telegram_account=TelegramAccount.objects.create(telegram_id=7500900),
        )
        self.assertEqual(self._run(telegram_id=7500900, telegram_language="uk")["language"], "en")
        user.is_active = False
        user.save(update_fields=["is_active"])
        self.assertEqual(self._run(telegram_id=7500900, telegram_language="uk")["language"], "uk")

    def test_the_owners_own_text_is_used(self):
        BotText.objects.create(partner=self.partner, language="uk", msgid=LANGUAGE, text="Мова?")
        self.assertEqual(self._run()["text"], "Мова?")


class GuestLanguageSwitchTests(LanguageFixture):
    def setUp(self):
        super().setUp()
        self.guest = get_or_create_guest_profile(partner_id=self.partner.id, telegram_id=GUEST)

    def _press(self, data):
        callback = SimpleNamespace(
            data=data,
            from_user=SimpleNamespace(id=GUEST, language_code="uk"),
            answer=AsyncMock(),
            message=SimpleNamespace(answer=AsyncMock(), edit_text=AsyncMock()),
        )
        async_to_sync(profile.guest_language_callback)(callback, self.bot)
        return callback

    def test_profile_offers_the_switch_only_with_several_languages(self):
        labels = [b.text for b in _buttons(build_profile_summary_keyboard(
            bonuses_label="Бонусы", show_language=True))]
        self.assertIn("🌐 Язык", labels)
        PartnerBotSettings.objects.filter(id=self.settings.id).update(
            language_ru=False, language_en=False
        )
        self.partner.refresh_from_db()
        self.assertFalse(profile._offers_language_choice(Partner.objects.get(id=self.partner.id)))

    def test_menu_lists_the_venues_languages(self):
        callback = self._press("guestlang:menu")
        markup = callback.message.edit_text.call_args.kwargs["reply_markup"]
        data = [b.callback_data for b in _buttons(markup)]
        self.assertEqual(
            data[:4], ["guestlang:uk", "guestlang:ru", "guestlang:en", "guestlang:auto"]
        )
        # Nothing chosen yet: «automatic» is marked.
        self.assertTrue(_buttons(markup)[3].text.startswith("✓ "))

    def test_choosing_a_language_saves_it_and_answers_in_it(self):
        callback = self._press("guestlang:en")
        self.guest.refresh_from_db()
        self.assertEqual(self.guest.language, "en")
        self.assertEqual(
            callback.message.answer.call_args.args[0], "Done, the bot language is English."
        )
        self.assertIn("reply_markup", callback.message.answer.call_args.kwargs)
        self._press("guestlang:auto")
        self.guest.refresh_from_db()
        self.assertEqual(self.guest.language, "")

    def test_a_language_the_venue_turned_off_is_refused(self):
        PartnerBotSettings.objects.filter(id=self.settings.id).update(language_en=False)
        callback = self._press("guestlang:en")
        self.assertIn("не включило", callback.answer.call_args.args[0])
        self.guest.refresh_from_db()
        self.assertEqual(self.guest.language, "")
