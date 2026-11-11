"""An order to a table from the staff tablet (PQ-052, step 2, owner 2026-11-11).

A guest sat down without the bot: a waiter, a bartender, a manager or the
owner taps the table and orders for them. The order is an ordinary table
order — it goes to the kitchen and bar screens accepted, the table's bill
grows, the guest pays at the end. Without a code the order belongs to the
visit's anonymous guest (one per company at the table — never one for the
whole venue); with a guest's code it is that guest's, and only then bonuses
are earned. A module of its own (``module_table_orders_enabled``)."""

import threading
from decimal import Decimal
from unittest import skipUnless

from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase

from apps.billing.models import Payment
from apps.billing.services import record_payment
from apps.billing.table_bills import create_shared_bill_for_table
from apps.bonuses.models import BonusProgram
from apps.menu.models import MenuCategory, MenuItem
from apps.notifications.services import send_guest_order_status_update_now
from apps.orders.models import Order
from apps.orders.services import OrderFlowError, create_staff_table_order
from apps.partners.models import PartnerBotSettings
from apps.tables.floor_plan import table_states
from apps.tables.models import Table, TableSession, TableVisit
from apps.tables.services import activate_table_session
from apps.users.models import GuestProfile, User
from apps.users.services import find_guest_by_customer_code
from tests.webapp.test_guest_api import make_bot, make_venue
from tests.webapp.test_staff_api import staff


def venue(name="Kava"):
    partner = make_venue(name)
    PartnerBotSettings.objects.filter(partner=partner).update(
        module_floor_plan_enabled=True, module_table_orders_enabled=True,
        module_billing_enabled=True,
    )
    return partner


class TableOrderFixture(TestCase):
    def setUp(self):
        self.partner = venue()
        self.table = Table.objects.create(partner=self.partner, number=5)
        self.other_table = Table.objects.create(partner=self.partner, number=6)
        self.latte = MenuItem.objects.get(partner=self.partner, name="Лате")
        self.hookah = MenuItem.objects.get(partner=self.partner, name="Кальян")
        self.waiter = staff(self.partner, "kava_waiter", User.Role.WAITER, 7701001)

    def order(self, *, table=None, visit=None, code="", items=None, actor=None, key="",
              comment=""):
        return create_staff_table_order(
            partner_id=self.partner.id, actor=actor or self.waiter,
            table_id=(table or self.table).id, visit_id=visit, customer_code=code,
            items=[{"menu_item_id": self.latte.id, "quantity": 2}] if items is None else items,
            comment=comment, request_key=key,
        )

    def scan(self, telegram_id, table=None):
        return activate_table_session(
            partner_id=self.partner.id, telegram_id=telegram_id,
            payload=(table or self.table).deep_link_payload,
        )


