"""Records name themselves without the venue inside one venue's Admin
(admin step 4, owner 2026-11-10, PQ-050): the owner sees one venue, so
«Night Owl · Поділ /» on every row is noise. The platform and everything
outside Admin keep full labels."""

from django.test import TestCase
from django.urls import reverse

from apps.menu.models import MenuItem
from apps.orders.models import Order
from apps.tables.models import Table
from apps.users.constants import AdminAccessPreset
from apps.users.models import GuestProfile, TelegramAccount, User
from core.labels import one_venue, within_one_venue
from tests.apps.users.test_admin_navigation import admin_user
from tests.webapp.test_guest_api import make_venue


class LabelTests(TestCase):
    def setUp(self):
        self.partner = make_venue("Kava")
        account = TelegramAccount.objects.create(
            telegram_id=880001, username="olya", first_name="Оля"
        )
        self.guest = GuestProfile.objects.create(partner=self.partner, telegram_account=account)
        self.order = Order.objects.create(
            partner=self.partner, guest=self.guest, channel=Order.Channel.PICKUP,
            subtotal_amount="80.00", total_amount="80.00",
        )
        self.table = Table.objects.create(partner=self.partner, number=7, qr_token="label007")
        self.category = MenuItem.objects.filter(partner=self.partner).first().category

    def test_full_outside_admin(self):
        self.assertFalse(within_one_venue())
        self.assertEqual(str(self.order), f"Kava / order {self.order.public_id}")
        self.assertEqual(str(self.guest), "Kava / olya")
        self.assertEqual(str(self.table), "Kava / table 7")
        self.assertEqual(str(self.category), f"Kava / {self.category.full_name}")

    def test_short_inside_a_venue(self):
        with one_venue():
            self.assertEqual(str(self.order), f"Заказ #{self.order.public_id}")
            self.assertEqual(str(self.guest), f"Оля @olya · {self.guest.customer_code}")
            self.assertEqual(str(self.table), "Стол 7")
            self.assertEqual(str(self.category), self.category.full_name)
        self.assertFalse(within_one_venue())

    def test_a_venues_admin_is_short_and_the_platforms_is_full(self):
        owner = admin_user(self.partner, "label_owner", AdminAccessPreset.OWNER, owner=True)
        self.client.force_login(owner)
        page = self.client.get(reverse("admin:orders_order_change", args=[self.order.pk]))
        self.assertContains(page, f"Заказ #{self.order.public_id}")
        self.assertNotContains(page, "Kava / ")
        listed = self.client.get(reverse("admin:menu_menuitem_changelist"))
        self.assertNotContains(listed, "Kava / ")
        self.assertFalse(within_one_venue())  # nothing leaks out of the request

        root = User.objects.create_superuser(username="label_root", password="x", email="")
        self.client.force_login(root)
        page = self.client.get(reverse("admin:orders_order_change", args=[self.order.pk]))
        self.assertContains(page, f"Kava / order {self.order.public_id}")

    def test_the_whole_page_is_drawn_with_short_labels(self):
        owner = admin_user(self.partner, "label_owner2", AdminAccessPreset.OWNER, owner=True)
        self.client.force_login(owner)
        seen = []
        original = Order.__str__

        def spy(order):
            seen.append(within_one_venue())
            return original(order)

        Order.__str__ = spy
        try:
            self.client.get(reverse("admin:orders_order_change", args=[self.order.pk]))
            self.assertTrue(seen and all(seen))
        finally:
            Order.__str__ = original
