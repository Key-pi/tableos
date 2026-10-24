"""Languages of texts: the catalog, the owner's own texts, isolation (PQ-040)."""

import asyncio

from asgiref.sync import async_to_sync, sync_to_async
from django.test import SimpleTestCase, override_settings
from django.utils import translation

from core.i18n import _, default_language, gettext_in, normalize_language, texts, variants

LANGUAGE = "🌐 Язык"
DONE = "Готово, язык бота: {language}."


class CatalogTests(SimpleTestCase):
    def test_russian_is_the_text_itself_and_others_come_from_the_catalog(self):
        self.assertEqual(gettext_in("ru", LANGUAGE), "🌐 Язык")
        self.assertEqual(gettext_in("uk", LANGUAGE), "🌐 Мова")
        self.assertEqual(gettext_in("en", LANGUAGE), "🌐 Language")

    def test_values_are_put_in_after_translation(self):
        with translation.override("uk"):
            self.assertEqual(_(DONE, language="English"), "Готово, мова бота: English.")

    def test_language_codes_are_normalized(self):
        self.assertEqual(normalize_language("uk-UA"), "uk")
        self.assertEqual(normalize_language("EN_us"), "en")
        self.assertEqual(normalize_language("de"), "")
        self.assertEqual(normalize_language(None), "")

    @override_settings(LANGUAGE_CODE="uk")
    def test_platform_default_language_follows_settings(self):
        self.assertEqual(default_language(), "uk")

    @override_settings(LANGUAGE_CODE="de")
    def test_unknown_platform_language_falls_back_to_ukrainian(self):
        self.assertEqual(default_language(), "uk")


class OwnerTextTests(SimpleTestCase):
    def test_owner_text_wins_in_its_language_only(self):
        own = {("uk", LANGUAGE): "🌐 Обрати мову"}
        with texts("uk", own):
            self.assertEqual(_(LANGUAGE), "🌐 Обрати мову")
        with texts("en", own):
            self.assertEqual(_(LANGUAGE), "🌐 Language")
        # Outside the update the owner's texts are gone.
        self.assertEqual(gettext_in("uk", LANGUAGE), "🌐 Мова")

    def test_owner_text_gets_the_values_too(self):
        with texts("ru", {("ru", DONE): "Язык: {language}!"}):
            self.assertEqual(_(DONE, language="Русский"), "Язык: Русский!")

    def test_broken_owner_text_falls_back_to_the_catalog(self):
        with texts("uk", {("uk", DONE): "Мова {unknown} {"}):
            self.assertEqual(_(DONE, language="English"), "Готово, мова бота: English.")

    def test_variants_cover_every_language_and_owner_texts(self):
        with texts("uk", {("en", LANGUAGE): "🌐 Lang"}):
            self.assertEqual(variants(LANGUAGE), {"🌐 Мова", "🌐 Язык", "🌐 Lang"})

    def test_concurrent_updates_keep_their_own_language_and_texts(self):
        async def update(language, own, delay):
            with texts(language, own):
                await asyncio.sleep(delay)
                in_thread = await sync_to_async(_)(LANGUAGE)
                return _(LANGUAGE), in_thread

        async def both():
            return await asyncio.gather(
                update("uk", {("uk", LANGUAGE): "А"}, 0.03),
                update("en", {}, 0.01),
                update("uk", {}, 0.02),
            )

        self.assertEqual(
            async_to_sync(both)(),
            [("А", "А"), ("🌐 Language", "🌐 Language"), ("🌐 Мова", "🌐 Мова")],
        )
