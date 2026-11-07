"""The catalog beyond names and prices (owner, 2026-11-07): subcategories,
a category's schedule, the showcase fields."""

from datetime import datetime, time
from decimal import Decimal
from unittest import mock
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.menu.catalog import (
    badges_of,
    category_open,
    first_not_on_sale,
    portion_label,
    schedule_label,
)
from apps.menu.models import MenuBadge, MenuCategory, MenuItem
from apps.orders.models import Order
from apps.tables.models import Table
from apps.tables.services import activate_table_session
from core.i18n import texts
from tests.webapp.test_guest_api import TG, MiniAppFixture, make_venue

KYIV = ZoneInfo("Europe/Kyiv")
MONDAY_7 = datetime(2026, 11, 9, 7, 0, tzinfo=KYIV)
MONDAY_9 = datetime(2026, 11, 9, 9, 0, tzinfo=KYIV)
SATURDAY_9 = datetime(2026, 11, 14, 9, 0, tzinfo=KYIV)


def at(moment):
    return mock.patch("apps.menu.catalog.timezone.now", return_value=moment)


class CategoryRulesTests(TestCase):
    def setUp(self):
        self.partner = make_venue("Shop")
        self.clothes = MenuCategory.objects.create(partner=self.partner, name="Одежда")

    def category(self, **fields):
        return MenuCategory(partner=self.partner, name=fields.pop("name", "Новая"), **fields)

    def test_two_levels_only(self):
        shirts = MenuCategory.objects.create(
            partner=self.partner, name="Футболки", parent=self.clothes
        )
        shirts.full_clean()
        self.assertEqual(shirts.full_name, "Одежда › Футболки")
        with self.assertRaises(ValidationError):
            self.category(name="Белые", parent=shirts).full_clean()
        self.clothes.parent = MenuCategory.objects.create(partner=self.partner, name="Всё")
        with self.assertRaises(ValidationError):  # it has its own subcategories
            self.clothes.full_clean()

    def test_a_parent_of_another_venue_is_refused(self):
        other = MenuCategory.objects.create(partner=make_venue("Other"), name="Чужая")
        with self.assertRaises(ValidationError):
            self.category(parent=other).full_clean()

    def test_the_schedule_needs_both_ends_and_real_days(self):
        with self.assertRaises(ValidationError):
            self.category(available_from=time(8)).full_clean()
        with self.assertRaises(ValidationError):
            self.category(available_from=time(8), available_until=time(8)).full_clean()
        with self.assertRaises(ValidationError):
            self.category(available_weekdays=[7]).full_clean()
        self.category(
            available_from=time(8), available_until=time(12), available_weekdays=[0, 4]
        ).full_clean()


class ScheduleTests(TestCase):
    def setUp(self):
        self.partner = make_venue("Cafe")

    def category(self, start=None, end=None, days=(), parent=None, name="Завтраки"):
        return MenuCategory.objects.create(
            partner=self.partner, name=name, available_from=start, available_until=end,
            available_weekdays=list(days), parent=parent,
        )

    def test_a_window_in_the_day(self):
        breakfast = self.category(time(8), time(12))
        self.assertFalse(category_open(breakfast, MONDAY_7))
        self.assertTrue(category_open(breakfast, MONDAY_9))
        self.assertEqual(schedule_label(breakfast), "с 08:00 до 12:00")

    def test_a_window_past_midnight_belongs_to_its_evening(self):
        night = self.category(time(22), time(2), days=[4])  # Friday night
        friday_23 = datetime(2026, 11, 13, 23, 0, tzinfo=KYIV)
        saturday_1 = datetime(2026, 11, 14, 1, 0, tzinfo=KYIV)
        saturday_23 = datetime(2026, 11, 14, 23, 0, tzinfo=KYIV)
        self.assertTrue(category_open(night, friday_23))
        self.assertTrue(category_open(night, saturday_1))
        self.assertFalse(category_open(night, saturday_23))

    def test_weekdays_only(self):
        lunch = self.category(days=[0, 1, 2, 3, 4], name="Ланч")
        self.assertTrue(category_open(lunch, MONDAY_9))
        self.assertFalse(category_open(lunch, SATURDAY_9))
        self.assertEqual(schedule_label(lunch), "пн, вт, ср, чт, пт")

    def test_a_subcategory_follows_its_parent(self):
        breakfast = self.category(time(8), time(12))
        eggs = self.category(parent=breakfast, name="Яйца")
        self.assertFalse(category_open(eggs, MONDAY_7))
        self.assertTrue(category_open(eggs, MONDAY_9))


