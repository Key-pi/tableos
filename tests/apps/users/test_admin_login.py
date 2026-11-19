"""Into Admin by a one-time link from the bot (PQ-045, step 2; owner
2026-11-19): «🔐 Войти в Admin» in work mode gives a link for 15 minutes; the
owner and the staff the owner gave Admin enter without a password. The page
asks «Войти» first: a link preview opening the address does not spend it.
Only the hash of the link is kept; access is checked again when it is used."""

import threading
from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import SESSION_KEY
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.employees.models import EmployeeProfile
from apps.employees.network_staff import sync_network_staff
from apps.employees.selectors import get_staff_employee_by_telegram
from apps.partners import onboarding
from apps.partners.models import Network, Partner
from apps.partners.onboarding import create_business
from apps.users.admin_login import (
    AdminLoginError,
    admin_login_url,
    issue_admin_login_link,
    redeem_admin_login_link,
)
from apps.users.constants import AdminAccessPreset
from apps.users.models import AdminAccessProfile, AdminLoginLink, TelegramAccount, User
from core.admin_mixins import ADMIN_POINT_SESSION_KEY

TOKEN = "444444444:AAEkavakavakavakavakavakavakavaka"
OWNER_TG, MANAGER_TG, WAITER_TG = 7719001, 7719002, 7719003


def business(name="Kava", bot="kava_login_bot", owner_tg=OWNER_TG):
    with patch.object(onboarding, "fetch_bot_username", return_value=bot):
        created = create_business(name=name, bot_token=TOKEN, owner_telegram_id=owner_tg,
                                  owner_name="Оля")
    return created.partner, get_staff_employee_by_telegram(created.partner.id, owner_tg)


def staff(partner, username, role, telegram_id, *, admin=False):
    user = User.objects.create_user(username=username, password="x", partner=partner, role=role)
    employee = EmployeeProfile.objects.create(
        partner=partner, user=user,
        telegram_account=TelegramAccount.objects.create(telegram_id=telegram_id),
    )
    if admin:
        AdminAccessProfile.objects.create(user=user, partner=partner, can_access_admin=True,
                                          preset=AdminAccessPreset.MANAGER)
    return employee


@override_settings(PLATFORM_BOT_TOKEN="", PLATFORM_OWNER_TELEGRAM_ID=None,
                   WEBAPP_BASE_URL="https://shop.example.com")
