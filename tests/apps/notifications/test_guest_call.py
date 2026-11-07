"""«🏃 Иду» on a staff call (PQ-044): the first employee takes the call, the
guest is told, the other employees see who went."""

import threading
from unittest import skipUnless
from unittest.mock import AsyncMock, patch

from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase

from apps.employees.models import EmployeeProfile
from apps.notifications.models import GuestCall, StaffNotification
from apps.notifications.services import (
    GuestCallError,
    deliver_staff_notification_now,
    request_staff_assistance,
    take_guest_call,
)
from apps.users.models import TelegramAccount, User
from tests.apps.notifications import test_notifications as notification_tests

SEND = "apps.notifications.services._send_telegram_messages"
EDIT = "apps.notifications.services.telegram_transport.edit_message_text"
GUEST_TG = 701001


def _sent(captured):
    async def send(_token, messages, **_kwargs):
        captured.extend(messages)
        return [(message[0], True, "", False, False, 500 + len(captured)) for message in messages]

    return send


class GuestCallFixture:
    def _setup_call(self):
        notification_tests.StaffNotificationCreationTests.setUp(self)
        self.waiters = []
        for number in range(2):
            user = User.objects.create_user(
                username=f"call_waiter_{number}",
                password="x",
                partner=self.partner,
                role=User.Role.WAITER,
                first_name=f"Офіціант{number}",
            )
            self.waiters.append(
                EmployeeProfile.objects.create(
                    partner=self.partner,
                    user=user,
                    telegram_account=TelegramAccount.objects.create(telegram_id=702000 + number),
                    bot_notifications_enabled=True,
                    notify_on_guest_calls=True,
                )
            )

    def _call(self, captured):
        with patch(SEND, new=_sent(captured)):
            request_staff_assistance(
                partner_id=self.partner.id,
                telegram_id=GUEST_TG,
                call_target="waiter",
                call_target_label="Официант",
            )
            for notification in StaffNotification.objects.filter(
                category=StaffNotification.Category.GUEST_CALL
            ):
                deliver_staff_notification_now(notification)
        return GuestCall.objects.get()

    def _notification_of(self, employee):
        return StaffNotification.objects.get(
            employee=employee, category=StaffNotification.Category.GUEST_CALL
        )


class TakeGuestCallTests(GuestCallFixture, TestCase):
    def setUp(self):
        self._setup_call()

    def test_the_call_message_has_the_button_and_remembers_the_message(self):
        captured = []
        call = self._call(captured)
        # Two waiters and the manager of the base fixture hear waiter calls.
        self.assertEqual(call.notifications.count(), 3)
        markups = [message[2] for message in captured]
        self.assertTrue(all(m.inline_keyboard[0][0].text == "🏃 Иду" for m in markups))
        for markup in markups:
            self.assertLessEqual(len(markup.inline_keyboard[0][0].callback_data.encode()), 64)
        self.assertTrue(all(n.telegram_message_id for n in call.notifications.all()))

    def test_the_first_takes_it_the_guest_is_told_and_others_see_who_went(self):
        self._call([])
        first, second = self.waiters
        captured, edit = [], AsyncMock()
        with patch(SEND, new=_sent(captured)), patch(EDIT, new=edit):
            with self.captureOnCommitCallbacks(execute=True):
                take_guest_call(
                    partner_id=self.partner.id,
                    notification_id=self._notification_of(first).id,
                    employee=first,
                )
        call = GuestCall.objects.get()
        self.assertEqual(call.taken_by, first)
        self.assertIsNotNone(call.guest_told_at)
        self.assertEqual([m[0] for m in captured], [GUEST_TG])
        self.assertIn("идёт к вам", captured[0][1])
        # Only the others' messages are edited; the call is read for all.
        edited = {c.kwargs["chat_id"] for c in edit.await_args_list}
        self.assertEqual(edited, {702001, self.employee.telegram_account.telegram_id})
        self.assertIn("Идёт: Офіціант0", edit.await_args.kwargs["text"])
        self.assertFalse(call.notifications.filter(is_read=False).exists())

        with self.assertRaisesMessage(GuestCallError, "Уже идёт: Офіціант0"):
            take_guest_call(
                partner_id=self.partner.id,
                notification_id=self._notification_of(second).id,
                employee=second,
            )

    def test_a_repeated_press_tells_the_guest_once(self):
        self._call([])
        first = self.waiters[0]
        captured = []
        with patch(SEND, new=_sent(captured)), patch(EDIT, new=AsyncMock()):
            for _press in range(2):
                with self.captureOnCommitCallbacks(execute=True):
                    take_guest_call(
                        partner_id=self.partner.id,
                        notification_id=self._notification_of(first).id,
                        employee=first,
                    )
        self.assertEqual(len(captured), 1)

    def test_only_an_employee_of_the_venue_with_that_notification_takes_it(self):
        self._call([])
        first, second = self.waiters
        with self.assertRaisesMessage(GuestCallError, "уже нельзя"):
            take_guest_call(
                partner_id=self.partner.id,
                notification_id=self._notification_of(first).id,
                employee=second,
            )
        EmployeeProfile.objects.filter(id=first.id).update(is_active=False)
        first.refresh_from_db()
        with self.assertRaises(GuestCallError):
            take_guest_call(
                partner_id=self.partner.id,
                notification_id=self._notification_of(first).id,
                employee=first,
            )
        self.assertIsNone(GuestCall.objects.get().taken_by_id)


@skipUnless(connection.vendor == "postgresql", "row locks need PostgreSQL")
class TakeGuestCallRaceTests(GuestCallFixture, TransactionTestCase):
    def setUp(self):
        self._setup_call()

    def test_two_presses_at_once_take_the_call_once(self):
        self._call([])
        notifications = {w.id: self._notification_of(w).id for w in self.waiters}
        barrier = threading.Barrier(2)
        outcomes = []

        def press(employee):
            try:
                barrier.wait()
                take_guest_call(
                    partner_id=self.partner.id,
                    notification_id=notifications[employee.id],
                    employee=EmployeeProfile.objects.select_related("user").get(id=employee.id),
                )
                outcomes.append("taken")
            except GuestCallError:
                outcomes.append("refused")
            finally:
                close_old_connections()

        threads = [threading.Thread(target=press, args=(w,)) for w in self.waiters]
        # Patched once for both threads: patching inside each is not thread-safe.
        with patch(SEND, new=_sent([])), patch(EDIT, new=AsyncMock()):
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(sorted(outcomes), ["refused", "taken"])
        self.assertIsNotNone(GuestCall.objects.get().taken_by_id)
