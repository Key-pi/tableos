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


class VisitFixtureMixin:
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



class TableVisitActivationTests(VisitFixtureMixin, TestCase):
    """ADR-0007 / PQ-026: join-or-new choice when a party already sits there."""

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


class VisitScopedBillingTests(VisitFixtureMixin, TestCase):
    """AA-020: orders of one visit never enter another visit's bill."""

    def setUp(self):
        super().setUp()
        self.old_party = self._scan(771001)
        self.old_order = self._order(self.old_party)
        # The old party leaves without paying; a new party sits down.
        self.new_party = self._scan(771002, choice=VISIT_CHOICE_NEW)
        self.new_order = self._order(self.new_party)

    def test_shared_bill_request_takes_only_the_new_party_orders(self):
        from apps.billing.models import BillingRequest
        from apps.billing.table_bills import create_billing_request_for_telegram_user

        result = create_billing_request_for_telegram_user(
            partner_id=self.partner.id,
            telegram_id=771002,
            request_type=BillingRequest.RequestType.SHARED,
        )

        billed_orders = set(result.bill.bill_orders.values_list("order_id", flat=True))
        self.assertEqual(billed_orders, {self.new_order.id})
        self.assertEqual(result.bill.visit_id, self.new_party.visit_id)

    def test_orders_of_two_visits_cannot_share_a_bill(self):
        from apps.billing.services import BillingServiceError

        with self.assertRaises(BillingServiceError):
            create_bill_from_orders(
                partner_id=self.partner.id,
                order_ids=[str(self.old_order.id), str(self.new_order.id)],
            )

    def test_staff_table_bill_needs_a_visit_when_several_are_open(self):
        from apps.billing.services import BillingServiceError
        from apps.billing.table_bills import create_shared_bill_for_table

        with self.assertRaisesMessage(BillingServiceError, "визит"):
            create_shared_bill_for_table(partner_id=self.partner.id, table_id=self.table.id)

        bill = create_shared_bill_for_table(
            partner_id=self.partner.id,
            table_id=self.table.id,
            visit_id=self.old_party.visit_id,
        )
        self.assertEqual(
            set(bill.bill_orders.values_list("order_id", flat=True)),
            {self.old_order.id},
        )

    def test_request_bill_button_follows_the_guest_visit(self):
        from bot.services.content import BotContent
        from bot.services.navigation import GuestAction, resolve_guest_navigation_state

        content = BotContent.defaults()
        content.module_billing_enabled = True
        newcomer = self._scan(771003, choice=VISIT_CHOICE_NEW)

        state = resolve_guest_navigation_state(
            partner_id=self.partner.id,
            telegram_id=771003,
            content=content,
        )

        self.assertIsNotNone(newcomer.visit_id)
        self.assertFalse(state.has_action(GuestAction.REQUEST_BILL))
