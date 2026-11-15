"""The anonymous guest of a visit or a counter sale (PQ-055, owner 2026-11-15).

A company the staff seated without a code, or a sale without a code, has its
own anonymous guest: orders and sessions need someone. That guest is not a
person, so it is not in Admin «Гости», not in the guest counts, and its code
is never shown (nobody can name it at the till anyway).
"""

from django.test import TestCase
from django.urls import reverse

from apps.analytics.services import build_daily_tails_page, refresh_partner_daily_metric
from apps.menu.models import MenuCategory, MenuItem
from apps.orders.models import Order
from apps.orders.services import create_order_from_session
from apps.partners.models import Partner, PartnerBotSettings
from apps.tables.admin import TableAdmin
from apps.tables.models import Table
from apps.tables.services import activate_table_session, open_staff_table_session
from apps.users.constants import AdminAccessPreset
from apps.users.models import GuestProfile, User
from tests.apps.orders.test_order_admin_actions import admin_access


class AnonymousGuestFixture(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(
            name="Hidden", slug="hidden-guests", status=Partner.Status.ACTIVE
        )
        PartnerBotSettings.objects.create(
            partner=self.partner, module_menu_enabled=True, module_tables_enabled=True,
            module_cart_enabled=True, module_orders_enabled=True, module_reports_enabled=True,
        )
        category = MenuCategory.objects.create(partner=self.partner, name="Bar")
        self.item = MenuItem.objects.create(
            partner=self.partner, category=category, name="Tea", price="100.00"
        )
        self.table = Table.objects.create(partner=self.partner, number=4, qr_token="hidden-4")
        self.other_table = Table.objects.create(partner=self.partner, number=5, qr_token="hidden-5")
        # A company seated by the staff, without a code.
        self.seated = open_staff_table_session(partner_id=self.partner.id, table_id=self.table.id)
        self.anonymous = self.seated.guest
        self.order(self.seated)
        # A guest with the bot at another table.
        self.known_session = activate_table_session(
            partner_id=self.partner.id, telegram_id=7790001,
            payload=self.other_table.deep_link_payload,
        )
        self.known = self.known_session.guest
        self.order(self.known_session)

    def order(self, session):
        return create_order_from_session(
            partner_id=self.partner.id, table_session=session,
            items=[{"menu_item_id": self.item.id, "item_name": self.item.name,
                    "unit_price": self.item.price, "quantity": 1}],
        )


class AdminGuestListTests(AnonymousGuestFixture):
    def setUp(self):
        super().setUp()
        self.owner = User.objects.create_user(
            username="hidden_owner", password="x", partner=self.partner, role=User.Role.OWNER
        )
        admin_access(self.owner, AdminAccessPreset.OWNER, owner=True)
        self.client.force_login(self.owner)

    def test_the_guest_list_has_only_people(self):
        self.assertTrue(self.anonymous.is_anonymous)
        response = self.client.get(reverse("admin:users_guestprofile_changelist"))
        self.assertEqual(response.status_code, 200)
        listed = list(response.context["cl"].queryset)
        self.assertEqual(listed, [self.known])
        self.assertNotContains(response, self.anonymous.customer_code)

    def test_a_link_to_the_anonymous_guest_still_opens_without_the_code(self):
        response = self.client.get(
            reverse("admin:users_guestprofile_change", args=[self.anonymous.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.anonymous.customer_code)
        self.assertContains(response, "Гость без бота")

    def test_the_table_page_shows_no_code_for_the_company(self):
        summary = str(TableAdmin(Table, None).active_sessions_summary(self.table))
        self.assertIn("Гость без бота", summary)
        self.assertNotIn(self.anonymous.customer_code, summary)
        orders = str(TableAdmin(Table, None).orders_summary(self.table))
        self.assertNotIn(self.anonymous.customer_code, orders)


class GuestCountTests(AnonymousGuestFixture):
    def test_the_day_counts_people_not_companies(self):
        metric = refresh_partner_daily_metric(partner_id=self.partner.id)
        self.assertEqual(Order.objects.filter(partner=self.partner).count(), 2)
        self.assertEqual(metric.active_guests, 1)

    def test_the_day_report_names_no_code(self):
        page = build_daily_tails_page(partner_id=self.partner.id)
        subtitles = " ".join(str(item.get("subtitle", "")) for item in page.items)
        self.assertIn("гость: Гость без бота", subtitles)
        self.assertNotIn(self.anonymous.customer_code, subtitles)

    def test_the_anonymous_guest_is_not_found_by_its_code(self):
        from apps.users.services import find_guest_by_customer_code

        with self.assertRaises(GuestProfile.DoesNotExist):
            find_guest_by_customer_code(self.partner.id, self.anonymous.customer_code)
