"""Big Admin forms in tabs and by the venue's modules (admin step 4, owner
2026-11-10, PQ-050): bot settings by what the owner sets up, an order and a
bill with their rows, a menu item without the ways to order the venue has
switched off."""

from decimal import Decimal

from django.contrib import admin
from django.test import TestCase
from django.urls import reverse

from apps.billing.models import Bill, Payment
from apps.billing.services import create_bill_from_orders, record_payment
from apps.menu.models import MenuItem
from apps.orders.models import Order, OrderItem, OrderStatusHistory
from apps.partners.admin import (
    BOT_SETTINGS_GROUPS,
    BOT_SETTINGS_SHOWN_WHEN,
    PartnerBotSettingsAdmin,
    PartnerBotSettingsInline,
)
from apps.partners.models import PartnerBotSettings
from apps.users.constants import AdminAccessPreset
from apps.users.models import User
from tests.apps.analytics.test_dashboard import ON_RECEIPT, order
from tests.apps.users.test_admin_navigation import admin_user, modules
from tests.webapp.test_guest_api import make_venue


def flat(fields):
    for entry in fields:
        if isinstance(entry, (list, tuple)):
            yield from flat(entry)
        else:
            yield entry


class BotSettingsGroupsTests(TestCase):
    def test_every_setting_is_in_exactly_one_group(self):
        grouped = [name for _title, _help, fields in BOT_SETTINGS_GROUPS for name in flat(fields)]
        self.assertEqual(len(grouped), len(set(grouped)))
        editable = {
            field.name for field in PartnerBotSettings._meta.fields
            if field.editable and not field.primary_key and field.name != "partner"
        }
        self.assertEqual(set(grouped), editable)

    def test_the_card_inline_and_the_page_show_the_same_settings(self):
        page = [name for _t, options in PartnerBotSettingsAdmin.fieldsets
                for name in flat(options["fields"])]
        inline = [name for _t, options in PartnerBotSettingsInline.fieldsets
                  for name in flat(options["fields"])]
        self.assertEqual(page, inline)
        self.assertTrue(all("tab" in options["classes"]
                            for _t, options in PartnerBotSettingsAdmin.fieldsets))

    def test_a_modules_settings_follow_its_switch_and_switches_stay(self):
        booleans = {
            field.name for field in PartnerBotSettings._meta.fields
            if field.get_internal_type() == "BooleanField"
        }
        for name, switches in BOT_SETTINGS_SHOWN_WHEN.items():
            with self.subTest(name):
                self.assertFalse(name.startswith("module_"))  # PQ-033: always shown
                self.assertTrue(set(switches) <= booleans)
        self.assertEqual(
            PartnerBotSettingsAdmin.conditional_fields["shipping_fee"],
            "module_shipping_enabled == true && shipping_cost_fixed == true",
        )


class BotSettingsPageTests(TestCase):
    def setUp(self):
        self.partner = make_venue("Kava")
        self.settings = PartnerBotSettings.objects.get(partner=self.partner)
        self.owner = admin_user(self.partner, "form_owner", AdminAccessPreset.OWNER, owner=True)
        self.client.force_login(self.owner)
        self.url = reverse("admin:partners_partnerbotsettings_change", args=[self.settings.pk])

    def test_tabs_and_conditions_are_on_the_page(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        for title in ("Каталог", "За столом", "Самовывоз", "Доставка", "Почта", "Перевод"):
            self.assertContains(response, title)
        self.assertContains(response, 'x-show="module_delivery_enabled == true"')
        self.assertContains(response, "transfer_payment_details")

    def test_saving_keeps_what_is_not_shown(self):
        """A switched-off module's settings are hidden, not lost."""

        PartnerBotSettings.objects.filter(pk=self.settings.pk).update(
            module_delivery_enabled=False, delivery_fee=Decimal("70.00")
        )
        form = self.client.get(self.url).context["adminform"].form
        data = {}
        for name, field in form.fields.items():
            value = form.initial.get(name, field.initial)
            if isinstance(value, bool):
                if value:
                    data[name] = "on"
            elif value is not None:
                data[name] = str(value)
        data["duplicate_request_cooldown_seconds"] = "9"
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 302, getattr(response, "context", None)
                         and response.context["adminform"].form.errors)
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.duplicate_request_cooldown_seconds, 9)
        self.assertEqual(self.settings.delivery_fee, Decimal("70.00"))
        self.assertFalse(self.settings.module_delivery_enabled)

    def test_a_venue_neither_adds_nor_deletes_its_settings(self):
        page = admin.site._registry[PartnerBotSettings]
        request = self.client.get(self.url).wsgi_request
        self.assertFalse(page.has_add_permission(request))
        self.assertFalse(page.has_delete_permission(request, self.settings))
        self.assertEqual(
            self.client.post(
                reverse("admin:partners_partnerbotsettings_delete", args=[self.settings.pk]),
                {"post": "yes"},
            ).status_code,
            403,
        )
        self.assertTrue(PartnerBotSettings.objects.filter(pk=self.settings.pk).exists())