class AdminLoginLinkTests(TestCase):
    def setUp(self):
        self.partner, self.owner = business()

    def link(self, employee=None):
        raw = issue_admin_login_link(partner_id=self.partner.id, employee=employee or self.owner)
        return raw, reverse("admin-enter", args=[raw])

    def test_the_owner_enters_admin_without_a_password(self):
        raw, path = self.link()
        self.assertEqual(admin_login_url(raw), f"https://shop.example.com{path}")
        page = self.client.get(path)
        self.assertContains(page, "Kava")
        self.assertContains(page, 'method="post"')
        self.assertNotIn(SESSION_KEY, self.client.session)
        entered = self.client.post(path)
        self.assertRedirects(entered, reverse("admin:index"), fetch_redirect_response=False)
        self.assertEqual(str(self.client.session[SESSION_KEY]), str(self.owner.user_id))
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)

    def test_looking_at_the_page_does_not_spend_the_link(self):
        # Telegram's link preview and the guest's browser may open it first.
        _raw, path = self.link()
        self.client.get(path)
        self.client.get(path)
        self.assertEqual(self.client.post(path).status_code, 302)

    def test_a_link_works_once(self):
        _raw, path = self.link()
        self.client.post(path)
        self.client.logout()
        again = self.client.post(path)
        self.assertEqual(again.status_code, 410)
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertEqual(self.client.get(path).status_code, 410)

    def test_a_link_lives_fifteen_minutes(self):
        raw, path = self.link()
        later = timezone.now() + timedelta(minutes=15, seconds=1)
        with patch("apps.users.admin_login.timezone.now", return_value=later):
            with self.assertRaises(AdminLoginError):
                redeem_admin_login_link(raw)
        self.assertEqual(self.client.post(path).status_code, 302)  # within the time

    def test_only_the_hash_is_kept(self):
        raw, _path = self.link()
        link = AdminLoginLink.objects.get()
        self.assertNotEqual(link.token_hash, raw)
        self.assertNotIn(raw, link.token_hash)
        self.assertEqual((link.user_id, link.partner_id), (self.owner.user_id, self.partner.id))

    def test_staff_without_admin_get_no_link_and_those_given_admin_do(self):
        waiter = staff(self.partner, "kava_waiter", User.Role.WAITER, WAITER_TG)
        with self.assertRaisesMessage(AdminLoginError, "Вход в Admin"):
            self.link(waiter)
        manager = staff(self.partner, "kava_manager", User.Role.MANAGER, MANAGER_TG, admin=True)
        _raw, path = self.link(manager)
        self.client.post(path)
        self.assertEqual(str(self.client.session[SESSION_KEY]), str(manager.user_id))

    def test_access_taken_away_after_the_link_stops_it(self):
        manager = staff(self.partner, "kava_manager", User.Role.MANAGER, MANAGER_TG, admin=True)
        _raw, path = self.link(manager)
        access = AdminAccessProfile.objects.get(user=manager.user)
        access.can_access_admin = False
        access.save()
        self.assertEqual(self.client.post(path).status_code, 410)
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_a_link_is_for_the_venue_of_the_bot(self):
        other, _owner = business("Other", "other_login_bot", 7719009)
        with self.assertRaises(AdminLoginError):
            issue_admin_login_link(partner_id=other.id, employee=self.owner)

    def test_a_wrong_link_is_refused(self):
        self.assertEqual(self.client.post(reverse("admin-enter", args=["nope"])).status_code, 410)


@override_settings(PLATFORM_BOT_TOKEN="", PLATFORM_OWNER_TELEGRAM_ID=None)
class NetworkOwnerLinkTests(TestCase):
    def test_the_networks_owner_enters_as_themselves_at_that_venue(self):
        # In another venue's bot the owner works through a copy of their
        # profile (PQ-047); Admin is their own login, opened at that venue.
        network = Network.objects.create(name="Ніч")
        home, owner = business("Ніч Київ", "nich_kyiv_bot")
        other, _ = business("Ніч Харків", "nich_kharkiv_bot", 7719008)
        Partner.objects.filter(id__in=[home.id, other.id]).update(network=network)
        sync_network_staff(network.id)
        copy = get_staff_employee_by_telegram(other.id, OWNER_TG)
        self.assertEqual(copy.mirrored_from_id, owner.id)
        raw = issue_admin_login_link(partner_id=other.id, employee=copy)
        self.client.post(reverse("admin-enter", args=[raw]))
        self.assertEqual(str(self.client.session[SESSION_KEY]), str(owner.user_id))
        self.assertEqual(self.client.session[ADMIN_POINT_SESSION_KEY], str(other.id))


@skipUnless(connection.vendor == "postgresql", "нужны построчные блокировки")
@override_settings(PLATFORM_BOT_TOKEN="", PLATFORM_OWNER_TELEGRAM_ID=None)
class OneLinkTwiceAtOnceTests(TransactionTestCase):
    def test_two_presses_at_once_enter_once(self):
        _partner, owner = business()
        raw = issue_admin_login_link(partner_id=owner.partner_id, employee=owner)
        barrier, results = threading.Barrier(2), []

        def press():
            barrier.wait()
            try:
                results.append(redeem_admin_login_link(raw).user_id)
            except AdminLoginError:
                results.append(None)
            finally:
                close_old_connections()

        threads = [threading.Thread(target=press) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(results, key=str), sorted([owner.user_id, None], key=str))