class ItemRulesTests(TestCase):
    def setUp(self):
        self.partner = make_venue("Shop")
        self.category = MenuCategory.objects.get(partner=self.partner, name="Кава")

    def item(self, **fields):
        return MenuItem(
            partner=self.partner, category=self.category, name=fields.pop("name", "Товар"),
            price=fields.pop("price", Decimal("100.00")), **fields,
        )

    def test_the_old_price_is_above_the_price(self):
        with self.assertRaises(ValidationError):
            self.item(old_price=Decimal("100.00")).full_clean()
        self.item(old_price=Decimal("120.00")).full_clean()

    def test_a_portion_needs_its_unit(self):
        with self.assertRaises(ValidationError):
            self.item(portion=Decimal("300")).full_clean()
        with self.assertRaises(ValidationError):
            self.item(portion_unit="ml").full_clean()
        item = self.item(portion=Decimal("0.500"), portion_unit="l")
        item.full_clean()
        with texts("uk"):
            self.assertEqual(portion_label(item), "0,5 л")
        with texts("en"):
            self.assertEqual(portion_label(item), "0.5 l")

    def test_labels_ready_and_own(self):
        with self.assertRaises(ValidationError):
            self.item(badges=["made-up"]).full_clean()
        item = self.item(badges=["hit", "vegan"])
        item.save()
        item.custom_badges.add(MenuBadge.objects.create(partner=self.partner, name="Сезон"))
        with texts("en"):
            self.assertEqual(
                [badge["name"] for badge in badges_of(item)], ["Hit", "Vegan", "Сезон"]
            )

    def test_an_sku_is_unique_in_the_venue(self):
        self.item(sku="A-1").save()
        with self.assertRaises(ValidationError) as caught:
            self.item(sku="A-1", name="Другой").full_clean()
        self.assertIn("артикул", str(caught.exception))
        other = make_venue("Other")
        MenuItem(
            partner=other, category=MenuCategory.objects.get(partner=other, name="Кава"),
            name="Товар", price=Decimal("1.00"), sku="A-1",
        ).full_clean()
        self.item(name="Без артикула 1").save()
        self.item(name="Без артикула 2").save()  # an empty SKU repeats freely


class ScheduleIsKeptTests(MiniAppFixture):
    """Shown all day, sold only in its time (owner, 2026-11-07)."""

    def setUp(self):
        super().setUp()
        self.breakfast = MenuCategory.objects.create(
            partner=self.partner, name="Сніданки", available_from=time(8),
            available_until=time(12),
        )
        self.omelette = MenuItem.objects.create(
            partner=self.partner, category=self.breakfast, name="Омлет", price="120.00",
        )

    def add(self, item):
        return self.call("cart", {"action": "add", "item": item.public_id})

    def test_not_into_the_cart_outside_its_time(self):
        self.choose("pickup")
        with at(MONDAY_7):
            status, body = self.add(self.omelette)
        self.assertEqual(status, 400)
        self.assertIn("08:00", body["error"])
        with at(MONDAY_9):
            self.assertEqual(self.add(self.omelette)[0], 200)

    def test_a_cart_from_the_morning_is_not_ordered_at_noon(self):
        self.choose("pickup")
        with at(MONDAY_9):
            self.add(self.omelette)
        self.call("details", {"phone": "+380501112233"})
        with at(datetime(2026, 11, 9, 12, 30, tzinfo=KYIV)):
            status, body = self.call("checkout")
        self.assertEqual(status, 400)
        self.assertIn("Омлет", body["error"])

    def test_at_the_table_too(self):
        table = Table.objects.create(partner=self.partner, number=5)
        activate_table_session(
            partner_id=self.partner.id, telegram_id=TG, payload=table.deep_link_payload
        )
        with at(MONDAY_9):
            self.add(self.omelette)
        with at(datetime(2026, 11, 9, 12, 30, tzinfo=KYIV)):
            status, _body = self.call("place", {})
        self.assertEqual(status, 400)
        self.assertFalse(Order.objects.exists())

    def test_the_check_uses_the_venues_own_time(self):
        self.partner.timezone = "Asia/Tokyo"  # 7:00 in Kyiv is 14:00 in Tokyo
        self.partner.save()
        self.omelette.refresh_from_db()
        with at(MONDAY_7):
            self.assertIsNotNone(first_not_on_sale([self.omelette]))
        tokyo_9 = datetime(2026, 11, 9, 9, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        with at(tokyo_9):
            self.assertIsNone(first_not_on_sale([self.omelette]))
