"""Public order and bill numbers stay unique per partner when a random one repeats.

Numbers are 8 random hex characters. Without a retry, a repeat aborts the
checkout with IntegrityError: after a year of 36 thousand orders the chance of
at least one such failure is about 15% (load test 2026-10-25).
"""

from unittest.mock import patch

from django.test import TestCase

from apps.billing.models import Bill
from apps.orders.models import Order
from apps.partners.models import Partner
from apps.users.models import GuestProfile, TelegramAccount


class PublicNumberRepeatTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(name="Numbers", slug="numbers")
        account = TelegramAccount.objects.create(telegram_id=7700001)
        self.guest = GuestProfile.objects.create(partner=self.partner, telegram_account=account)

    def test_order_number_is_drawn_again_when_it_repeats(self):
        Order.objects.create(partner=self.partner, guest=self.guest, public_id="aaaa0001")
        with patch("apps.orders.models.secrets.token_hex", side_effect=["aaaa0001", "bbbb0002"]):
            order = Order.objects.create(partner=self.partner, guest=self.guest)
        self.assertEqual(order.public_id, "bbbb0002")

    def test_bill_number_is_drawn_again_when_it_repeats(self):
        Bill.objects.create(partner=self.partner, public_id="cccc0003")
        with patch("apps.billing.models.secrets.token_hex", side_effect=["cccc0003", "dddd0004"]):
            bill = Bill.objects.create(partner=self.partner)
        self.assertEqual(bill.public_id, "dddd0004")

    def test_same_number_is_fine_for_another_partner(self):
        other = Partner.objects.create(name="Other", slug="other")
        account = TelegramAccount.objects.create(telegram_id=7700002)
        other_guest = GuestProfile.objects.create(partner=other, telegram_account=account)
        Order.objects.create(partner=self.partner, guest=self.guest, public_id="eeee0005")
        with patch("apps.orders.models.secrets.token_hex", side_effect=["eeee0005"]):
            order = Order.objects.create(partner=other, guest=other_guest)
        self.assertEqual(order.public_id, "eeee0005")
