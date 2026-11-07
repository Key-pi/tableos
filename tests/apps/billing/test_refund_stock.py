"""A refund puts goods back in stock only when the staff tick it (catalog
step 3, owner 2026-11-07); a refund that cancels the order gives nothing
back by itself."""

from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase

from apps.billing.models import Bill, BillItem, Refund
from apps.billing.remote_orders import confirm_transfer, save_guest_phone
from apps.billing.services import refund_bill_lines, refund_can_restock
from apps.menu.models import StockMovement
from apps.orders.models import Order
from apps.partners.models import PartnerBotSettings
from apps.users.models import User
from tests.apps.billing.test_remote_orders import TG, PickupFixtureMixin


class RefundStockFixture(PickupFixtureMixin, TestCase):
    counted = True

    def setUp(self):
        super().setUp()
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            module_stock_enabled=self.counted
        )
        self.item.track_stock = True
        self.item.stock_quantity = 5
        self.item.save()
        save_guest_phone(partner_id=self.partner.id, telegram_id=TG, phone="+380501112233")
        self.owner = User.objects.create_user(
            username="refund_owner", password="x", partner=self.partner, role=User.Role.OWNER
        )
        self._cart(quantity=2)
        self.order = confirm_transfer(order=self._place(), actor=self.cashier)
        self.bill = Bill.objects.get(bill_orders__order=self.order)
        self.line = BillItem.objects.get(bill=self.bill, order_item__menu_item=self.item)

    def stock(self):
        self.item.refresh_from_db()
        return self.item.stock_quantity

    def refund(self, restock):
        with self.captureOnCommitCallbacks(execute=True), patch(
            "apps.notifications.services.send_guest_refund_notice"
        ):
            return refund_bill_lines(
                bill=self.bill, bill_item_ids=[self.line.id], actor=self.owner,
                reason_code=Refund.ReasonCode.QUALITY, method=Refund.Method.CASH,
                restock=restock,
            )


class RefundStockTests(RefundStockFixture):
    def test_the_order_took_two(self):
        self.assertEqual(self.stock(), 3)

    def test_not_ticked_nothing_comes_back_even_when_the_order_is_canceled(self):
        self.refund(restock=False)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CANCELED)
        self.assertEqual(self.stock(), 3)

    def test_ticked_the_goods_come_back_once(self):
        self.refund(restock=True)
        self.assertEqual(self.stock(), 5)
        self.assertEqual(
            list(StockMovement.objects.filter(reason="refund").values_list("delta", flat=True)),
            [2],
        )

    def test_the_tick_is_offered_only_while_something_can_come_back(self):
        self.assertTrue(refund_can_restock(self.bill, [self.line.id]))
        self.refund(restock=True)
        self.assertFalse(refund_can_restock(self.bill, [self.line.id]))


class RefundWithoutStockTests(RefundStockFixture):
    """The order was placed while stock was not counted: it took nothing, so
    nothing comes back and no tick is offered."""

    counted = False

    def test_nothing_to_put_back(self):
        self.assertFalse(refund_can_restock(self.bill, [self.line.id]))
        self.refund(restock=True)
        self.assertEqual(self.stock(), 5)
        self.assertFalse(StockMovement.objects.exists())


class RefundStockAdminTests(RefundStockFixture):
    """The Admin refund page has the same tick."""

    def admin_refund(self, data):
        from django.contrib import admin
        from django.test import RequestFactory

        from apps.billing.admin import BillItemAdmin

        model_admin = BillItemAdmin(BillItem, admin.site)
        request = RequestFactory().post("/admin/", data)
        request.user = self.owner
        with patch.object(model_admin, "message_user"), patch(
            "apps.notifications.services.send_guest_refund_notice"
        ), self.captureOnCommitCallbacks(execute=True):
            return model_admin.refund_selected_lines(
                request, BillItem.objects.filter(id=self.line.id)
            )

    def test_the_page_offers_the_tick(self):
        response = self.admin_refund({})
        self.assertIn("restock", response.context_data["form"].fields)

    def test_ticked_in_admin_the_goods_come_back(self):
        self.admin_refund({"apply": "1", "reason_code": "quality", "method": "cash",
                           "reason_text": "", "restock": "on"})
        self.assertEqual(Refund.objects.count(), 1)
        self.assertEqual(self.stock(), 5)
