"""The menu from Poster (PQ-058, step 3): what comes over, in which units,
what Botera keeps, the stop list, errors and locks. Poster itself is
replaced by a mock of ``menu_sync.fetch_menu``; the payloads copy the trial
account of step 0 (docs/architecture/POSTER.md, «Итоги шага 0»)."""

import threading
from decimal import Decimal
from unittest import mock, skipUnless

from django.db import connection as db_connection
from django.db import transaction
from django.test import TestCase, TransactionTestCase

from apps.integrations.poster import menu_sync
from apps.integrations.poster.client import PosterError
from apps.integrations.poster.menu_sync import PosterSyncError, sync_menu
from apps.integrations.poster.models import PosterConnection, PosterRef
from apps.menu.models import MenuCategory, MenuItem, MenuItemVariant, Modifier, ModifierGroup
from apps.partners.languages import main_language
from apps.partners.models import Partner, PartnerBotSettings
from apps.users.constants import AdminAccessPreset
from core.i18n import gettext_in
from tests.apps.users.test_admin_navigation import admin_user
from tests.webapp.test_guest_api import make_venue

TOKEN = "100200:token-placeholder"
SPOT = 1
Kind = PosterRef.Kind


def category(cid, name, parent=0, *, hidden="0", visible=1, spot=SPOT):
    return {
        "category_id": str(cid), "category_name": name, "parent_category": str(parent),
        "category_hidden": hidden, "visible": [{"spot_id": spot, "visible": visible}],
    }


def product(pid, name, category_id, price=None, *, type="3", hidden="0", visible="1",
            photo="", variants=None, groups=None, spots=None):
    """A product as ``menu.getProducts`` gives it: product and variant
    prices in kopecks; ``spots`` — ``{spot_id: (price, visible)}``."""

    row = {
        "product_id": str(pid), "product_name": name, "menu_category_id": str(category_id),
        "type": type, "hidden": hidden, "photo": photo,
    }
    if variants:
        row["price"] = None
        row["spots"] = None
        row["modifications"] = [
            {"modificator_id": str(mid), "modificator_name": mname, "order": "0",
             "spots": [{"spot_id": str(SPOT), "price": str(mprice), "visible": mvisible}]}
            for mid, mname, mprice, mvisible in variants
        ]
    else:
        spots = spots or {SPOT: (price, visible)}
        row["price"] = {str(spot): str(value) for spot, (value, _seen) in spots.items()}
        row["spots"] = [
            {"spot_id": str(spot), "price": str(value), "visible": seen}
            for spot, (value, seen) in spots.items()
        ]
    if groups:
        row["group_modifications"] = groups
    return row


def group(gid, name, modifications, num_min=0, num_max=0, *, type=2, is_deleted=0):
    """A tech card's set of modifications; their prices are in hryvnias."""

    return {
        "dish_modification_group_id": gid, "name": name, "num_min": num_min,
        "num_max": num_max, "type": type, "is_deleted": is_deleted,
        "modifications": [
            {"dish_modification_id": mid, "name": mname, "price": mprice, "sort_order": index}
            for index, (mid, mname, mprice) in enumerate(modifications)
        ],
    }


def sauce(*modifications):
    return group(1, "Соус до сирників", modifications or ((1, "Сметана", 15), (2, "Варення", 20)),
                 0, 2)


def serving():
    return group(2, "Подача", ((3, "На тарілці", 0), (4, "У ланч-боксі", 10)), 1, 1, type=1)


def trial_menu():
    """The trial account: three categories, a product, a product with
    variants, two tech cards (one with sets of modifications) and a
    semi-finished product that is not sold."""

    categories = [category(1, "Кава"), category(2, "Випічка"), category(3, "Холодні напої")]
    products = [
        product(1, "Вода мінеральна", 3, 2500, photo="/upload/pos_cdb/menu/product_1.jpg"),
        product(6, "Кава лате", 1, variants=[
            (1, "S 250 мл", 5500, "1"), (2, "M 350 мл", 6500, "1"), (3, "L 450 мл", 7500, "1"),
        ]),
        product(3, "Капучино 250 мл", 1, 3000, type="2"),
        product(7, "Сирники", 2, 12000, type="2", groups=[sauce(), serving()]),
        product(9, "Тісто", 2, 500, type="1"),
    ]
    return categories, products


