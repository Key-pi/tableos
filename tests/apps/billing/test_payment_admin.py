"""A recorded payment opens in Admin for viewing (owner check 2026-11-09).

A payment is never changed after it is recorded, so its page is view-only:
every field is read-only and the form has none of them. The form still
narrowed the «Способ» choices and failed with KeyError: 'method'."""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.billing.models import Payment
from apps.billing.services import create_bill_from_orders, record_payment
from apps.orders.services import create_order_from_session
from apps.users.constants import AdminAccessPreset
from apps.users.models import AdminAccessProfile, User
from tests.apps.billing import test_billing as billing_tests


class PaymentAdminPageTests(TestCase):
    setUp = billing_tests.BillingServiceTests.setUp

    def payment(self) -> Payment:
        order = create_order_from_session(
            partner_id=self.partner.id,
            table_session=self.session,
            items=[{
                "menu_item_id": self.menu_item.id,
                "item_name": self.menu_item.name,
                "unit_price": self.menu_item.price,
                "quantity": 1,
            }],
        )
        bill = create_bill_from_orders(partner_id=self.partner.id, order_ids=[order.id])
        return record_payment(
            bill=bill, amount=Decimal("50.00"), method=Payment.Method.CASH,
            created_by=self.manager.user,
        )

    def test_a_recorded_payment_opens_for_the_venue_owner(self):
        owner = User.objects.create_user(
            username="pay_owner", password="x", partner=self.partner, role=User.Role.OWNER,
            is_staff=True,
        )
        AdminAccessProfile.objects.create(
            user=owner, partner=self.partner, preset=AdminAccessPreset.OWNER,
            is_partner_owner=True, can_access_admin=True,
        )
        self.client.force_login(owner)
        payment = self.payment()
        response = self.client.get(reverse("admin:billing_payment_change", args=[payment.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "50")

    def test_a_recorded_payment_opens_for_the_platform(self):
        root = User.objects.create_superuser(username="pay_root", password="x", email="")
        self.client.force_login(root)
        payment = self.payment()
        response = self.client.get(reverse("admin:billing_payment_change", args=[payment.pk]))
        self.assertEqual(response.status_code, 200)

    def test_the_add_page_still_hides_bonus_payments(self):
        root = User.objects.create_superuser(username="pay_root2", password="x", email="")
        self.client.force_login(root)
        response = self.client.get(reverse("admin:billing_payment_add"))
        self.assertEqual(response.status_code, 200)
        methods = [value for value, _label in response.context["adminform"].form
                   .fields["method"].choices]
        self.assertIn(Payment.Method.CASH, methods)
        self.assertNotIn(Payment.Method.BONUSES, methods)
