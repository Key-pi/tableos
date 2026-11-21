"""The menu from Poster in Admin (PQ-058, step 3): «Обновить меню» on the
Poster page, and what Poster owns of a menu row is read only while the venue
works with Poster. Poster is replaced by a mock of ``menu_sync.fetch_menu``."""

from decimal import Decimal
from unittest import mock

from django.contrib import admin
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.integrations.poster import menu_sync
from apps.integrations.poster.client import PosterError
from apps.integrations.poster.menu_sync import sync_menu
from apps.integrations.poster.models import PosterConnection, PosterRef
from apps.menu.admin import MenuItemVariantInline, ModifierInline
from apps.menu.models import MenuCategory, MenuItem, ModifierGroup
from apps.partners.models import PartnerBotSettings
from apps.users.constants import AdminAccessPreset
from tests.apps.integrations.poster.test_connection import _manager
from tests.apps.integrations.poster.test_menu_sync import connect, product, trial_menu
from tests.apps.users.test_admin_navigation import admin_user
from tests.webapp.test_guest_api import make_venue

Kind = PosterRef.Kind


class MenuAdminFixture(TestCase):
    def setUp(self):
        self.partner = make_venue("Kava")
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            module_options_enabled=True,
        )
        self.connection = connect(self.partner)
        self.menu = trial_menu()
        self.fetch_mock = mock.patch.object(
            menu_sync, "fetch_menu", side_effect=lambda token: self.menu,
        ).start()
        self.addCleanup(mock.patch.stopall)
        sync_menu(connection_id=self.connection.pk)
        self.owner = admin_user(self.partner, "poster_admin_owner", AdminAccessPreset.OWNER,
                                owner=True)
        self.client.force_login(self.owner)
        self.request = RequestFactory().get("/")
        self.request.user = self.owner

    def linked(self, model, kind, poster_id):
        ref = PosterRef.objects.get(partner=self.partner, kind=kind, poster_id=str(poster_id))
        return model.objects.get(pk=ref.object_id)

    def readonly(self, obj):
        return admin.site._registry[type(obj)].get_readonly_fields(self.request, obj)


class PosterFieldsReadOnlyTests(MenuAdminFixture):
    def test_a_poster_item_shows_poster_fields_read_only(self):
        water = self.linked(MenuItem, Kind.PRODUCT, 1)
        response = self.client.get(reverse("admin:menu_menuitem_change", args=[water.pk]))
        self.assertContains(response, "приходят из Poster")
        self.assertNotContains(response, 'name="name"')
        self.assertNotContains(response, 'name="price"')
        self.assertContains(response, 'name="description"')
        self.assertContains(response, 'name="available_for_delivery"')
        form = admin.site._registry[MenuItem].get_form(self.request, water)
        self.assertFalse({"name", "price", "category", "image_url"} & set(form.base_fields))
        self.assertIn("is_available", form.base_fields)  # the stop list stays Botera's

    def test_botera_own_item_is_edited_as_before(self):
        latte = MenuItem.objects.get(partner=self.partner, name="Лате")
        response = self.client.get(reverse("admin:menu_menuitem_change", args=[latte.pk]))
        self.assertNotContains(response, "приходят из Poster")
        self.assertContains(response, 'name="name"')
        self.assertContains(response, 'name="price"')

    def test_after_disconnecting_everything_is_botera_s_again(self):
        PosterConnection.objects.filter(pk=self.connection.pk).update(
            status=PosterConnection.Status.DISCONNECTED,
        )
        water = self.linked(MenuItem, Kind.PRODUCT, 1)
        self.assertNotIn("name", self.readonly(water))
        response = self.client.get(reverse("admin:menu_menuitem_change", args=[water.pk]))
        self.assertContains(response, 'name="name"')

    def test_a_poster_category(self):
        drinks = self.linked(MenuCategory, Kind.CATEGORY, 3)
        fields = self.readonly(drinks)
        self.assertIn("name", fields)
        self.assertIn("parent", fields)
        self.assertNotIn("is_active", fields)
        self.assertNotIn("description", fields)

    def test_variants_of_a_poster_item(self):
        latte = self.linked(MenuItem, Kind.PRODUCT, 6)
        inline = MenuItemVariantInline(MenuItem, admin.site)
        self.assertTrue({"name", "price", "is_available"} <= set(
            inline.get_readonly_fields(self.request, latte)))
        self.assertFalse(inline.has_add_permission(self.request, latte))
        self.assertFalse(inline.has_delete_permission(self.request, latte))
        own = MenuItem.objects.get(partner=self.partner, name="Лате")
        self.assertTrue(inline.has_add_permission(self.request, own))
        self.assertNotIn("name", inline.get_readonly_fields(self.request, own))

    def test_a_poster_group_and_its_add_ons(self):
        served = self.linked(ModifierGroup, Kind.MODIFIER_GROUP, 2)
        self.assertTrue({"name", "min_choices", "max_choices", "items", "categories"} <= set(
            self.readonly(served)))
        inline = ModifierInline(ModifierGroup, admin.site)
        self.assertFalse(inline.has_add_permission(self.request, served))
        self.assertIn("price", inline.get_readonly_fields(self.request, served))
        response = self.client.get(reverse("admin:menu_modifiergroup_change", args=[served.pk]))
        self.assertContains(response, "приходят из Poster")

    def test_the_stop_list_works_for_poster_items(self):
        water = self.linked(MenuItem, Kind.PRODUCT, 1)
        self.client.post(reverse("admin:menu_menuitem_act", args=[water.pk]),
                         {"object_action": "availability"})
        water.refresh_from_db()
        self.assertFalse(water.is_available)
        sync_menu(connection_id=self.connection.pk)  # Poster did not change its mind
        water.refresh_from_db()
        self.assertFalse(water.is_available)