def connect(partner, *, spot_id=SPOT, status=PosterConnection.Status.ACTIVE):
    connection = PosterConnection(
        partner=partner, account="kava", account_number="100200", spot_id=spot_id,
        status=status,
    )
    connection.set_access_token(TOKEN)
    connection.save()
    return connection


def ref(partner, kind, poster_id):
    return PosterRef.objects.get(partner=partner, kind=kind, poster_id=str(poster_id))


class SyncFixture(TestCase):
    def setUp(self):
        # make_venue brings Botera's own «Кава» category with «Лате» and «Кальян».
        self.partner = make_venue("Kava")
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            module_options_enabled=True,
        )
        self.connection = connect(self.partner)
        self.menu = trial_menu()
        self.fetch = mock.patch.object(menu_sync, "fetch_menu", side_effect=lambda token: (
            [dict(row) for row in self.menu[0]], [dict(row) for row in self.menu[1]]
        ))
        self.fetch_mock = self.fetch.start()
        self.addCleanup(self.fetch.stop)

    def sync(self, **kwargs):
        return sync_menu(connection_id=self.connection.pk, **kwargs)

    def item(self, poster_id):
        return MenuItem.objects.get(pk=ref(self.partner, Kind.PRODUCT, poster_id).object_id)

    def replace_product(self, poster_id, new):
        products = [row for row in self.menu[1] if row["product_id"] != str(poster_id)]
        self.menu = (self.menu[0], [*products, new] if new else products)


