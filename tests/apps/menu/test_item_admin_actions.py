"""The stop list in one press (admin step 3, owner 2026-11-10, PQ-050).

From the item's page or its row of the list: the item leaves the guests'
menu and comes back the same way."""

from django.test import TestCase
from django.urls import reverse

from apps.menu.models import MenuItem
from apps.menu.repositories import MenuItemRepository
from apps.users.constants import AdminAccessPreset, AdminSection
from apps.users.models import User
from tests.apps.orders.test_order_admin_actions import admin_access
from tests.apps.users.test_admin_navigation import admin_user
from tests.webapp.test_guest_api import make_venue


class AvailabilityTests(TestCase):
    def setUp(self):
        self.partner = make_venue("Kava")
        self.owner = admin_user(self.partner, "item_owner", AdminAccessPreset.OWNER, owner=True)
        self.client.force_login(self.owner)
        self.latte = MenuItem.objects.get(partner=self.partner, name="Лате")

    def on_menu(self) -> bool:
        return MenuItemRepository.active_for_partner(self.partner.id).filter(
            id=self.latte.id
        ).exists()

    def test_from_the_items_page(self):
        response = self.client.get(reverse("admin:menu_menuitem_change", args=[self.latte.pk]))
        self.assertContains(response, "В стоп-лист")
        self.client.post(reverse("admin:menu_menuitem_act", args=[self.latte.pk]),
                         {"object_action": "availability"})
        self.assertFalse(self.on_menu())
        response = self.client.get(reverse("admin:menu_menuitem_change", args=[self.latte.pk]))
        self.assertContains(response, "Вернуть в меню")
        self.client.post(reverse("admin:menu_menuitem_act", args=[self.latte.pk]),
                         {"object_action": "availability"})
        self.assertTrue(self.on_menu())

    def test_from_the_list_and_back_to_it(self):
        listed = self.client.get(reverse("admin:menu_menuitem_changelist"))
        act = reverse("admin:menu_menuitem_act", args=[self.latte.pk]) + "?from=list"
        self.assertContains(listed, f'formaction="{act}"')
        back = "http://testserver" + reverse("admin:menu_menuitem_changelist") + "?q=%D0%9B"
        response = self.client.post(act, {"object_action": "availability"}, HTTP_REFERER=back)
        self.assertRedirects(response, back, fetch_redirect_response=False)
        self.assertFalse(self.on_menu())

    def test_a_foreign_referer_is_not_followed(self):
        act = reverse("admin:menu_menuitem_act", args=[self.latte.pk]) + "?from=list"
        response = self.client.post(act, {"object_action": "availability"},
                                    HTTP_REFERER="https://evil.example/")
        self.assertRedirects(response, reverse("admin:menu_menuitem_changelist"),
                             fetch_redirect_response=False)

    def test_only_with_the_right_to_change_the_menu(self):
        viewer = User.objects.create_user(username="menu_viewer", password="x",
                                          partner=self.partner, role=User.Role.MANAGER)
        admin_access(viewer, AdminAccessPreset.CUSTOM, view_only=[AdminSection.MENU])
        self.client.force_login(viewer)
        listed = self.client.get(reverse("admin:menu_menuitem_changelist"))
        self.assertNotIn("availability_button", listed.context["cl"].list_display)
        response = self.client.post(reverse("admin:menu_menuitem_act", args=[self.latte.pk]),
                                    {"object_action": "availability"})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(self.on_menu())
