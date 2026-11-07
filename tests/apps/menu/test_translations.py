"""The catalog in the guest's language (catalog step 4, owner 2026-11-07):
the owner translates in Admin; a guest sees a translation where there is one
and the original where there is none; order lines keep the original for the
staff and are named again in the guest's language on the guest's screens."""

from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.menu.models import MenuItem, MenuItemVariant, Modifier, ModifierGroup
from apps.menu.options import resolve_choice
from apps.menu.translations import (
    clean_translations,
    has_translation,
    line_names_in,
    set_translation,
    translated,
)
from core.i18n import texts
from tests.apps.menu.test_options import options_module
from tests.webapp.test_guest_api import make_venue


class TranslatedTests(TestCase):
    def setUp(self):
        self.partner = make_venue("Kava")
        self.latte = MenuItem.objects.get(partner=self.partner, name="Лате")

    def test_a_translation_where_there_is_one(self):
        self.latte.translations = {"en": {"name": "Latte", "description": "  "}}
        self.latte.description = "Ніжна кава"
        self.assertEqual(translated(self.latte, "name", "en"), "Latte")
        self.assertEqual(translated(self.latte, "description", "en"), "Ніжна кава")  # blank
        self.assertEqual(translated(self.latte, "name", "ru"), "Лате")  # none
        self.assertTrue(has_translation(self.latte, "en"))
        self.assertFalse(has_translation(self.latte, "ru"))

    def test_the_readers_language_by_default(self):
        self.latte.translations = {"en": {"name": "Latte"}}
        with texts("en"):
            self.assertEqual(translated(self.latte, "name"), "Latte")
        with texts("uk"):
            self.assertEqual(translated(self.latte, "name"), "Лате")

    def test_set_translation_keeps_the_rest(self):
        set_translation(self.latte, "en", "name", "Latte")
        set_translation(self.latte, "en", "description", "Soft")
        set_translation(self.latte, "en", "description", "")
        self.assertEqual(self.latte.translations, {"en": {"name": "Latte"}})
        set_translation(self.latte, "en", "name", " ")
        self.assertEqual(self.latte.translations, {})


class CleanTests(TestCase):
    def setUp(self):
        self.partner = make_venue("Kava")
        self.latte = MenuItem.objects.get(partner=self.partner, name="Лате")

    def test_known_languages_and_fields_only(self):
        for bad in ({"de": {"name": "Latte"}}, {"en": {"price": "1"}}, {"en": "Latte"},
                    {"en": {"name": 5}}, ["Latte"]):
            self.latte.translations = bad
            with self.assertRaises(ValidationError, msg=bad):
                clean_translations(self.latte)

    def test_within_the_limit_of_the_original(self):
        self.latte.translations = {"en": {"name": "L" * 256}}
        with self.assertRaises(ValidationError):
            self.latte.full_clean()

    def test_empty_entries_are_dropped(self):
        self.latte.translations = {"en": {"name": " Latte ", "description": ""}, "ru": {}}
        self.latte.full_clean()
        self.assertEqual(self.latte.translations, {"en": {"name": "Latte"}})


class LineNameTests(TestCase):
    def setUp(self):
        self.partner = make_venue("Kava")
        options_module(self.partner)
        self.latte = MenuItem.objects.get(partner=self.partner, name="Лате")
        self.big = MenuItemVariant.objects.create(item=self.latte, name="400 мл", price="95.00")
        group = ModifierGroup.objects.create(partner=self.partner, name="Молоко", max_choices=2)
        group.items.add(self.latte)
        self.oat = Modifier.objects.create(group=group, name="Вівсяне", price="10.00")
        self.syrup = Modifier.objects.create(group=group, name="Карамель", price="10.00")
        self.choice = resolve_choice(
            self.latte, self.big.id.hex, [self.oat.id.hex, self.syrup.id.hex]
        )

    def line(self, **overrides):
        data = dict(
            menu_item=self.latte, menu_item_id=self.latte.id, variant=self.big,
            variant_id=self.big.id, modifiers=self.choice.snapshot(), item_name=self.choice.name,
        )
        data.update(overrides)
        return SimpleNamespace(**data)

    def test_the_line_in_the_guests_language(self):
        self.assertEqual(self.choice.name, "Лате, 400 мл (Вівсяне, Карамель)")
        self.latte.translations = {"en": {"name": "Latte"}}
        self.big.translations = {"en": {"name": "400 ml"}}
        self.oat.translations = {"en": {"name": "Oat"}}
        self.oat.save()
        self.choice = resolve_choice(
            self.latte, self.big.id.hex, [self.oat.id.hex, self.syrup.id.hex]
        )
        line = self.line()
        self.assertEqual(line_names_in([line], "en"), ["Latte, 400 ml (Oat, Карамель)"])
        self.assertEqual(line_names_in([line], "uk"), ["Лате, 400 мл (Вівсяне, Карамель)"])

    def test_nothing_translated_the_stored_name(self):
        line = self.line(item_name="Лате, 400 мл (Вівсяне, Карамель)")
        self.assertEqual(line_names_in([line], "en"), [line.item_name])

    def test_a_line_that_no_longer_matches_keeps_its_name(self):
        self.latte.translations = {"en": {"name": "Latte"}}
        renamed = self.line(item_name="Лате велике, 400 мл (Вівсяне, Карамель)")
        self.assertEqual(line_names_in([renamed], "en"), [renamed.item_name])
        fee = SimpleNamespace(menu_item=None, menu_item_id=None, item_name="Доставка")
        self.assertEqual(line_names_in([fee], "en"), ["Доставка"])
