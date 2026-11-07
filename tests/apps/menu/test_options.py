"""Variants and add-ons of an item (catalog step 2, owner 2026-11-07).

One list of variants with their own price; groups of add-ons set once and
attached to items or whole categories; module ``module_options_enabled``.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.menu.models import (
    MenuCategory,
    MenuItem,
    MenuItemVariant,
    Modifier,
    ModifierGroup,
)
from apps.menu.options import (
    ChoiceError,
    catalog_choices,
    choices_for,
    has_choices,
    needs_choice,
    price_from,
    resolve_choice,
)
from apps.partners.models import PartnerBotSettings
from tests.webapp.test_guest_api import make_venue


def options_module(partner, on=True):
    PartnerBotSettings.objects.filter(partner=partner).update(module_options_enabled=on)
    partner.refresh_from_db()


class OptionsFixture(TestCase):
    def setUp(self):
        self.partner = make_venue("Coffee")
        options_module(self.partner)
        self.drinks = MenuCategory.objects.create(partner=self.partner, name="Гарячі напої")
        self.coffee = MenuCategory.objects.create(
            partner=self.partner, name="Кавові", parent=self.drinks
        )
        self.latte = MenuItem.objects.create(
            partner=self.partner, category=self.coffee, name="Лате", price="60.00"
        )
        self.small = MenuItemVariant.objects.create(
            item=self.latte, name="250 мл", price="60.00", sort_order=1
        )
        self.big = MenuItemVariant.objects.create(
            item=self.latte, name="400 мл", price="75.00", sort_order=2
        )
        self.milk = ModifierGroup.objects.create(
            partner=self.partner, name="Молоко", min_choices=1, max_choices=1, sort_order=1
        )
        self.cow = Modifier.objects.create(group=self.milk, name="Коров'яче", sort_order=1)
        self.oat = Modifier.objects.create(
            group=self.milk, name="Вівсяне", price="15.00", sort_order=2
        )
        self.milk.items.add(self.latte)
        self.syrups = ModifierGroup.objects.create(
            partner=self.partner, name="Сироп", min_choices=0, max_choices=2, sort_order=2
        )
        self.caramel = Modifier.objects.create(
            group=self.syrups, name="Карамель", price="10.00", sort_order=1
        )
        self.vanilla = Modifier.objects.create(
            group=self.syrups, name="Ваніль", price="10.00", sort_order=2
        )
        self.mint = Modifier.objects.create(
            group=self.syrups, name="М'ята", price="10.00", sort_order=3
        )
        self.syrups.categories.add(self.drinks)  # covers the subcategory «Кава»

    def latte_choice(self, variant=None, modifiers=()):
        return resolve_choice(
            self.latte, variant_id=variant.id.hex if variant else None,
            modifier_ids=[modifier.id.hex for modifier in modifiers],
        )


class ResolveChoiceTests(OptionsFixture):
    def test_variant_and_add_ons_make_the_price_and_the_name(self):
        choice = self.latte_choice(self.big, [self.caramel, self.oat])
        self.assertEqual(choice.unit_price, Decimal("100.00"))  # 75 + 15 + 10
        # Groups in their order, add-ons in theirs, whatever order they came in.
        self.assertEqual(choice.name, "Лате, 400 мл (Вівсяне, Карамель)")
        self.assertEqual(choice.key, self.latte_choice(self.big, [self.oat, self.caramel]).key)
        self.assertEqual(
            [row["name"] for row in choice.snapshot()], ["Вівсяне", "Карамель"]
        )
        self.assertEqual(choice.snapshot()[0]["price"], "15.00")

    def test_a_variant_must_be_chosen_while_there_are_some(self):
        with self.assertRaises(ChoiceError):
            self.latte_choice(None, [self.cow])
        self.big.is_available = False
        self.big.save()
        with self.assertRaises(ChoiceError):
            self.latte_choice(self.big, [self.cow])
        other = MenuItem.objects.create(
            partner=self.partner, category=self.coffee, name="Капучино", price="55.00"
        )
        foreign = MenuItemVariant.objects.create(item=other, name="XL", price="90.00")
        with self.assertRaises(ChoiceError):
            self.latte_choice(foreign, [self.cow])

    def test_the_counts_of_a_group(self):
        with self.assertRaises(ChoiceError):  # milk is required
            self.latte_choice(self.small)
        with self.assertRaises(ChoiceError):  # one milk only
            self.latte_choice(self.small, [self.cow, self.oat])
        with self.assertRaises(ChoiceError):  # two syrups at most
            self.latte_choice(self.small, [self.cow, self.caramel, self.vanilla, self.mint])
        self.syrups.max_choices = 0  # as many as wanted
        self.syrups.save()
        choice = self.latte_choice(self.small, [self.cow, self.caramel, self.vanilla, self.mint])
        self.assertEqual(choice.unit_price, Decimal("90.00"))

    def test_add_ons_not_on_sale_or_not_for_this_item(self):
        self.oat.is_available = False
        self.oat.save()
        with self.assertRaises(ChoiceError):
            self.latte_choice(self.small, [self.oat])
        sauces = ModifierGroup.objects.create(partner=self.partner, name="Соус")
        ketchup = Modifier.objects.create(group=sauces, name="Кетчуп")
        with self.assertRaises(ChoiceError):  # the group is not attached to the latte
            self.latte_choice(self.small, [self.cow, ketchup])
        self.syrups.is_active = False
        self.syrups.save()
        with self.assertRaises(ChoiceError):
            self.latte_choice(self.small, [self.cow, self.caramel])

    def test_a_group_of_another_venue_is_never_offered(self):
        other = make_venue("Other")
        foreign = ModifierGroup.objects.create(partner=other, name="Чужа", min_choices=1)
        Modifier.objects.create(group=foreign, name="Щось")
        foreign.items.add(self.latte)  # only by mistake in code: Admin limits it
        self.assertNotIn(foreign, [entry.group for entry in choices_for(self.latte).groups])
        self.latte_choice(self.small, [self.cow])  # and does not hold the latte back

    def test_a_required_group_with_nothing_on_sale_does_not_block_the_item(self):
        Modifier.objects.filter(group=self.milk).update(is_available=False)
        self.assertEqual(self.latte_choice(self.small).unit_price, Decimal("60.00"))

    def test_a_plain_item_is_as_before(self):
        cake = MenuItem.objects.create(
            partner=self.partner, category=MenuCategory.objects.create(
                partner=self.partner, name="Десерти"
            ), name="Чізкейк", price="95.00",
        )
        choice = resolve_choice(cake)
        self.assertEqual(
            (choice.name, choice.unit_price, choice.key), ("Чізкейк", Decimal("95.00"), "")
        )
        self.assertEqual(choice.snapshot(), [])
        self.assertFalse(needs_choice(cake))
        self.assertFalse(has_choices(cake))


class ModuleTests(OptionsFixture):
    def test_switched_off_the_item_is_sold_as_before(self):
        options_module(self.partner, on=False)
        self.latte.refresh_from_db()
        choice = resolve_choice(self.latte)
        self.assertEqual(
            (choice.name, choice.unit_price, choice.key), ("Лате", Decimal("60.00"), "")
        )
        with self.assertRaises(ChoiceError):
            self.latte_choice(self.big, [self.cow])
        self.assertFalse(needs_choice(self.latte))
        self.assertEqual(price_from(self.latte), Decimal("60.00"))

    def test_what_needs_a_choice(self):
        self.assertTrue(needs_choice(self.latte))  # variants
        self.assertEqual(price_from(self.latte), Decimal("60.00"))
        self.small.is_available = False
        self.small.save()
        self.latte.refresh_from_db()
        self.assertEqual(price_from(self.latte), Decimal("75.00"))
        tea = MenuItem.objects.create(
            partner=self.partner, category=self.coffee, name="Чай", price="40.00"
        )
        self.assertFalse(needs_choice(tea))  # only the optional syrups
        self.assertTrue(has_choices(tea))
        self.milk.items.add(tea)
        self.assertTrue(needs_choice(tea))  # a required group


class CatalogChoicesTests(OptionsFixture):
    def test_one_load_for_a_whole_menu(self):
        tea = MenuItem.objects.create(
            partner=self.partner, category=self.coffee, name="Чай", price="40.00"
        )
        items = list(MenuItem.objects.filter(partner=self.partner).select_related("category"))
        # The module, variants, add-ons with their groups, the groups' items
        # and categories — however long the menu.
        with self.assertNumQueries(5):
            found = catalog_choices(self.partner, items)
        latte = found[self.latte.id]
        self.assertEqual([variant.name for variant in latte.variants], ["250 мл", "400 мл"])
        self.assertEqual([entry.group.name for entry in latte.groups], ["Молоко", "Сироп"])
        self.assertEqual([entry.group.name for entry in found[tea.id].groups], ["Сироп"])


class ModelRulesTests(OptionsFixture):
    def test_at_least_cannot_be_more_than_at_most(self):
        self.syrups.min_choices = 3
        with self.assertRaises(ValidationError):
            self.syrups.full_clean()
        self.syrups.max_choices = 0
        self.syrups.full_clean()  # no upper limit

    def test_the_venue_comes_from_the_item_and_the_group(self):
        self.assertEqual(self.small.partner_id, self.partner.id)
        self.assertEqual(self.caramel.partner_id, self.partner.id)


class OptionsAdminTests(TestCase):
    def setUp(self):
        from django.urls import reverse

        from apps.users.constants import AdminAccessPreset
        from apps.users.models import AdminAccessProfile, User

        self.reverse = reverse
        self.partner = make_venue("Shop")
        owner = User.objects.create_user(
            username="shop_owner", password="x", partner=self.partner, role=User.Role.OWNER,
            is_staff=True,
        )
        AdminAccessProfile.objects.create(
            user=owner, partner=self.partner, preset=AdminAccessPreset.OWNER,
            is_partner_owner=True, can_access_admin=True,
        )
        self.client.force_login(owner)
        self.item = MenuItem.objects.get(partner=self.partner, name="Лате")

    def test_variants_on_the_item_page_follow_the_module(self):
        url = self.reverse("admin:menu_menuitem_change", args=[self.item.pk])
        self.assertNotContains(self.client.get(url), "variants-TOTAL_FORMS")
        options_module(self.partner)
        self.assertContains(self.client.get(url), "variants-TOTAL_FORMS")

    def test_the_owner_makes_a_group_for_a_category(self):
        foreign = MenuCategory.objects.create(partner=make_venue("Other"), name="Чужа")
        page = self.client.get(self.reverse("admin:menu_modifiergroup_add"))
        categories = page.context["adminform"].form.fields["categories"].queryset
        self.assertNotIn(foreign, categories)
        response = self.client.post(self.reverse("admin:menu_modifiergroup_add"), {
            "name": "Сироп", "min_choices": "0", "max_choices": "2", "is_active": "on",
            "sort_order": "100", "categories": [str(self.item.category_id)],
            "modifiers-TOTAL_FORMS": "2", "modifiers-INITIAL_FORMS": "0",
            "modifiers-0-name": "Карамель", "modifiers-0-price": "10.00",
            "modifiers-0-is_available": "on", "modifiers-0-sort_order": "1",
            "modifiers-1-name": "Ваніль", "modifiers-1-price": "10.00",
            "modifiers-1-is_available": "on", "modifiers-1-sort_order": "2",
        })
        self.assertEqual(response.status_code, 302, getattr(response, "context", None)
                         and response.context["adminform"].form.errors)
        group = ModifierGroup.objects.get(name="Сироп")
        self.assertEqual(group.partner, self.partner)
        self.assertEqual(
            sorted(group.modifiers.values_list("name", "partner_id")),
            [("Ваніль", self.partner.id), ("Карамель", self.partner.id)],
        )
        options_module(self.partner)
        self.item.refresh_from_db()
        self.assertEqual([entry.group for entry in choices_for(self.item).groups], [group])

    def test_at_least_more_than_at_most_is_a_form_error(self):
        response = self.client.post(self.reverse("admin:menu_modifiergroup_add"), {
            "name": "Соус", "min_choices": "3", "max_choices": "1", "is_active": "on",
            "sort_order": "100", "modifiers-TOTAL_FORMS": "0", "modifiers-INITIAL_FORMS": "0",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("min_choices", response.context["adminform"].form.errors)
