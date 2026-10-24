"""The Admin speaks each user's language; Ukrainian by default (PQ-040)."""

import re

from django.test import TestCase, override_settings
from django.utils import translation

from apps.orders.models import Order
from apps.users.models import User
from core.settings.env import AppSettings

CYRILLIC = re.compile("[а-яА-ЯёЁіІїЇєЄ]")


@override_settings(LANGUAGE_CODE="uk")
class AdminLanguageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="root", password="x", email="root@example.com"
        )
        self.client.force_login(self.user)

    def _index(self):
        return self.client.get("/admin/")

    def test_platform_default_is_ukrainian(self):
        self.assertEqual(AppSettings.model_fields["language_code"].default, "uk")
        response = self._index()
        self.assertEqual(response["Content-Language"], "uk")
        self.assertContains(response, "Замовлення")

    def test_each_user_sees_their_own_language(self):
        for language, heading in (("en", "Orders"), ("ru", "Заказы"), ("uk", "Замовлення")):
            with self.subTest(language=language):
                User.objects.filter(id=self.user.id).update(language=language)
                response = self._index()
                self.assertEqual(response["Content-Language"], language)
                self.assertContains(response, heading)

    def test_the_header_switch_saves_the_language(self):
        response = self.client.post(
            "/admin/language/", {"language": "en", "next": "/admin/orders/order/"}
        )
        self.assertRedirects(response, "/admin/orders/order/", fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertEqual(self.user.language, "en")

    def test_the_switch_refuses_unknown_languages_and_outside_links(self):
        response = self.client.post(
            "/admin/language/", {"language": "de", "next": "https://evil.example/"}
        )
        self.assertEqual(response["Location"], "/admin/")
        self.user.refresh_from_db()
        self.assertEqual(self.user.language, "")

    def test_the_switch_needs_a_staff_login(self):
        self.client.logout()
        response = self.client.post("/admin/language/", {"language": "en"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])
        self.assertEqual(self.client.get("/admin/language/").status_code, 405)

    def test_model_labels_are_translated(self):
        for language, label in (("uk", "Замовлення"), ("ru", "Заказы"), ("en", "Orders")):
            with self.subTest(language=language), translation.override(language):
                self.assertEqual(str(Order._meta.verbose_name_plural), label)
                self.assertEqual(str(Order.Status.ACCEPTED.label), {
                    "uk": "Прийнято", "ru": "Принят", "en": "Accepted"
                }[language])


class AdminLabelsAreLazyTests(TestCase):
    """Labels fixed at import (decorators, fieldsets) must follow each user."""

    def _texts(self):
        from django.contrib import admin

        for model_admin in admin.site._registry.values():
            owner = type(model_admin).__name__
            for name in dir(type(model_admin)):
                attr = getattr(type(model_admin), name, None)
                for key in ("short_description",):
                    if hasattr(attr, key):
                        yield f"{owner}.{name}", getattr(attr, key)
            for title, _options in model_admin.fieldsets or ():
                if title:
                    yield f"{owner} fieldset", title
            for inline in model_admin.inlines:
                for value in (inline.verbose_name, inline.verbose_name_plural):
                    if value:
                        yield f"{inline.__name__}", value

    def test_admin_labels_are_translated_per_request(self):
        from django.utils.functional import Promise

        fixed = [
            f"{where}: {text}"
            for where, text in self._texts()
            if isinstance(text, str) and not isinstance(text, Promise) and CYRILLIC.search(text)
        ]
        self.assertEqual(fixed, [])