class FirstSyncTests(SyncFixture):
    def test_categories_items_and_units(self):
        report = self.sync()

        water = self.item(1)
        self.assertEqual(water.name, "Вода мінеральна")
        self.assertEqual(water.price, Decimal("25.00"))  # 2500 kopecks
        self.assertEqual(water.category.name, "Холодні напої")
        self.assertEqual(water.image_url,
                         "https://joinposter.com/upload/pos_cdb/menu/product_1.jpg")
        self.assertTrue(water.is_available)
        self.assertEqual(self.item(3).price, Decimal("30.00"))
        self.assertEqual(self.item(3).image_url, "")
        self.assertFalse(PosterRef.objects.filter(partner=self.partner, kind=Kind.PRODUCT,
                                                  poster_id="9").exists())  # semi-finished
        self.assertFalse(MenuItem.objects.filter(partner=self.partner, name="Тісто").exists())
        self.assertEqual(report.created[Kind.PRODUCT], 4)

    def test_variants_in_kopecks(self):
        self.sync()
        latte = self.item(6)
        variants = list(latte.variants.order_by("sort_order"))
        self.assertEqual([(v.name, v.price) for v in variants], [
            ("S 250 мл", Decimal("55.00")), ("M 350 мл", Decimal("65.00")),
            ("L 450 мл", Decimal("75.00")),
        ])
        self.assertEqual(latte.price, Decimal("55.00"))  # the item shows «from»
        self.assertEqual(
            MenuItemVariant.objects.get(pk=ref(self.partner, Kind.VARIANT, "6:2").object_id),
            variants[1],
        )

    def test_add_ons_in_hryvnias_and_the_required_group(self):
        self.sync()
        cheesecakes = self.item(7)
        sauce_group = ModifierGroup.objects.get(
            pk=ref(self.partner, Kind.MODIFIER_GROUP, 1).object_id)
        self.assertEqual((sauce_group.name, sauce_group.min_choices, sauce_group.max_choices),
                         ("Соус до сирників", 0, 2))
        self.assertEqual(
            [(m.name, m.price) for m in sauce_group.modifiers.order_by("sort_order")],
            [("Сметана", Decimal("15.00")), ("Варення", Decimal("20.00"))],
        )
        served = ModifierGroup.objects.get(pk=ref(self.partner, Kind.MODIFIER_GROUP, 2).object_id)
        self.assertEqual((served.min_choices, served.max_choices), (1, 1))  # «одно из»
        self.assertEqual(set(cheesecakes.modifier_groups.all()), {sauce_group, served})
        box = Modifier.objects.get(pk=ref(self.partner, Kind.MODIFIER, "2:4").object_id)
        self.assertEqual((box.name, box.price, box.group), ("У ланч-боксі", Decimal("10.00"),
                                                            served))

    def test_one_of_is_required_even_when_poster_says_zero(self):
        cheesecakes = product(7, "Сирники", 2, 12000, type="2", groups=[
            group(2, "Подача", ((3, "На тарілці", 0),), 0, 0, type=1),
        ])
        self.replace_product(7, cheesecakes)
        self.sync()
        served = ModifierGroup.objects.get(pk=ref(self.partner, Kind.MODIFIER_GROUP, 2).object_id)
        self.assertEqual((served.min_choices, served.max_choices), (1, 1))

    def test_a_botera_category_of_the_same_name_is_taken_over(self):
        own = MenuCategory.objects.get(partner=self.partner, name="Кава")
        self.sync()
        self.assertEqual(ref(self.partner, Kind.CATEGORY, 1).object_id, own.pk)
        self.assertEqual(self.item(6).category, own)
        self.assertEqual(MenuCategory.objects.filter(partner=self.partner, name="Кава").count(), 1)

    def test_botera_own_items_stay_as_they_are(self):
        self.sync()
        latte = MenuItem.objects.get(partner=self.partner, name="Лате")
        self.assertTrue(latte.is_available)
        self.assertEqual(latte.price, Decimal("80.00"))
        self.assertFalse(PosterRef.objects.filter(object_id=latte.pk).exists())

    def test_hidden_in_poster_comes_hidden(self):
        self.replace_product(1, product(1, "Вода мінеральна", 3, 2500, hidden="1"))
        self.sync()
        self.assertFalse(self.item(1).is_available)

    def test_the_price_and_visibility_of_this_spot(self):
        self.connection.spot_id = 2
        self.connection.save()
        self.replace_product(1, product(1, "Вода мінеральна", 3, spots={
            1: (2500, "1"), 2: (2700, "0"),
        }))
        self.sync()
        water = self.item(1)
        self.assertEqual(water.price, Decimal("27.00"))
        self.assertFalse(water.is_available)

    def test_without_a_price_it_is_not_brought(self):
        self.replace_product(3, product(3, "Капучино 250 мл", 1, 0, type="2"))
        report = self.sync()
        self.assertFalse(PosterRef.objects.filter(partner=self.partner, kind=Kind.PRODUCT,
                                                  poster_id="3").exists())
        self.assertTrue(any("Капучино 250 мл" in line for line in report.skipped))

    def test_two_levels_of_categories(self):
        self.menu[0].extend([category(4, "Десерти", 2), category(5, "Торти", 4)])
        self.menu[1].append(product(11, "Наполеон", 5, 9000, type="2"))
        self.sync()
        desserts = MenuCategory.objects.get(pk=ref(self.partner, Kind.CATEGORY, 4).object_id)
        self.assertEqual(desserts.parent.name, "Випічка")
        self.assertEqual(self.item(11).category, desserts)
        self.assertFalse(MenuCategory.objects.filter(partner=self.partner, name="Торти").exists())

    def test_a_hidden_deep_category_hides_its_items(self):
        self.menu[0].extend([category(4, "Десерти", 2), category(5, "Торти", 4, hidden="1")])
        self.menu[1].append(product(11, "Наполеон", 5, 9000, type="2"))
        self.sync()
        self.assertFalse(self.item(11).is_available)

    def test_categories_of_the_same_name_get_their_own(self):
        self.menu[0].extend([category(10, "Інше", 1), category(12, "Інше", 2)])
        self.sync()
        first = MenuCategory.objects.get(pk=ref(self.partner, Kind.CATEGORY, 10).object_id)
        second = MenuCategory.objects.get(pk=ref(self.partner, Kind.CATEGORY, 12).object_id)
        self.assertNotEqual(first.name, second.name)
        self.assertEqual({first.name, second.name}, {"Інше", "Інше (Випічка)"})

    def test_a_product_without_a_category(self):
        self.menu[1].append(product(12, "Пакет", 0, 300))
        self.sync()
        settings = PartnerBotSettings.objects.get(partner=self.partner)
        self.assertEqual(self.item(12).category.name,
                         gettext_in(main_language(settings), "Без категории"))
        self.assertEqual(ref(self.partner, Kind.CATEGORY, 0).object_id, self.item(12).category_id)

    def test_a_group_of_several_dishes_is_one_group(self):
        self.menu[1].append(product(8, "Деруни", 2, 9500, type="2", groups=[sauce()]))
        self.sync()
        sauce_group = ModifierGroup.objects.get(
            pk=ref(self.partner, Kind.MODIFIER_GROUP, 1).object_id)
        self.assertEqual(set(sauce_group.items.all()), {self.item(7), self.item(8)})

    def test_options_module_off_is_named(self):
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            module_options_enabled=False,
        )
        report = self.sync()
        self.assertTrue(any("Варианты и добавки" in line for line in report.warnings))

    def test_success_is_remembered(self):
        self.connection.last_error = "old"
        self.connection.save()
        self.sync()
        self.connection.refresh_from_db()
        self.assertIsNotNone(self.connection.last_synced_at)
        self.assertEqual(self.connection.last_error, "")
        self.assertIsNone(self.connection.last_error_at)
        self.fetch_mock.assert_called_once_with(TOKEN)


