"""«🔐 Войти в Admin» in work mode (PQ-045, step 2; owner 2026-11-19): the
owner and staff given Admin see it; it sends a one-time link for 15 minutes
without a link preview."""

from django.test import override_settings

from apps.employees.models import EmployeeProfile
from apps.users.constants import AdminAccessPreset
from apps.users.models import AdminAccessProfile, AdminLoginLink, TelegramAccount, User
from bot.handlers import staff, staff_admin_login
from tests.bot.test_staff_flow import StaffFlowFixture, _buttons, _data

WAITER_TG = 7731010


class AdminLoginButtonTests(StaffFlowFixture):
    def setUp(self):
        super().setUp()
        AdminAccessProfile.objects.create(
            user=self.owner_user, partner=self.partner, preset=AdminAccessPreset.OWNER,
            is_partner_owner=True, can_access_admin=True,
        )

    def _waiter(self):
        return EmployeeProfile.objects.create(
            partner=self.partner,
            user=User.objects.create_user(username="flow_waiter", password="x",
                                          partner=self.partner, role=User.Role.WAITER),
            telegram_account=TelegramAccount.objects.create(telegram_id=WAITER_TG),
        )

    def test_the_home_shows_the_button_to_who_may_enter(self):
        callback = self._press(staff.staff_home_callback_handler, "staffhome")
        _text, markup = self._screen(callback)
        self.assertIn(staff_admin_login.ADMIN_LOGIN_CALLBACK, _data(markup))
        self.assertNotIn(staff_admin_login.ADMIN_LOGIN_CALLBACK,
                         _data(staff._staff_home_keyboard(self._waiter())))

    @override_settings(WEBAPP_BASE_URL="https://shop.example.com")
    def test_the_button_sends_a_link_without_a_preview(self):
        callback = self._press(staff_admin_login.admin_login_handler,
                               staff_admin_login.ADMIN_LOGIN_CALLBACK)
        call = callback.message.answer.call_args
        self.assertIn("15", call.args[0])
        self.assertTrue(call.kwargs["link_preview_options"].is_disabled)
        button = _buttons(call.kwargs["reply_markup"])[0]
        self.assertTrue(button.url.startswith("https://shop.example.com/admin/enter/"))
        self.assertEqual(AdminLoginLink.objects.get().user_id, self.owner_user.id)
        self.assertNotIn(button.url.rsplit("/", 2)[-2], AdminLoginLink.objects.get().token_hash)

    @override_settings(WEBAPP_BASE_URL="", CSRF_TRUSTED_ORIGINS=["http://localhost:8000"])
    def test_without_https_the_link_is_in_the_text(self):
        # Telegram takes only public addresses in buttons: locally — a text link.
        callback = self._press(staff_admin_login.admin_login_handler,
                               staff_admin_login.ADMIN_LOGIN_CALLBACK)
        call = callback.message.answer.call_args
        self.assertIn("http://localhost:8000/admin/enter/", call.args[0])
        self.assertIsNone(call.kwargs.get("reply_markup"))

    def test_staff_without_admin_are_told_why(self):
        AdminAccessProfile.objects.filter(user=self.owner_user).update(can_access_admin=False)
        callback = self._press(staff_admin_login.admin_login_handler,
                               staff_admin_login.ADMIN_LOGIN_CALLBACK)
        self.assertTrue(callback.answer.call_args.kwargs.get("show_alert"))
        callback.message.answer.assert_not_called()
        self.assertFalse(AdminLoginLink.objects.exists())