class WhatTheVenueReadsTests(MenuAdminFixture):
    def test_the_item_list_marks_poster_items(self):
        page = admin.site._registry[MenuItem]
        self.assertIn("poster_source", page.get_list_display(self.request))
        rows = {item.name: item.from_poster for item in page.get_queryset(self.request)}
        self.assertTrue(rows["Вода мінеральна"])
        self.assertFalse(rows["Лате"])
        self.assertContains(self.client.get(reverse("admin:menu_menuitem_changelist")),
                            "Из Poster")

    def test_no_mark_without_poster(self):
        PosterConnection.objects.filter(pk=self.connection.pk).update(
            status=PosterConnection.Status.DISCONNECTED,
        )
        page = admin.site._registry[MenuItem]
        self.assertNotIn("poster_source", page.get_list_display(self.request))
        self.assertNotContains(self.client.get(reverse("admin:menu_menuitem_changelist")),
                               "Из Poster")

    def test_the_connection_page_has_names_not_raw_answers(self):
        PosterConnection.objects.filter(pk=self.connection.pk).update(
            tariff={"value": []},
            available_spots=[{"spot_id": 1, "name": "Головна", "address": ""},
                             {"spot_id": 2, "name": "Поділ", "address": ""}],
        )
        response = self.client.get(
            reverse("admin:poster_posterconnection_change", args=[self.connection.pk]))
        self.assertContains(response, "Головна (1), Поділ (2)")
        self.assertNotContains(response, "Тариф Poster")
        self.assertNotContains(response, "spot_id")


class UpdateMenuButtonTests(MenuAdminFixture):
    def page(self):
        return reverse("admin:poster_posterconnection_change", args=[self.connection.pk])

    def press(self):
        act = reverse("admin:poster_posterconnection_act", args=[self.connection.pk])
        return self.client.post(act, {"object_action": "sync"}, follow=True)

    def test_the_owner_brings_the_menu_now(self):
        self.assertContains(self.client.get(self.page()), "Обновить меню")
        products = [row for row in self.menu[1] if row["product_id"] != "1"]
        self.menu = (self.menu[0], [*products, product(1, "Вода мінеральна", 3, 2800)])
        response = self.press()
        self.assertContains(response, "Меню обновлено из Poster")
        self.assertEqual(self.linked(MenuItem, Kind.PRODUCT, 1).price, Decimal("28.00"))

    def test_notes_are_shown(self):
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            module_options_enabled=False,
        )
        products = [row for row in self.menu[1] if row["product_id"] != "3"]
        self.menu = (self.menu[0], [*products, product(3, "Капучино 250 мл", 1, 0, type="2")])
        response = self.press()
        self.assertContains(response, "Варианты и добавки")
        self.assertContains(response, "Капучино 250 мл")

    def test_a_poster_error_is_said(self):
        self.fetch_mock.side_effect = PosterError("Poster did not answer in time",
                                                  temporary=True)
        response = self.press()
        self.assertContains(response, "Poster did not answer in time")

    def test_no_button_while_poster_is_not_working(self):
        PosterConnection.objects.filter(pk=self.connection.pk).update(
            status=PosterConnection.Status.DISCONNECTED,
        )
        self.assertNotContains(self.client.get(self.page()), "Обновить меню")

    def test_a_manager_has_no_button(self):
        manager = _manager(self.partner, "poster_admin_manager")
        manager.language = "ru"
        manager.save()
        self.client.force_login(manager)
        self.assertNotContains(self.client.get(self.page()), "Обновить меню")
        calls = self.fetch_mock.call_count
        self.press()
        self.assertEqual(self.fetch_mock.call_count, calls)
