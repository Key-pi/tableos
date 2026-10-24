"""Which language the bot speaks (PQ-040)."""

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from apps.partners.languages import choose_language, enabled_languages, text_overrides
from apps.partners.models import BotText, Partner, PartnerBotSettings, text_key


class LanguageChoiceTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(name="L", slug="lang", status=Partner.Status.ACTIVE)
        self.settings = PartnerBotSettings.objects.create(
            partner=self.partner, default_language="uk", language_en=False
        )

    def test_main_language_comes_first_then_the_others_enabled(self):
        self.assertEqual(enabled_languages(self.settings), ("uk", "ru"))
        self.settings.default_language = "ru"
        self.assertEqual(enabled_languages(self.settings), ("ru", "uk"))

    def test_order_guest_choice_staff_telegram_main(self):
        s = self.settings
        self.assertEqual(choose_language(s, guest_choice="ru", staff_choice="en"), "ru")
        self.assertEqual(choose_language(s, staff_choice="en", telegram="ru"), "en")
        self.assertEqual(choose_language(s, telegram="ru-RU"), "ru")
        self.assertEqual(choose_language(s), "uk")

    def test_disabled_languages_are_skipped_for_guests(self):
        self.assertEqual(choose_language(self.settings, guest_choice="en"), "uk")
        self.assertEqual(choose_language(self.settings, telegram="en"), "uk")
        self.assertEqual(choose_language(self.settings, telegram="de"), "uk")

    def test_main_language_must_be_enabled(self):
        self.settings.default_language = "en"
        with self.assertRaises(ValidationError) as ctx:
            self.settings.full_clean()
        self.assertIn("default_language", ctx.exception.error_dict)

    @override_settings(LANGUAGE_CODE="uk")
    def test_new_bot_speaks_the_platform_language_with_all_languages_on(self):
        other = Partner.objects.create(name="N", slug="new-lang", status=Partner.Status.ACTIVE)
        settings = PartnerBotSettings.objects.create(partner=other)
        self.assertEqual(settings.default_language, "uk")
        self.assertEqual(enabled_languages(settings), ("uk", "ru", "en"))

    def test_owner_texts_are_per_partner_and_language(self):
        BotText.objects.create(partner=self.partner, language="uk", msgid="Корзина", text="Кошик")
        other = Partner.objects.create(name="O", slug="other-lang", status=Partner.Status.ACTIVE)
        BotText.objects.create(partner=other, language="uk", msgid="Корзина", text="Візок")
        self.assertEqual(text_overrides(self.partner.id), {("uk", "Корзина"): "Кошик"})
        self.assertEqual(
            BotText.objects.get(partner=self.partner).key, text_key("Корзина")
        )