class NextSyncTests(SyncFixture):
    def setUp(self):
        super().setUp()
        self.sync()

    def test_the_same_menu_changes_nothing(self):
        stamps = dict(MenuItem.objects.filter(partner=self.partner).values_list("id",
                                                                                "updated_at"))
        report = self.sync()
        self.assertEqual(sum(report.created.values()), 0)
        self.assertEqual(sum(report.updated.values()), 0)
        self.assertEqual(sum(report.removed.values()), 0)
        self.assertEqual(
            dict(MenuItem.objects.filter(partner=self.partner).values_list("id", "updated_at")),
            stamps,
        )

    def test_changes_in_poster_come_over(self):
        self.replace_product(1, product(1, "Вода мінеральна", 3, 2800))
        self.replace_product(3, product(3, "Капучино 300 мл", 3, 3000, type="2"))
        self.replace_product(6, product(6, "Кава лате", 1, variants=[
            (1, "S 250 мл", 5500, "1"), (2, "M 350 мл", 6900, "1"),
        ]))
        self.replace_product(7, product(7, "Сирники", 2, 12000, type="2", groups=[
            sauce((1, "Сметана", 18)), serving(),
        ]))
        report = self.sync()

        self.assertEqual(self.item(1).price, Decimal("28.00"))
        capuccino = self.item(3)
        self.assertEqual((capuccino.name, capuccino.category.name),
                         ("Капучино 300 мл", "Холодні напої"))
        medium = MenuItemVariant.objects.get(pk=ref(self.partner, Kind.VARIANT, "6:2").object_id)
        self.assertEqual(medium.price, Decimal("69.00"))
        large = MenuItemVariant.objects.get(pk=ref(self.partner, Kind.VARIANT, "6:3").object_id)
        self.assertFalse(large.is_available)
        self.assertTrue(ref(self.partner, Kind.VARIANT, "6:3").removed_in_poster)
        cream = Modifier.objects.get(pk=ref(self.partner, Kind.MODIFIER, "1:1").object_id)
        self.assertEqual(cream.price, Decimal("18.00"))
        jam = Modifier.objects.get(pk=ref(self.partner, Kind.MODIFIER, "1:2").object_id)
        self.assertFalse(jam.is_available)
        self.assertGreaterEqual(report.updated[Kind.PRODUCT], 2)

    def test_removed_in_poster_is_hidden_and_comes_back(self):
        self.replace_product(1, None)
        report = self.sync()
        self.assertFalse(self.item(1).is_available)
        self.assertTrue(ref(self.partner, Kind.PRODUCT, 1).removed_in_poster)
        self.assertEqual(report.removed[Kind.PRODUCT], 1)

        self.menu = trial_menu()
        self.sync()
        self.assertTrue(self.item(1).is_available)
        self.assertFalse(ref(self.partner, Kind.PRODUCT, 1).removed_in_poster)

    def test_a_removed_group_and_category(self):
        self.replace_product(7, product(7, "Сирники", 2, 12000, type="2", groups=[
            sauce(), group(2, "Подача", (), 1, 1, type=1, is_deleted=1),
        ]))
        self.menu = ([row for row in self.menu[0] if row["category_id"] != "3"],
                     [row for row in self.menu[1] if row["product_id"] != "1"])
        self.sync()
        served = ModifierGroup.objects.get(pk=ref(self.partner, Kind.MODIFIER_GROUP, 2).object_id)
        self.assertFalse(served.is_active)
        drinks = MenuCategory.objects.get(pk=ref(self.partner, Kind.CATEGORY, 3).object_id)
        self.assertFalse(drinks.is_active)
        self.assertTrue(ref(self.partner, Kind.CATEGORY, 3).removed_in_poster)

    def test_botera_fields_are_kept(self):
        water = self.item(1)
        water.description = "Без газу"
        water.translations = {"en": {"name": "Mineral water"}}
        water.available_for_delivery = False
        water.sort_order = 5
        water.save()
        drinks = water.category
        drinks.description = "Охолоджені"
        drinks.sort_order = 7
        drinks.save()
        self.replace_product(1, product(1, "Вода мінеральна 0,5", 3, 2800))
        self.sync()
        water.refresh_from_db()
        self.assertEqual(water.name, "Вода мінеральна 0,5")
        self.assertEqual((water.description, water.translations, water.available_for_delivery,
                          water.sort_order),
                         ("Без газу", {"en": {"name": "Mineral water"}}, False, 5))
        drinks.refresh_from_db()
        self.assertEqual((drinks.description, drinks.sort_order), ("Охолоджені", 7))

    def test_the_botera_stop_list_survives_while_poster_does_not_change(self):
        water = self.item(1)
        water.is_available = False
        water.save()
        self.sync()
        self.assertFalse(self.item(1).is_available)

    def test_poster_hiding_and_showing_wins(self):
        self.replace_product(1, product(1, "Вода мінеральна", 3, 2500, visible="0"))
        self.sync()
        self.assertFalse(self.item(1).is_available)
        self.menu = trial_menu()
        self.sync()
        self.assertTrue(self.item(1).is_available)

    def test_a_category_hidden_in_botera_stays_hidden(self):
        drinks = self.item(1).category
        drinks.is_active = False
        drinks.save()
        self.sync()
        drinks.refresh_from_db()
        self.assertFalse(drinks.is_active)

    def test_an_item_deleted_in_botera_comes_back(self):
        self.item(1).delete()
        self.sync()
        self.assertEqual(self.item(1).name, "Вода мінеральна")


