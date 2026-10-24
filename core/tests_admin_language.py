"""Admin speaks Russian: every label people see in it (owner decision, CLAUDE.md)."""

import re

from django.apps import apps
from django.test import SimpleTestCase
from django.utils import translation
from django.utils.translation import gettext

CYRILLIC = re.compile("[а-яА-ЯёЁ]")
# Names that stay as they are: brands and technical identifiers.
ALLOWED = {
    "Meest", "Polling", "Webhook", "Telegram", "Telegram ID", "@username", "Botera Shop",
    # A language is named in itself (PQ-040).
    "English",
}


class AdminLanguageTests(SimpleTestCase):
    def _domain_models(self):
        for config in apps.get_app_configs():
            if config.name.startswith("apps."):
                yield from config.get_models()

    def test_models_fields_and_choices_have_russian_labels(self):
        english = []
        for model in self._domain_models():
            if not CYRILLIC.search(str(model._meta.verbose_name_plural)):
                english.append(f"{model.__name__} (модель)")
            for field in model._meta.concrete_fields:
                if field.name == "id":
                    continue
                label = str(field.verbose_name)
                if not CYRILLIC.search(label) and label not in ALLOWED:
                    english.append(f"{model.__name__}.{field.name}: {label}")
                for _value, choice in field.choices or ():
                    if not CYRILLIC.search(str(choice)) and str(choice) not in ALLOWED:
                        english.append(f"{model.__name__}.{field.name} = {choice}")
        self.assertEqual(english, [])

    def test_app_sections_are_russian(self):
        english = [
            config.label
            for config in apps.get_app_configs()
            if config.name.startswith(("apps.", "bot"))
            and not CYRILLIC.search(str(config.verbose_name))
        ]
        self.assertEqual(english, [])

    def test_admin_theme_strings_are_translated(self):
        with translation.override("ru"):
            self.assertEqual(gettext("Toggle sidebar"), "Свернуть меню")
            self.assertEqual(gettext("Reset filters"), "Сбросить фильтры")
