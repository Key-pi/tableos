"""Bot buttons and messages per language, the owner's own texts (PQ-040)."""

from types import SimpleNamespace

from asgiref.sync import async_to_sync
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase

from apps.partners.admin import BotTextAdmin
from apps.partners.bot_texts import BUTTON_TEXTS, WHOLE_TEXTS
from apps.partners.models import BotText, Partner, PartnerBotSettings, text_key
from apps.users.constants import AdminAccessPreset
from apps.users.models import AdminAccessProfile, User
from bot.filters.content import PartnerButtonFilter
from bot.handlers import remote_checkout
from bot.services.content import BotContent
from core.i18n import LANGUAGES, _, gettext_in, texts

MENU = BUTTON_TEXTS["button_menu_label"]
PROFILE = BUTTON_TEXTS["button_my_profile"]
WELCOME = "Добро пожаловать в {partner_name}."


def _matches(label_attr, text):
    return async_to_sync(PartnerButtonFilter(label_attr).__call__)(SimpleNamespace(text=text))


class ContentLanguageTests(TestCase):
    def test_buttons_and_messages_follow_the_language(self):
        with texts("uk"):
            content = BotContent.defaults()
        self.assertEqual(content.button_menu_label, "Відкрити меню")
        self.assertEqual(content.welcome_message(partner_name="Кава"), "Ласкаво просимо до Кава.")
        with texts("en"):
            self.assertEqual(BotContent.defaults().button_cart_label, "Cart")

    def test_the_owners_own_texts_replace_the_catalog(self):
        own = {("uk", MENU): "Меню закладу", ("uk", WELCOME): "Вітаємо в {partner_name}!"}
        with texts("uk", own):
            content = BotContent.defaults()
        self.assertEqual(content.button_menu_label, "Меню закладу")
        self.assertEqual(content.welcome_message(partner_name="<Кава>"), "Вітаємо в &lt;Кава&gt;!")

    def test_whole_help_text_only_when_the_owner_wrote_one(self):
        help_key = WHOLE_TEXTS["ordering_help_message_template"]
        with texts("ru"):
            self.assertIn("Как заказать", BotContent.defaults().ordering_help_message())
        with texts("ru", {("ru", help_key): "Звоните нам."}):
            self.assertEqual(BotContent.defaults().ordering_help_message(), "Звоните нам.")

    def test_a_button_is_recognized_in_every_language(self):
        for language in LANGUAGES:
            self.assertTrue(_matches("button_menu_label", gettext_in(language, MENU)))
        self.assertFalse(_matches("button_menu_label", gettext_in("uk", PROFILE)))
        with texts("uk", {("uk", MENU): "Меню закладу"}):
            self.assertTrue(_matches("button_menu_label", "Меню закладу"))
            self.assertFalse(_matches("button_my_profile", "Меню закладу"))

    def test_catalog_buttons_never_share_a_label_across_languages(self):
        seen = {}
        for field, msgid in BUTTON_TEXTS.items():
            for language in LANGUAGES:
                label = gettext_in(language, msgid)
                self.assertNotIn(label, {k: v for k, v in seen.items() if v != field}, label)
                seen[label] = field

    def test_address_prompt_is_recognized_in_any_language(self):
        for language in LANGUAGES:
            with texts(language):
                prompt = _(remote_checkout.ADDRESS_PROMPT) + "\n…"
            bot_message = SimpleNamespace(text=prompt, from_user=SimpleNamespace(is_bot=True))
            reply = SimpleNamespace(text="Хрещатик 1", reply_to_message=bot_message)
            self.assertTrue(remote_checkout._is_address_answer(reply), language)


class OwnTextValidationTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(name="T", slug="texts", status=Partner.Status.ACTIVE)

    def _text(self, msgid, text, language="uk"):
        return BotText(partner=self.partner, language=language, msgid=msgid, text=text)

    def _errors(self, obj):
        with self.assertRaises(ValidationError) as ctx:
            obj.full_clean()
        return " ".join(ctx.exception.messages)

    def test_a_good_text_is_accepted_and_keyed_by_the_catalog_text(self):
        obj = self._text(WELCOME, "Вітаємо в <b>{partner_name}</b>!")
        obj.full_clean()
        obj.save()
        self.assertEqual(obj.key, text_key(WELCOME))

    def test_unknown_catalog_text_placeholder_or_markup_is_refused(self):
        self.assertIn("нет в каталоге", self._errors(self._text("Нет такого", "x")))
        self.assertIn("{partner_name}", self._errors(self._text(WELCOME, "Вітаємо {name}")))
        self.assertIn("Разметка", self._errors(self._text(WELCOME, "<b>Вітаємо")))
        self.assertIn("Разметка", self._errors(self._text(WELCOME, "<script>x</script>")))
        self.assertIn("пуст", self._errors(self._text(WELCOME, "  ")))

    def test_button_labels_stay_one_line_and_unique(self):
        self.assertIn("одна строка", self._errors(self._text(MENU, "Меню\nзакладу")))
        # The catalog label of another button in this language.
        self.assertIn("другой кнопки", self._errors(self._text(MENU, "Мій профіль")))
        BotText.objects.create(
            partner=self.partner, language="uk", msgid=PROFILE, text="Меню закладу"
        )
        # Another button's own label.
        self.assertIn("другой кнопки", self._errors(self._text(MENU, "Меню закладу")))
        # Other languages and other venues do not clash.
        self._text(MENU, "Меню закладу", language="en").full_clean()


class OwnTextAdminTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(name="A", slug="admin-texts", status="active")
        PartnerBotSettings.objects.create(
            partner=self.partner, default_language="uk", language_en=False
        )
        self.owner = User.objects.create_user(
            username="texts_owner", password="x", partner=self.partner, role=User.Role.OWNER
        )
        AdminAccessProfile.objects.create(
            user=self.owner,
            partner=self.partner,
            preset=AdminAccessPreset.OWNER,
            is_partner_owner=True,
            can_access_admin=True,
        )
        self.model_admin = BotTextAdmin(BotText, admin.site)

    def _request(self, method="get", path="/admin/", data=None):
        request = getattr(RequestFactory(), method)(path, data or {})
        request.user = self.owner
        return request

    def test_catalog_lists_texts_in_the_venues_languages(self):
        BotText.objects.create(partner=self.partner, language="uk", msgid=MENU, text="Меню!")
        response = self.model_admin.catalog_view(self._request(data={"q": "меню!"}))
        self.assertEqual(response.context_data["languages"], ["Українська", "Русский"])
        rows = list(response.context_data["page"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cells"][0]["text"], "Меню!")
        self.assertTrue(rows[0]["cells"][0]["own"])
        self.assertIn(f"key={text_key(MENU)}", rows[0]["cells"][1]["url"])

    def test_add_form_is_prefilled_and_scoped_to_the_venue(self):
        request = self._request(data={"key": text_key(WELCOME), "language": "uk"})
        initial = self.model_admin.get_changeform_initial_data(request)
        self.assertEqual(initial["msgid"], WELCOME)
        form_class = self.model_admin.get_form(request)
        self.assertNotIn("partner", form_class.base_fields)
        form = form_class(data={"language": "uk", "msgid": WELCOME, "text": "Вітаємо!"})
        self.assertTrue(form.is_valid(), form.errors)
        obj = form.save()
        self.assertEqual(obj.partner, self.partner)
        again = form_class(data={"language": "uk", "msgid": WELCOME, "text": "Ще раз"})
        self.assertFalse(again.is_valid())
        self.assertIn("уже есть свой вариант", str(again.errors))
