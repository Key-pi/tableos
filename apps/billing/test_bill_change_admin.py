"""Admin entry points for bill changes (B3.2 part A)."""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.billing.models import Bill, BillAdjustment, BillItem
from apps.billing.services import create_bill_from_orders
from apps.employees.forms import EmployeeProfileAdminForm
from apps.employees.models import EmployeeProfile
from apps.menu.models import MenuCategory, MenuItem
from apps.orders.models import Order
from apps.orders.services import create_order_from_session
from apps.partners.models import Partner, PartnerBotSettings
from apps.tables.models import Table
from apps.tables.services import activate_table_session
from apps.users.constants import AdminAccessPreset
from apps.users.models import AdminAccessProfile, User


class BillChangeAdminTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(
            name="Admin Correction Venue",
            slug="admin-correction-venue",
            status=Partner.Status.ACTIVE,
        )
        PartnerBotSettings.objects.create(partner=self.partner, module_billing_enabled=True)
        table = Table.objects.create(partner=self.partner, number=2, qr_token="admincorr02")
        category = MenuCategory.objects.create(partner=self.partner, name="Bar")
        menu_item = MenuItem.objects.create(
            partner=self.partner, category=category, name="Tea", price="100.00"
        )
        session = activate_table_session(
            partner_id=self.partner.id,
            telegram_id=7720001,
            payload=table.deep_link_payload,
        )
        self.orders = [
            create_order_from_session(
                partner_id=self.partner.id,
                table_session=session,
                items=[
                    {
                        "menu_item_id": menu_item.id,
                        "item_name": menu_item.name,
                        "unit_price": menu_item.price,
                        "quantity": 1,
                    }
                ],
            )
            for _ in range(2)
        ]
        self.bill = create_bill_from_orders(
            partner_id=self.partner.id,
            order_ids=[str(order.id) for order in self.orders],
        )
        self.owner = self._admin("venue_owner", preset=AdminAccessPreset.OWNER, owner=True)
        self.manager = self._admin("venue_manager", preset=AdminAccessPreset.MANAGER, owner=False)

    def _admin(self, username, *, preset, owner):
        user = User.objects.create_user(
            username=username,
            password="pass12345",
            partner=self.partner,
            role=User.Role.OWNER if owner else User.Role.MANAGER,
            is_staff=True,
        )
        AdminAccessProfile.objects.create(
            user=user,
            partner=self.partner,
            preset=preset,
            is_partner_owner=owner,
            can_access_admin=True,
        )
        return user

    def _post_action(self, user, model, action, ids, **extra):
        self.client.force_login(user)
        url = reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist")
        return self.client.post(
            url,
            {"action": action, "_selected_action": [str(pk) for pk in ids], **extra},
        )

    def test_void_action_asks_for_reason_then_voids(self):
        page = self._post_action(self.owner, Bill, "void_selected_bills", [self.bill.id])
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Аннулировать счета")
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.status, Bill.Status.DRAFT)

        self._post_action(
            self.owner,
            Bill,
            "void_selected_bills",
            [self.bill.id],
            apply="1",
            reason_code=BillAdjustment.ReasonCode.INPUT_ERROR,
            reason_text="duplicate bill",
        )

        self.bill.refresh_from_db()
        self.assertEqual(self.bill.status, Bill.Status.CANCELED)
        adjustment = BillAdjustment.objects.get(bill=self.bill)
        self.assertEqual(adjustment.actor, self.owner)
        self.assertEqual(adjustment.reason_text, "duplicate bill")

    def test_manager_without_grant_cannot_void(self):
        self._post_action(
            self.manager,
            Bill,
            "void_selected_bills",
            [self.bill.id],
            apply="1",
            reason_code=BillAdjustment.ReasonCode.OTHER,
        )

        self.bill.refresh_from_db()
        self.assertEqual(self.bill.status, Bill.Status.DRAFT)
        self.assertFalse(BillAdjustment.objects.exists())

    def test_remove_line_action_recalculates_bill(self):
        line = BillItem.objects.get(bill=self.bill, order=self.orders[1])

        self._post_action(
            self.owner,
            BillItem,
            "remove_selected_lines",
            [line.id],
            apply="1",
            reason_code=BillAdjustment.ReasonCode.COMPLIMENT,
        )

        self.bill.refresh_from_db()
        line.refresh_from_db()
        self.assertFalse(line.is_active)
        self.assertEqual(self.bill.total_amount, Decimal("100.00"))

    def test_order_admin_cancel_of_billed_order_goes_through_the_bill(self):
        page = self._post_action(self.owner, Order, "mark_as_canceled", [self.orders[1].id])
        self.assertContains(page, "Отменить заказы")

        self._post_action(
            self.owner,
            Order,
            "mark_as_canceled",
            [self.orders[1].id],
            apply="1",
            reason_code=BillAdjustment.ReasonCode.GUEST_REFUSED,
        )

        self.orders[1].refresh_from_db()
        self.bill.refresh_from_db()
        self.assertEqual(self.orders[1].status, Order.Status.CANCELED)
        self.assertEqual(self.bill.total_amount, Decimal("100.00"))
        self.assertEqual(
            BillAdjustment.objects.get(bill=self.bill).kind,
            BillAdjustment.Kind.ORDER_CANCELED,
        )

    def test_only_the_owner_can_grant_the_bill_permission(self):
        staff_user = User.objects.create_user(
            username="floor_cashier", password="pass12345", partner=self.partner
        )
        employee = EmployeeProfile.objects.create(partner=self.partner, user=staff_user)
        data = {
            "partner": self.partner.id,
            "user": staff_user.id,
            "hourly_rate": "0",
            "commission_rate": "0",
            "can_adjust_bills": "on",
            "is_active": "on",
        }

        manager_form = EmployeeProfileAdminForm(
            data=data, instance=employee, actor_user=self.manager
        )
        self.assertTrue(manager_form.fields["can_adjust_bills"].disabled)
        self.assertTrue(manager_form.is_valid(), manager_form.errors)
        manager_form.save()
        employee.refresh_from_db()
        self.assertFalse(employee.can_adjust_bills)

        owner_form = EmployeeProfileAdminForm(data=data, instance=employee, actor_user=self.owner)
        self.assertTrue(owner_form.is_valid(), owner_form.errors)
        owner_form.save()
        employee.refresh_from_db()
        self.assertTrue(employee.can_adjust_bills)
