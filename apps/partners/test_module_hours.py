"""Opening hours per module: pickup and delivery each may have their own."""

from datetime import datetime, time
from datetime import timezone as dt_timezone

from django.test import TestCase

from apps.billing.remote_orders import available_slots, closed_message
from apps.orders.models import Order
from apps.partners.models import OpeningHours, Partner
from apps.partners.opening_hours import describe_opening_hours, is_open_at

# Wednesday 2026-10-21, 12:00 and 20:00 in Kyiv (UTC+3).
NOON = datetime(2026, 10, 21, 9, 0, tzinfo=dt_timezone.utc)
EVENING = datetime(2026, 10, 21, 17, 0, tzinfo=dt_timezone.utc)
PICKUP = Order.Channel.PICKUP
DELIVERY = Order.Channel.DELIVERY


class ModuleHoursTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(
            name="Hours", slug="hours", status=Partner.Status.ACTIVE, timezone="Europe/Kyiv"
        )

    def _hours(self, scope, opens, closes):
        OpeningHours.objects.bulk_create(
            OpeningHours(
                partner=self.partner, scope=scope, weekday=day, opens_at=opens, closes_at=closes
            )
            for day in range(7)
        )

    def test_without_own_hours_a_module_uses_the_common_ones(self):
        self._hours("", time(10), time(22))
        self.assertTrue(is_open_at(self.partner, NOON, scope=PICKUP))
        self.assertTrue(is_open_at(self.partner, NOON, scope=DELIVERY))
        self.assertEqual(
            describe_opening_hours(self.partner, scope=DELIVERY),
            describe_opening_hours(self.partner),
        )

    def test_own_delivery_hours_do_not_change_pickup(self):
        self._hours("", time(10), time(22))
        self._hours(DELIVERY, time(11), time(15))
        self.assertTrue(is_open_at(self.partner, EVENING, scope=PICKUP))
        self.assertFalse(is_open_at(self.partner, EVENING, scope=DELIVERY))
        self.assertEqual(available_slots(self.partner, EVENING, channel=DELIVERY), ())
        self.assertNotEqual(available_slots(self.partner, EVENING, channel=PICKUP), ())
        self.assertIn("11:00–15:00", closed_message(self.partner, channel=DELIVERY))

    def test_own_pickup_hours_without_common_ones(self):
        self._hours(PICKUP, time(8), time(12))
        self.assertFalse(is_open_at(self.partner, NOON, scope=PICKUP))
        # Delivery has neither its own nor common hours: always open.
        self.assertTrue(is_open_at(self.partner, NOON, scope=DELIVERY))

    def test_scope_choices_are_modules_with_hours(self):
        values = {value for value, _label in OpeningHours.Scope.choices}
        self.assertEqual(values, {"", PICKUP, DELIVERY})
