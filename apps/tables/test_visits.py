from decimal import Decimal

from django.test import TestCase

from apps.billing.models import Payment
from apps.billing.services import create_bill_from_orders, record_payment
from apps.employees.models import EmployeeProfile
from apps.menu.models import MenuCategory, MenuItem
from apps.notifications.models import StaffNotification
from apps.orders.services import create_order_from_session
from apps.partners.models import Partner
from apps.tables.models import Table, TableSession, TableVisit
from apps.tables.services import (
    VISIT_CHOICE_ASK,
    VISIT_CHOICE_JOIN_PREFIX,
    VISIT_CHOICE_NEW,
    TableSessionError,
    VisitChoiceRequired,
    activate_table_session,
    close_table_visit,
)
from apps.users.models import TelegramAccount, User


class TableVisitActivationTests(TestCase):
    """ADR-0007 / PQ-026: join-or-new choice when a party already sits there."""

    def setUp(self):
        self.partner = Partner.objects.create(
            name="Visit Venue", slug="visit-venue", status=Partner.Status.ACTIVE
        )
        self.table = Table.objects.create(partner=self.partner, number=5, qr_token="visit05")
        self.other_table = Table.objects.create(
            partner=self.partner, number=6, qr_token="visit06"
        )
        category = MenuCategory.objects.create(partner=self.partner, name="Bar")
        self.menu_item = MenuItem.objects.create(
            partner=self.partner, category=category, name="Tea", price="100.00"
        )
        waiter = User.objects.create_user(
            username="visit_waiter",
            password="pass12345",
            partner=self.partner,
            role=User.Role.WAITER,
        )
        EmployeeProfile.objects.create(
            partner=self.partner,
            user=waiter,
            telegram_account=TelegramAccount.objects.create(telegram_id=770999),
            notify_on_billing_requests=True,
        )

    def _scan(self, telegram_id, *, table=None, choice=None):
        kwargs = {"visit_choice": choice} if choice else {}
        return activate_table_session(
            partner_id=self.partner.id,
            telegram_id=telegram_id,
            payload=(table or self.table).deep_link_payload,
            **kwargs,
        )

    def _order(self, session):
        return create_order_from_session(
            partner_id=self.partner.id,
            table_session=session,
            items=[
                {
                    "menu_item_id": self.menu_item.id,
                    "item_name": "Tea",
                    "unit_price": self.menu_item.price,
                    "quantity": 1,
                }
            ],
        )

    def test_first_guest_starts_a_visit_without_a_question(self):
        session = self._scan(770001, choice=VISIT_CHOICE_ASK)

        self.assertIsNotNone(session.visit_id)
        self.assertEqual(session.visit.status, TableVisit.Status.ACTIVE)
        self.assertEqual(session.visit.table_id, self.table.id)

    def test_second_guest_is_asked_and_can_join(self):
        first = self._scan(770001, choice=VISIT_CHOICE_ASK)

        with self.assertRaises(VisitChoiceRequired) as raised:
            self._scan(770002, choice=VISIT_CHOICE_ASK)
        options = raised.exception.options
        self.assertEqual([option.visit_id for option in options], [str(first.visit_id)])
        self.assertEqual(options[0].guest_count, 1)
        self.assertFalse(
            TableSession.objects.filter(guest__telegram_account__telegram_id=770002).exists()
        )

        second = self._scan(770002, choice=f"{VISIT_CHOICE_JOIN_PREFIX}{first.visit_id}")
        self.assertEqual(second.visit_id, first.visit_id)

    def test_new_visit_keeps_the_previous_one_open_and_notifies_staff(self):
        first = self._scan(770001)

        second = self._scan(770002, choice=VISIT_CHOICE_NEW)

        self.assertNotEqual(second.visit_id, first.visit_id)
        first.visit.refresh_from_db()
        self.assertEqual(first.visit.status, TableVisit.Status.ACTIVE)
        self.assertTrue(
            StaffNotification.objects.filter(
                partner=self.partner,
                category=StaffNotification.Category.TABLE_VISIT,
            ).exists()
        )

    def test_member_rescans_into_the_same_visit_without_a_question(self):
        first = self._scan(770001)
        self._scan(770002)

        again = self._scan(770001, choice=VISIT_CHOICE_ASK)

        self.assertEqual(again.visit_id, first.visit_id)

    def test_joining_a_closed_visit_is_rejected(self):
        first = self._scan(770001)
        close_table_visit(first.visit)

        with self.assertRaises(TableSessionError):
            self._scan(770002, choice=f"{VISIT_CHOICE_JOIN_PREFIX}{first.visit_id}")

    def test_guest_moving_tables_closes_an_empty_settled_visit(self):
        first = self._scan(770001)

        self._scan(770001, table=self.other_table)

        first.visit.refresh_from_db()
        self.assertEqual(first.visit.status, TableVisit.Status.CLOSED)

    def test_visit_with_unpaid_orders_stays_open_for_staff(self):
        first = self._scan(770001)
        self._order(first)

        self._scan(770001, table=self.other_table)
        first.visit.refresh_from_db()
        self.assertEqual(first.visit.status, TableVisit.Status.ACTIVE)

        # Nobody sits there any more, so a new guest is not asked to join it.
        newcomer = self._scan(770003, choice=VISIT_CHOICE_ASK)
        self.assertNotEqual(newcomer.visit_id, first.visit_id)
        self.assertTrue(
            StaffNotification.objects.filter(
                category=StaffNotification.Category.TABLE_VISIT
            ).exists()
        )
        with self.assertRaisesMessage(TableSessionError, "неоплаченные"):
            close_table_visit(first.visit)

    def test_paying_the_last_order_closes_the_visit(self):
        first = self._scan(770001)
        order = self._order(first)
        bill = create_bill_from_orders(partner_id=self.partner.id, order_ids=[str(order.id)])
        from apps.orders.services import confirm_order_received_by_guest

        confirm_order_received_by_guest(
            partner_id=self.partner.id,
            telegram_id=770001,
            order_public_id=order.public_id,
        )
        record_payment(bill=bill, amount=Decimal("100.00"), method=Payment.Method.CASH)

        first.refresh_from_db()
        first.visit.refresh_from_db()
        self.assertEqual(first.status, TableSession.Status.CLOSED)
        self.assertEqual(first.visit.status, TableVisit.Status.CLOSED)