class SyncRefusalTests(SyncFixture):
    def test_only_a_working_connection(self):
        for status in (PosterConnection.Status.DISCONNECTED, PosterConnection.Status.PENDING_SPOT):
            with self.subTest(status=status):
                self.connection.status = status
                self.connection.save()
                with self.assertRaises(PosterSyncError):
                    self.sync()
        self.fetch_mock.assert_not_called()

    def test_only_the_owner_presses_it(self):
        manager = admin_user(self.partner, "poster_menu_manager", AdminAccessPreset.MANAGER)
        with self.assertRaises(PosterSyncError):
            self.sync(user=manager)
        self.fetch_mock.assert_not_called()
        owner = admin_user(self.partner, "poster_menu_owner", AdminAccessPreset.OWNER,
                           owner=True)
        self.sync(user=owner)
        self.assertTrue(PosterRef.objects.filter(partner=self.partner).exists())

    def test_a_poster_error_is_remembered_and_nothing_written(self):
        self.fetch_mock.side_effect = PosterError("Poster did not answer in time", temporary=True)
        with self.assertRaisesMessage(PosterSyncError, "Poster did not answer in time"):
            self.sync()
        self.connection.refresh_from_db()
        self.assertIn("Poster did not answer in time", self.connection.last_error)
        self.assertIsNotNone(self.connection.last_error_at)
        self.assertFalse(PosterRef.objects.filter(partner=self.partner).exists())

    def test_reconnected_while_reading_is_not_written(self):
        def reconnect(token):
            PosterConnection.objects.filter(pk=self.connection.pk).update(account_number="999")
            return trial_menu()

        self.fetch_mock.side_effect = reconnect
        with self.assertRaises(PosterSyncError):
            self.sync()
        self.assertFalse(PosterRef.objects.filter(partner=self.partner).exists())

    def test_disconnected_while_reading_is_not_written(self):
        def disconnect(token):
            PosterConnection.objects.filter(pk=self.connection.pk).update(
                status=PosterConnection.Status.DISCONNECTED,
            )
            return trial_menu()

        self.fetch_mock.side_effect = disconnect
        with self.assertRaises(PosterSyncError):
            self.sync()
        self.assertFalse(PosterRef.objects.filter(partner=self.partner).exists())


