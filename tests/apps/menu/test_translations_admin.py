"""Translations in Admin (catalog step 4, owner 2026-11-07): a field per
text for each guest language the venue has on, except the main one; empty
— the guest sees the original."""

from django.test import TestCase
from django.urls import reverse

from apps.menu.models import (
    MenuBadge,
    MenuCategory,
    MenuItem,
    MenuItemAttribute,
    MenuItemVariant,
    Modifier,
    ModifierGroup,
)
from apps.partners.models import PartnerBotSettings
from apps.users.constants import AdminAccessPreset
from apps.users.models import AdminAccessProfile, User
from tests.apps.menu.test_options import options_module
from tests.apps.menu.test_stock_admin import form_data
from tests.webapp.test_guest_api import make_venue


class TranslationsAdminTests(TestCase):
    def setUp(self):
        self.partner = make_venue("Kava")
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            default_language="uk", language_uk=True, language_ru=False, language_en=True,
            module_showcase_enabled=True,
        )
        options_module(self.partner)
        self.owner = User.objects.create_user(
            username="tr_owner", password="x", partner=self.partner, role=User.Role.OWNER,
            is_staff=True,
        )
        AdminAccessProfile.objects.create(
            user=self.owner, partner=self.partner, preset=AdminAccessPreset.OWNER,
            is_partner_owner=True, can_access_admin=True,
        )
        self.client.force_login(self.owner)
        self.latte = MenuItem.objects.get(partner=self.partner, name="Лате")
        self.big = MenuItemVariant.objects.create(item=self.latte, name="400 мл", price="95.00")
        MenuItemAttribute.objects.create(item=self.latte, name="Обсмаження", value="середнє")

    def url(self, obj):
        return reverse(f"admin:menu_{type(obj).__name__.lower()}_change", args=[obj.pk])

    def save(self, obj, data):
        response = self.client.post(self.url(obj), data)
        self.assertEqual(response.status_code, 302, response.context and [
            response.context["adminform"].form.errors,
            [inline.formset.errors for inline in response.context["inline_admin_formsets"]],
        ])

    def page(self, obj):
        response = self.client.get(self.url(obj))
        self.assertEqual(response.status_code, 200)
        return response

    def test_a_field_per_language_on_except_the_main_one(self):
        fields = self.page(self.latte).context["adminform"].form.fields
        self.assertIn("name_en", fields)
        self.assertIn("description_en", fields)
        self.assertNotIn("name_uk", fields)  # the main language: the original
        self.assertNotIn("name_ru", fields)  # off for guests
        page = self.page(self.latte)
        self.assertContains(page, "Переводы для гостей")
        self.assertContains(page, 'name="variants-0-name_en"')
        self.assertContains(page, 'name="attributes-0-value_en"')
        PartnerBotSettings.objects.filter(partner=self.partner).update(language_en=False)
        self.assertNotIn("name_en", self.page(self.latte).context["adminform"].form.fields)

    def test_the_item_and_its_rows_are_translated(self):
        data = form_data(self.page(self.latte))
        variant = next(key.split("-")[1] for key, value in data.items()
                       if key.startswith("variants-") and key.endswith("-id")
                       and value == str(self.big.pk))
        data.update({
            "name_en": "Latte", "description_en": "Soft coffee",
            f"variants-{variant}-name_en": "400 ml",
            "attributes-0-name_en": "Roast", "attributes-0-value_en": "medium",
        })
        self.save(self.latte, data)
        self.latte.refresh_from_db()
        self.big.refresh_from_db()
        self.assertEqual(self.latte.translations,
                         {"en": {"name": "Latte", "description": "Soft coffee"}})
        self.assertEqual(self.big.translations, {"en": {"name": "400 ml"}})
        self.assertEqual(MenuItemAttribute.objects.get().translations,
                         {"en": {"name": "Roast", "value": "medium"}})
        # Emptied — the original again.
        data = form_data(self.page(self.latte))
        self.assertEqual(data["name_en"], "Latte")
        data["name_en"] = ""
        self.save(self.latte, data)
        self.latte.refresh_from_db()
        self.assertEqual(self.latte.translations, {"en": {"description": "Soft coffee"}})

    def test_a_language_switched_off_keeps_its_translations(self):
        self.latte.translations = {"ru": {"name": "Латте"}}
        self.latte.save()
        data = form_data(self.page(self.latte))
        data["name_en"] = "Latte"
        self.save(self.latte, data)
        self.latte.refresh_from_db()
        self.assertEqual(
            self.latte.translations, {"ru": {"name": "Латте"}, "en": {"name": "Latte"}}
        )

    def test_category_group_add_ons_and_badges(self):
        category = MenuCategory.objects.get(partner=self.partner, name="Кава")
        data = form_data(self.page(category))
        data["name_en"] = "Coffee"
        self.save(category, data)
        category.refresh_from_db()
        self.assertEqual(category.translations, {"en": {"name": "Coffee"}})

        group = ModifierGroup.objects.create(partner=self.partner, name="Молоко")
        oat = Modifier.objects.create(group=group, name="Вівсяне", price="10.00")
        data = form_data(self.page(group))
        index = next(key.split("-")[1] for key, value in data.items()
                     if key.endswith("-id") and value == str(oat.pk))
        prefix = key_prefix(data, index)
        data.update({"name_en": "Milk", f"{prefix}-{index}-name_en": "Oat"})
        self.save(group, data)
        group.refresh_from_db()
        oat.refresh_from_db()
        self.assertEqual((group.translations, oat.translations),
                         ({"en": {"name": "Milk"}}, {"en": {"name": "Oat"}}))

        badge = MenuBadge.objects.create(partner=self.partner, name="Сезонне")
        data = form_data(self.page(badge))
        data["name_en"] = "Seasonal"
        self.save(badge, data)
        badge.refresh_from_db()
        self.assertEqual(badge.translations, {"en": {"name": "Seasonal"}})

    def test_a_too_long_translation_is_a_form_error(self):
        data = form_data(self.page(self.latte))
        data["name_en"] = "L" * 256
        response = self.client.post(self.url(self.latte), data)
        self.assertEqual(response.status_code, 200)
        self.assertIn("name_en", response.context["adminform"].form.errors)


def key_prefix(data, index) -> str:
    return next(key.rsplit("-", 2)[0] for key in data
                if key.endswith(f"-{index}-id") and not key.startswith("variants"))