class AnonymousGuestTests(TableOrderFixture):
    def test_a_free_table_gets_a_visit_and_an_accepted_order(self):
        order = self.order(comment="без цукру")
        self.assertEqual((order.channel, order.status, order.table_id),
                         (Order.Channel.TABLE, Order.Status.ACCEPTED, self.table.id))
        self.assertEqual(order.total_amount, Decimal("160.00"))
        self.assertEqual((order.comment, order.assigned_employee), ("без цукру", self.waiter))
        guest = order.guest
        self.assertIsNone(guest.telegram_account_id)
        self.assertFalse(guest.is_subscribed)
        self.assertEqual(guest.staff_label(), "Гость без бота")
        self.assertEqual(order.table_session.visit.status, TableVisit.Status.ACTIVE)
        self.assertEqual(table_states(self.partner.id)[self.table.id]["state"], "busy")

    def test_one_anonymous_guest_per_visit(self):
        first = self.order()
        second = self.order(visit=first.table_session.visit_id,
                            items=[{"menu_item_id": self.hookah.id, "quantity": 1}])
        self.assertEqual(first.guest_id, second.guest_id)
        self.assertEqual(first.table_session_id, second.table_session_id)
        # Another table, another company: its own anonymous guest.
        third = self.order(table=self.other_table)
        self.assertNotEqual(third.guest_id, first.guest_id)
        self.assertEqual(
            TableSession.objects.filter(guest_id=first.guest_id, status="active").count(), 1
        )

    def test_joins_the_company_already_at_the_table(self):
        session = self.scan(880101)
        order = self.order(visit=session.visit_id)
        self.assertEqual(order.table_session.visit_id, session.visit_id)
        self.assertNotEqual(order.guest_id, session.guest_id)
        bill = create_shared_bill_for_table(partner_id=self.partner.id, table_id=self.table.id,
                                            visit_id=session.visit_id)
        self.assertEqual(set(bill.bill_orders.values_list("order_id", flat=True)), {order.id})

    def test_a_page_out_of_date_is_refused(self):
        session = self.scan(880101)
        with self.assertRaisesMessage(OrderFlowError, "обновите экран"):
            self.order()  # the table looked free
        from apps.tables.services import close_table_visit

        close_table_visit(session.visit)
        with self.assertRaisesMessage(OrderFlowError, "обновите экран"):
            self.order(visit=session.visit_id)  # that company has left
        self.assertFalse(Order.objects.exists())

    def test_no_bonuses_messages_or_codes_for_the_anonymous_guest(self):
        BonusProgram.objects.create(
            partner=self.partner, name="5%",
            trigger_event=BonusProgram.TriggerEvent.ORDER_COMPLETED,
            program_type=BonusProgram.ProgramType.CASHBACK, percent=Decimal("5"), is_active=True,
        )
        order = self.order()
        bill = create_shared_bill_for_table(partner_id=self.partner.id, table_id=self.table.id,
                                            visit_id=order.table_session.visit_id)
        record_payment(bill=bill, amount=bill.total_amount, method=Payment.Method.CASH)
        guest = GuestProfile.objects.get(id=order.guest_id)
        self.assertEqual(guest.loyalty_balance, Decimal("0"))
        self.assertFalse(guest.bonus_transactions.exists())
        # No Telegram to write to: nothing is sent, nothing breaks (the venue
        # has a working bot, so only the guest stops it).
        make_bot(self.partner)
        self.assertFalse(send_guest_order_status_update_now(
            order_id=order.id, from_status="new", to_status="accepted"))
        # No broadcasts, even if someone ticks «subscribed» in Admin.
        from apps.notifications.repositories import NotificationPreferenceRepository

        GuestProfile.objects.filter(id=guest.id).update(is_subscribed=True)
        self.assertNotIn(guest, NotificationPreferenceRepository.marketing_guests(self.partner.id))
        self.assertNotIn(guest, NotificationPreferenceRepository.marketing_guests_for(
            [self.partner.id]))
        # A cashier cannot name the anonymous guest by its code.
        with self.assertRaises(GuestProfile.DoesNotExist):
            find_guest_by_customer_code(self.partner.id, guest.customer_code)


class GuestByCodeTests(TableOrderFixture):
    def test_the_order_is_the_named_guests(self):
        session = self.scan(880201, table=self.other_table)
        guest = session.guest
        order = self.order(code=guest.customer_code.lower())
        self.assertEqual(order.guest_id, guest.id)
        # The guest is at this table now, as after scanning its QR.
        self.assertEqual(order.table_session.table_id, self.table.id)
        session.refresh_from_db()
        self.assertNotEqual(session.status, TableSession.Status.ACTIVE)

    def test_bonuses_go_to_the_named_guest(self):
        BonusProgram.objects.create(
            partner=self.partner, name="5%",
            trigger_event=BonusProgram.TriggerEvent.ORDER_COMPLETED,
            program_type=BonusProgram.ProgramType.CASHBACK, percent=Decimal("5"), is_active=True,
        )
        guest = self.scan(880202, table=self.other_table).guest
        order = self.order(code=guest.customer_code)
        bill = create_shared_bill_for_table(partner_id=self.partner.id, table_id=self.table.id,
                                            visit_id=order.table_session.visit_id)
        record_payment(bill=bill, amount=bill.total_amount, method=Payment.Method.CASH)
        guest.refresh_from_db()
        self.assertEqual(guest.loyalty_balance, Decimal("8.00"))

    def test_an_unknown_code_is_refused(self):
        with self.assertRaisesMessage(OrderFlowError, "Гость с кодом"):
            self.order(code="NOPE00")
        self.assertFalse(TableVisit.objects.exists())