class OrderAndBillPageTests(TestCase):
    def setUp(self):
        self.partner = make_venue("Kava")
        self.owner = admin_user(self.partner, "rows_owner", AdminAccessPreset.OWNER, owner=True)
        self.client.force_login(self.owner)
        self.latte = MenuItem.objects.get(partner=self.partner, name="Лате")

    def order_with_a_line(self, **kwargs):
        made = order(self.partner, **kwargs)
        OrderItem.objects.create(
            partner=self.partner, order=made, menu_item=self.latte, item_name="Лате великий",
            unit_price="80.00", quantity=1,
        )
        OrderStatusHistory.objects.create(
            partner=self.partner, order=made, from_status="", to_status=made.status,
            note="Створено гостем",
        )
        return made

    def page(self, made):
        response = self.client.get(reverse("admin:orders_order_change", args=[made.pk]))
        self.assertEqual(response.status_code, 200)
        return response

    def shown(self, response) -> set[str]:
        return set(flat(
            options["fields"] for _title, options in response.context["adminform"].fieldsets
        ))

    def test_the_owner_sees_the_orders_lines_and_history(self):
        made = self.order_with_a_line(channel=Order.Channel.TABLE)
        response = self.page(made)
        self.assertContains(response, "Лате великий")
        self.assertContains(response, "Створено гостем")
        formsets = response.context["inline_admin_formsets"]
        self.assertEqual(len(formsets), 2)
        for formset in formsets:
            self.assertFalse(formset.has_add_permission)
            self.assertFalse(formset.has_change_permission)
            self.assertNotIn("partner", list(flat(formset.fieldsets[0][1]["fields"])))

    def test_only_the_part_of_its_own_way_to_order(self):
        cases = {
            Order.Channel.TABLE: ({"table"}, {"delivery_address", "tracking_number",
                                              "requested_for"}),
            Order.Channel.PICKUP: ({"requested_for"}, {"table", "delivery_address",
                                                       "shipping_carrier"}),
            Order.Channel.DELIVERY: ({"delivery_address", "requested_for"},
                                     {"table", "tracking_number"}),
            Order.Channel.SHIPPING: ({"tracking_number", "shipping_destination"},
                                     {"table", "delivery_address", "requested_for"}),
        }
        for channel, (present, absent) in cases.items():
            with self.subTest(channel):
                shown = self.shown(self.page(order(self.partner, channel=channel)))
                self.assertTrue(present <= shown, present - shown)
                self.assertFalse(absent & shown)
                self.assertTrue({"public_id", "status", "total_amount", "created_at"} <= shown)
                self.assertNotIn("partner", shown)

    def test_the_owner_sees_the_bills_lines_and_payments(self):
        made = self.order_with_a_line(channel=Order.Channel.PICKUP, timing=ON_RECEIPT)
        Order.objects.filter(pk=made.pk).update(status=Order.Status.ACCEPTED)
        bill = create_bill_from_orders(partner_id=self.partner.id, order_ids=[made.id])
        record_payment(bill=bill, amount=Decimal("30.00"), method=Payment.Method.CASH,
                       created_by=self.owner)
        response = self.client.get(reverse("admin:billing_bill_change", args=[bill.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Лате великий")
        models = {formset.opts.model.__name__
                  for formset in response.context["inline_admin_formsets"]}
        self.assertEqual(models, {"BillItem", "Payment", "Refund", "BillAdjustment", "BillOrder"})
        for formset in response.context["inline_admin_formsets"]:
            self.assertFalse(formset.has_change_permission, formset.opts.model.__name__)
        self.assertEqual(Bill.objects.get(pk=bill.pk).paid_amount, Decimal("30.00"))

    def test_the_platform_keeps_the_venue_column_in_rows(self):
        made = self.order_with_a_line(channel=Order.Channel.TABLE)
        root = User.objects.create_superuser(username="rows_root", password="x", email="")
        self.client.force_login(root)
        formsets = self.page(made).context["inline_admin_formsets"]
        self.assertIn("partner", list(flat(formsets[0].fieldsets[0][1]["fields"])))


class ItemFormByModulesTests(TestCase):
    def setUp(self):
        self.partner = make_venue("Kava")
        self.owner = admin_user(self.partner, "item_form_owner", AdminAccessPreset.OWNER,
                                owner=True)
        self.client.force_login(self.owner)
        self.latte = MenuItem.objects.get(partner=self.partner, name="Лате")

    def test_ways_to_order_the_venue_switched_off_are_not_asked_about(self):
        modules(self.partner, module_tables_enabled=True, module_pickup_enabled=True,
                module_delivery_enabled=False, module_shipping_enabled=False)
        MenuItem.objects.filter(pk=self.latte.pk).update(available_for_delivery=False)
        url = reverse("admin:menu_menuitem_change", args=[self.latte.pk])
        fields = self.client.get(url).context["adminform"].form.fields
        self.assertIn("available_at_table", fields)
        self.assertIn("available_for_pickup", fields)
        self.assertNotIn("available_for_delivery", fields)
        self.assertNotIn("available_for_shipping", fields)
        listed = self.client.get(reverse("admin:menu_menuitem_changelist"))
        columns = listed.context["cl"].list_display
        self.assertIn("available_at_table", columns)
        self.assertNotIn("available_for_delivery", columns)
        # Its mark keeps its value when the page is saved without it.
        self.latte.refresh_from_db()
        self.assertFalse(self.latte.available_for_delivery)

    def test_the_parts_of_the_item_are_tabs(self):
        url = reverse("admin:menu_menuitem_change", args=[self.latte.pk])
        fieldsets = self.client.get(url).context["adminform"].fieldsets
        tabbed = [str(title) for title, options in fieldsets if "tab" in options.get("classes", ())]
        self.assertIn("Где продаётся", tabbed)
        self.assertNotIn("None", tabbed)