class NetworkOutsideTransactionTests(TransactionTestCase):
    def test_poster_is_read_before_the_transaction(self):
        partner = Partner.objects.create(name="Kava", slug="poster-menu-tx",
                                         status=Partner.Status.ACTIVE)
        connection = connect(partner)

        def fetch(token):
            self.assertFalse(transaction.get_connection().in_atomic_block)
            return trial_menu()

        with mock.patch.object(menu_sync, "fetch_menu", side_effect=fetch) as fetch_mock:
            sync_menu(connection_id=connection.pk)
        fetch_mock.assert_called_once()
        self.assertTrue(PosterRef.objects.filter(partner=partner, kind=Kind.PRODUCT).exists())


@skipUnless(db_connection.vendor == "postgresql", "нужны построчные блокировки")
class TwoSyncsAtOnceTests(TransactionTestCase):
    """Two syncs of one venue (the button and the night) do not both create
    the menu: the second waits for the venue's connection row and finds
    the first one's references."""

    def test_the_menu_is_made_once(self):
        partner = Partner.objects.create(name="Kava", slug="poster-menu-race",
                                         status=Partner.Status.ACTIVE)
        connection = connect(partner)
        barrier = threading.Barrier(2)
        load = menu_sync._load_refs

        def load_and_wait(partner_id):
            refs = load(partner_id)
            try:
                barrier.wait(timeout=1.5)
            except threading.BrokenBarrierError:
                pass
            return refs

        errors = []

        def run():
            try:
                sync_menu(connection_id=connection.pk)
            except Exception as exc:  # noqa: BLE001 — the test reports it
                errors.append(exc)
            finally:
                db_connection.close()

        with mock.patch.object(menu_sync, "fetch_menu", side_effect=lambda token: trial_menu()), \
                mock.patch.object(menu_sync, "_load_refs", side_effect=load_and_wait):
            threads = [threading.Thread(target=run) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(MenuItem.objects.filter(partner=partner, name="Сирники").count(), 1)
        self.assertEqual(PosterRef.objects.filter(partner=partner, kind=Kind.PRODUCT).count(), 4)