class RulesTests(TableOrderFixture):
    def test_what_a_guest_could_not_order_neither_can_staff(self):
        bar = MenuCategory.objects.create(partner=self.partner, name="Тільки самовивіз")
        takeaway = MenuItem.objects.create(partner=self.partner, category=bar, name="Зерно",
                                           price="300.00", available_at_table=False)
        with self.assertRaisesMessage(OrderFlowError, "Зерно"):
            self.order(items=[{"menu_item_id": takeaway.id, "quantity": 1}])
        with self.assertRaises(OrderFlowError):
            self.order(items=[])
        self.assertFalse(TableVisit.objects.exists())

    def test_who_may_order(self):
        for number, role in enumerate((User.Role.OWNER, User.Role.MANAGER, User.Role.CASHIER)):
            person = staff(self.partner, f"kava_{role}", role, 7701100 + number)
            self.order(table=self.other_table, actor=person,
                       visit=TableVisit.objects.filter(table=self.other_table, status="active")
                       .values_list("id", flat=True).first())
        hookah_master = staff(self.partner, "kava_hookah", User.Role.HOOKAH_MASTER, 7701201)
        with self.assertRaisesMessage(OrderFlowError, "официант"):
            self.order(actor=hookah_master)
        self.waiter.is_active = False
        self.waiter.save()
        with self.assertRaises(OrderFlowError):
            self.order()

    def test_only_this_venue(self):
        other = venue("Other")
        foreign = Table.objects.create(partner=other, number=5)
        with self.assertRaises(OrderFlowError):
            self.order(table=foreign)
        foreign_waiter = staff(other, "other_waiter", User.Role.WAITER, 7701301)
        with self.assertRaises(OrderFlowError):
            self.order(actor=foreign_waiter)

    def test_the_module_is_checked(self):
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            module_table_orders_enabled=False
        )
        with self.assertRaisesMessage(OrderFlowError, "выключен"):
            self.order()

    def test_a_retry_with_the_same_key_makes_one_order(self):
        first = self.order(key="tbl-key-0001")
        again = self.order(key="tbl-key-0001", visit=first.table_session.visit_id)
        self.assertEqual(first.id, again.id)
        self.assertEqual(Order.objects.count(), 1)

    def test_needs_the_floor_plan_and_orders(self):
        from django.core.exceptions import ValidationError

        settings = PartnerBotSettings.objects.get(partner=self.partner)
        settings.module_floor_plan_enabled = False
        with self.assertRaises(ValidationError) as caught:
            settings.full_clean()
        self.assertIn("module_table_orders_enabled", str(caught.exception))


@skipUnless(connection.vendor == "postgresql", "PostgreSQL only")
class FreeTableRaceTests(TransactionTestCase):
    """Two people tap one free table at once: one company sits down, the
    other sees the table taken (the table row is locked first)."""

    def test_one_visit_for_two_taps(self):
        partner = venue("Race")
        table = Table.objects.create(partner=partner, number=1)
        latte = MenuItem.objects.get(partner=partner, name="Лате")
        people = [staff(partner, f"race_{n}", User.Role.WAITER, 7702000 + n) for n in (1, 2)]
        barrier = threading.Barrier(2)
        results = []

        def tap(person):
            try:
                barrier.wait()
                create_staff_table_order(
                    partner_id=partner.id, actor=person, table_id=table.id, visit_id=None,
                    customer_code="", items=[{"menu_item_id": latte.id, "quantity": 1}],
                )
                results.append("ok")
            except OrderFlowError:
                results.append("taken")
            finally:
                close_old_connections()

        threads = [threading.Thread(target=tap, args=(person,)) for person in people]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(results), ["ok", "taken"])
        self.assertEqual(TableVisit.objects.filter(table=table, status="active").count(), 1)
