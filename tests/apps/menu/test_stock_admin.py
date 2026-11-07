"""Stock in Admin (catalog step 3, owner 2026-11-07): the fields follow the
module; a new number is a count («пересчёт») in the journal; saving the rest
of the page never puts back what orders took meanwhile."""

from django.db.models.fields.files import FieldFile
from django.test import TestCase
from django.urls import reverse

from apps.menu.models import MenuItem, MenuItemVariant, StockMovement
from apps.users.constants import AdminAccessPreset
from apps.users.models import AdminAccessProfile, User
from tests.apps.menu.test_options import options_module
from tests.apps.menu.test_stock import stock_module
from tests.webapp.test_guest_api import make_venue


def form_data(response) -> dict:
    """What the change page would post back as it is."""

    def put(data, form):
        for name in form.fields:
            value = form[name].value()
            key = form.add_prefix(name)
            if value is None or value is False:
                continue
            if value is True:
                data[key] = "on"
            elif isinstance(value, (list, tuple)):
                data[key] = [str(entry) for entry in value]
            elif isinstance(value, FieldFile):
                continue  # a file: nothing new uploaded
            else:
                data[key] = str(value)

    data: dict = {}
    put(data, response.context["adminform"].form)
    for inline in response.context["inline_admin_formsets"]:
        formset = inline.formset
        put(data, formset.management_form)
        for form in formset.forms:
            put(data, form)
    return data


class StockAdminTests(TestCase):
    def setUp(self):
        self.partner = make_venue("Shop")
        stock_module(self.partner)
        options_module(self.partner)
        self.owner = User.objects.create_user(
            username="stock_owner", password="x", partner=self.partner, role=User.Role.OWNER,
            is_staff=True,
        )
        AdminAccessProfile.objects.create(
            user=self.owner, partner=self.partner, preset=AdminAccessPreset.OWNER,
            is_partner_owner=True, can_access_admin=True,
        )
        self.client.force_login(self.owner)
        self.latte = MenuItem.objects.get(partner=self.partner, name="Лате")
        self.mouthpiece = MenuItem.objects.create(
            partner=self.partner, category=self.latte.category, name="Мундштук",
            price="100.00", track_stock=True, stock_quantity=1,
        )
        self.shirt = MenuItem.objects.create(
            partner=self.partner, category=self.latte.category, name="Футболка",
            price="500.00", track_stock=True,
        )
        self.m = MenuItemVariant.objects.create(
            item=self.shirt, name="M", price="500.00", stock_quantity=2, sort_order=1
        )
        self.l = MenuItemVariant.objects.create(
            item=self.shirt, name="L", price="500.00", stock_quantity=0, sort_order=2
        )

    def page(self, item):
        response = self.client.get(reverse("admin:menu_menuitem_change", args=[item.pk]))
        self.assertEqual(response.status_code, 200)
        return response

    def save(self, item, data):
        response = self.client.post(reverse("admin:menu_menuitem_change", args=[item.pk]), data)
        self.assertEqual(response.status_code, 302, getattr(response, "context", None) and [
            response.context["adminform"].form.errors,
            [inline.formset.errors for inline in response.context["inline_admin_formsets"]],
        ])

    def level(self, row):
        row.refresh_from_db()
        return row.stock_quantity

    def test_the_stock_part_follows_the_module(self):
        self.assertIn("stock_quantity", self.page(self.latte).context["adminform"].form.fields)
        self.assertContains(self.page(self.shirt), "variants-0-stock_quantity")
        # The level the page showed goes back with it (hidden).
        self.assertContains(self.page(self.shirt), 'name="variants-0-stock_seen"')
        self.assertContains(self.page(self.mouthpiece), 'name="stock_seen"')
        stock_module(self.partner, on=False)
        self.assertNotIn("stock_quantity", self.page(self.latte).context["adminform"].form.fields)
        self.assertNotContains(self.page(self.shirt), "variants-0-stock_quantity")

    def test_a_new_number_is_a_count(self):
        data = form_data(self.page(self.mouthpiece))
        data["stock_quantity"] = "7"
        self.save(self.mouthpiece, data)
        self.assertEqual(self.level(self.mouthpiece), 7)
        movement = StockMovement.objects.get()
        self.assertEqual((movement.reason, movement.delta, movement.balance_after, movement.actor),
                         ("count", 6, 7, self.owner))

    def test_saving_the_rest_keeps_what_orders_took(self):
        data = form_data(self.page(self.mouthpiece))
        MenuItem.objects.filter(pk=self.mouthpiece.pk).update(stock_quantity=0)  # an order took it
        data["price"] = "120.00"
        self.save(self.mouthpiece, data)
        self.mouthpiece.refresh_from_db()
        self.assertEqual((self.mouthpiece.price, self.mouthpiece.stock_quantity), (120, 0))
        self.assertFalse(StockMovement.objects.exists())

    def test_variants_are_counted_each(self):
        data = form_data(self.page(self.shirt))
        MenuItemVariant.objects.filter(pk=self.m.pk).update(stock_quantity=1)  # an order took one
        index = next(key.split("-")[1] for key, value in data.items()
                     if key.endswith("-id") and value == str(self.l.pk))
        data[f"variants-{index}-stock_quantity"] = "4"
        self.save(self.shirt, data)
        self.assertEqual((self.level(self.m), self.level(self.l)), (1, 4))
        movement = StockMovement.objects.get()
        self.assertEqual((movement.variant, movement.delta, movement.reason), (self.l, 4, "count"))

    def test_a_new_variant_with_a_number_is_counted(self):
        data = form_data(self.page(self.shirt))
        index = data["variants-TOTAL_FORMS"]
        data.update({
            "variants-TOTAL_FORMS": str(int(index) + 1),
            f"variants-{index}-name": "XL", f"variants-{index}-price": "550.00",
            f"variants-{index}-is_available": "on", f"variants-{index}-sort_order": "3",
            f"variants-{index}-stock_quantity": "3",
        })
        self.save(self.shirt, data)
        xl = MenuItemVariant.objects.get(name="XL")
        self.assertEqual(xl.stock_quantity, 3)
        self.assertEqual(StockMovement.objects.get().variant, xl)

    def test_the_journal_is_read_only(self):
        self.client.post(reverse("admin:menu_menuitem_change", args=[self.mouthpiece.pk]),
                         {**form_data(self.page(self.mouthpiece)), "stock_quantity": "3"})
        page = self.client.get(reverse("admin:menu_stockmovement_changelist"))
        self.assertContains(page, "Мундштук")
        self.assertEqual(self.client.get(reverse("admin:menu_stockmovement_add")).status_code, 403)
