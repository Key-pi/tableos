"""Staff invite links in the bot (PQ-046): issue → press → confirm."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from asgiref.sync import async_to_sync

from apps.employees.models import EmployeeProfile, StaffInvite
from apps.employees.selectors import get_staff_employee_by_telegram
from apps.users.models import TelegramAccount, User
from bot.handlers import staff, staff_invites, start
from tests.bot.test_staff_flow import OWNER_TG, StaffFlowFixture, _buttons, _data

NEW_TG = 7730077


class InviteFlowTests(StaffFlowFixture):
    def setUp(self):
        super().setUp()
        self.bot.send_message = AsyncMock()

    def _press_start(self, payload, telegram_id=NEW_TG):
        message = SimpleNamespace(
            text=f"/start {payload}",
            from_user=SimpleNamespace(
                id=telegram_id, username="dima", first_name="Дмитро", last_name="",
                language_code="uk",
            ),
            answer=AsyncMock(),
        )
        async_to_sync(start.start_handler)(message, SimpleNamespace(args=payload), self.bot)
        return message.answer.call_args.args[0]

    def _issue(self, role="waiter"):
        self._press(staff_invites.invite_roles_handler, "staffinv")
        callback = self._press(staff_invites.invite_create_handler, f"staffinv:r:{role}")
        text, markup = self._screen(callback)
        return text, markup, StaffInvite.objects.get()

    def _sent_to(self, chat_id):
        calls = [c for c in self.bot.send_message.call_args_list if c.args[0] == chat_id]
        self.assertTrue(calls, f"nothing sent to {chat_id}")
        return calls[-1].args[1], calls[-1].kwargs.get("reply_markup")

    def test_the_owner_sees_the_invite_button_and_a_waiter_does_not(self):
        self.assertIn("staffinv", _data(staff._staff_home_keyboard(self.owner)))
        waiter = EmployeeProfile.objects.create(
            partner=self.partner,
            user=User.objects.create_user(
                username="flow_waiter", password="x", partner=self.partner, role=User.Role.WAITER
            ),
            telegram_account=TelegramAccount.objects.create(telegram_id=7730010),
        )
        self.assertNotIn("staffinv", _data(staff._staff_home_keyboard(waiter)))

    def test_issue_press_confirm_and_the_new_person_works(self):
        roles_callback = self._press(staff_invites.invite_roles_handler, "staffinv")
        _text, roles = self._screen(roles_callback)
        self.assertEqual(
            _data(roles),
            ["staffinv:r:manager", "staffinv:r:cashier", "staffinv:r:waiter",
             "staffinv:r:hookah_master", "staffhome"],
        )

        text, markup, invite = self._issue("cashier")
        link = f"https://t.me/flow_cafe_bot?start=inv_{invite.code}"
        self.assertIn(link, text)
        self.assertIn("24 часа", text)
        share = _buttons(markup)[0]
        self.assertTrue(share.url.startswith("https://t.me/share/url?url="))

        answer = self._press_start(f"inv_{invite.code}")
        self.assertIn("Заявка отправлена", answer)
        prompt, buttons = self._sent_to(OWNER_TG)
        self.assertIn("Дмитро @dima", prompt)
        self.assertEqual(
            _data(buttons), [f"staffinv:y:{invite.id.hex}", f"staffinv:n:{invite.id.hex}"]
        )
        for data in _data(buttons):
            self.assertLessEqual(len(data.encode()), 64)

        decided = self._press(staff_invites.invite_decision_handler, f"staffinv:y:{invite.id.hex}")
        self.assertIn("теперь в команде", decided.message.edit_text.call_args.args[0])
        welcome, keyboard = self._sent_to(NEW_TG)
        self.assertIn("Готово", welcome)
        labels = [button.text for row in keyboard.keyboard for button in row]
        # Their language (Telegram: uk), not the language of whoever confirmed.
        self.assertIn("🧑‍🍳 Робочий режим", labels)
        employee = get_staff_employee_by_telegram(self.partner.id, NEW_TG)
        self.assertEqual(employee.user.role, User.Role.CASHIER)

    def test_a_rejected_person_is_told_and_stays_a_guest(self):
        _text, _markup, invite = self._issue()
        self._press_start(f"inv_{invite.code}")
        self._press(staff_invites.invite_decision_handler, f"staffinv:n:{invite.id.hex}")
        told, _keyboard = self._sent_to(NEW_TG)
        self.assertIn("відхилили", told)
        with self.assertRaises(EmployeeProfile.DoesNotExist):
            get_staff_employee_by_telegram(self.partner.id, NEW_TG)

    def test_a_used_link_is_refused_and_pressing_again_does_not_ask_twice(self):
        _text, _markup, invite = self._issue()
        self._press_start(f"inv_{invite.code}")
        self._press_start(f"inv_{invite.code}")
        self.assertEqual(
            [c.args[0] for c in self.bot.send_message.call_args_list].count(OWNER_TG), 1
        )
        self.assertIn("уже использована", self._press_start(f"inv_{invite.code}", 7730099))

    def test_a_guest_cannot_decide(self):
        _text, _markup, invite = self._issue()
        self._press_start(f"inv_{invite.code}")
        callback = SimpleNamespace(
            data=f"staffinv:y:{invite.id.hex}",
            from_user=SimpleNamespace(id=NEW_TG, language_code=""),
            answer=AsyncMock(),
            message=SimpleNamespace(edit_text=AsyncMock()),
        )
        async_to_sync(staff_invites.invite_decision_handler)(callback, self.bot)
        self.assertTrue(callback.answer.call_args.kwargs.get("show_alert"))
        invite.refresh_from_db()
        self.assertEqual(invite.status, StaffInvite.Status.CLAIMED)
