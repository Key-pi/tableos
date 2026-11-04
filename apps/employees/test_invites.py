"""Staff invite links (PQ-046): the owner or a manager issues a one-time link
for a role, the person presses Start, the issuer confirms."""

import threading
import unittest
from datetime import timedelta

from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from apps.employees.invites import (
    StaffInviteError,
    claim_staff_invite,
    create_staff_invite,
    decide_staff_invite,
    invite_link,
    parse_invite_payload,
    revoke_staff_invite,
    roles_invitable_by,
)
from apps.employees.models import EmployeeProfile, StaffInvite
from apps.employees.selectors import get_staff_employee_by_telegram
from apps.partners.models import BotInstance, Partner
from apps.users.constants import AdminAccessPreset
from apps.users.models import AdminAccessProfile, TelegramAccount, User

Role = User.Role
OWNER_TG, MANAGER_TG, WAITER_TG, NEW_TG, OTHER_TG = 8810001, 8810002, 8810003, 8810004, 8810005


def make_staff(partner, username, role, telegram_id):
    user = User.objects.create_user(
        username=username, password="x", partner=partner, role=role, first_name=username
    )
    account, _created = TelegramAccount.objects.get_or_create(telegram_id=telegram_id)
    EmployeeProfile.objects.create(partner=partner, user=user, telegram_account=account)
    return user


class InviteFixture:
    def make_partner(self):
        self.partner = Partner.objects.create(
            name="Invite Cafe", slug="invite-cafe", status=Partner.Status.ACTIVE
        )
        self.owner = make_staff(self.partner, "inv_owner", Role.OWNER, OWNER_TG)
        self.manager = make_staff(self.partner, "inv_manager", Role.MANAGER, MANAGER_TG)
        self.waiter = make_staff(self.partner, "inv_waiter", Role.WAITER, WAITER_TG)
        TelegramAccount.objects.create(telegram_id=NEW_TG, first_name="Дмитро", username="dima")
        TelegramAccount.objects.create(telegram_id=OTHER_TG, first_name="Чужой")

    def invite(self, actor=None, role=Role.WAITER):
        return create_staff_invite(
            partner_id=self.partner.id, actor=actor or self.owner, role=role
        )

    def claim(self, invite, telegram_id=NEW_TG):
        return claim_staff_invite(
            partner_id=self.partner.id, code=invite.code, telegram_id=telegram_id
        )

    def decide(self, invite, actor=None, accept=True):
        return decide_staff_invite(
            partner_id=self.partner.id, invite_id=invite.id, actor=actor or self.owner,
            accept=accept,
        )


class IssuingTests(InviteFixture, TestCase):
    def setUp(self):
        self.make_partner()

    def test_owner_and_manager_invite_any_staff_role_but_never_an_owner(self):
        staff_roles = [Role.MANAGER, Role.CASHIER, Role.WAITER, Role.HOOKAH_MASTER]
        self.assertEqual(roles_invitable_by(self.owner, self.partner.id), staff_roles)
        # Not above their own rights (PQ-035): a manager may add a manager.
        self.assertEqual(roles_invitable_by(self.manager, self.partner.id), staff_roles)
        self.assertEqual(roles_invitable_by(self.waiter, self.partner.id), [])
        for role in staff_roles:
            self.assertEqual(self.invite(self.manager, role).role, role)
        with self.assertRaises(StaffInviteError):
            self.invite(role=Role.OWNER)
        with self.assertRaises(StaffInviteError):
            self.invite(self.waiter)

    def test_staff_of_another_venue_cannot_invite_here(self):
        other = Partner.objects.create(name="Other", slug="other", status=Partner.Status.ACTIVE)
        stranger = make_staff(other, "other_owner", Role.OWNER, OTHER_TG)
        with self.assertRaises(StaffInviteError):
            self.invite(stranger)
        self.assertEqual(roles_invitable_by(stranger, self.partner.id), [])

    def test_a_link_lives_a_day_and_its_code_is_secret_and_short(self):
        first, second = self.invite(), self.invite()
        self.assertNotEqual(first.code, second.code)
        self.assertGreaterEqual(len(first.code), 20)
        self.assertAlmostEqual(
            first.expires_at, timezone.now() + timedelta(hours=24), delta=timedelta(minutes=1)
        )
        link = invite_link(first.code, "invite_cafe_bot")
        self.assertEqual(link, f"https://t.me/invite_cafe_bot?start=inv_{first.code}")
        payload = link.split("start=")[1]
        self.assertLessEqual(len(payload), 64)  # Telegram's limit for a start parameter
        self.assertEqual(parse_invite_payload(payload), first.code)
        self.assertIsNone(parse_invite_payload("src_abc"))
        self.assertIsNone(parse_invite_payload("table-token"))


class ClaimTests(InviteFixture, TestCase):
    def setUp(self):
        self.make_partner()

    def test_pressing_the_link_waits_for_the_issuer(self):
        invite = self.invite(self.manager)
        result = self.claim(invite)
        invite.refresh_from_db()
        self.assertEqual(invite.status, StaffInvite.Status.CLAIMED)
        self.assertEqual(invite.claimed_by.telegram_id, NEW_TG)
        self.assertEqual(result.issuer_telegram_id, MANAGER_TG)
        self.assertFalse(result.already_claimed)
        # Not staff yet.
        with self.assertRaises(EmployeeProfile.DoesNotExist):
            get_staff_employee_by_telegram(self.partner.id, NEW_TG)

    def test_the_link_is_one_time(self):
        invite = self.invite()
        self.claim(invite)
        self.assertTrue(self.claim(invite).already_claimed)  # the same person again
        with self.assertRaisesMessage(StaffInviteError, "уже использована"):
            self.claim(invite, OTHER_TG)

    def test_an_old_cancelled_or_foreign_link_does_not_work(self):
        old = self.invite()
        StaffInvite.objects.filter(id=old.id).update(expires_at=timezone.now())
        with self.assertRaisesMessage(StaffInviteError, "устарела"):
            self.claim(old)
        revoked = self.invite()
        revoke_staff_invite(partner_id=self.partner.id, invite_id=revoked.id, actor=self.owner)
        with self.assertRaisesMessage(StaffInviteError, "отменено"):
            self.claim(revoked)
        other = Partner.objects.create(name="Other", slug="other", status=Partner.Status.ACTIVE)
        with self.assertRaisesMessage(StaffInviteError, "не найдена"):
            claim_staff_invite(partner_id=other.id, code=self.invite().code, telegram_id=NEW_TG)

    def test_a_current_employee_does_not_use_up_the_link(self):
        invite = self.invite()
        with self.assertRaisesMessage(StaffInviteError, "уже сотрудник"):
            self.claim(invite, WAITER_TG)
        invite.refresh_from_db()
        self.assertEqual(invite.status, StaffInvite.Status.PENDING)


class DecisionTests(InviteFixture, TestCase):
    def setUp(self):
        self.make_partner()

    def test_confirmed_person_works_in_the_bot_with_the_role_of_the_invite(self):
        invite = self.invite(self.manager, Role.CASHIER)
        self.claim(invite)
        self.decide(invite, self.manager)
        employee = get_staff_employee_by_telegram(self.partner.id, NEW_TG)
        self.assertEqual(employee.user.role, Role.CASHIER)
        self.assertEqual(employee.user.partner_id, self.partner.id)
        self.assertEqual(employee.user.first_name, "Дмитро")
        self.assertFalse(employee.user.has_usable_password())
        self.assertFalse(employee.user.is_staff)  # Admin access is the owner's call
        invite.refresh_from_db()
        self.assertEqual(invite.status, StaffInvite.Status.ACCEPTED)
        self.assertEqual(invite.employee, employee)
        self.assertEqual(invite.decided_by, self.manager)

    def test_only_the_issuer_or_the_owner_decides(self):
        invite = self.invite(self.manager)
        self.claim(invite)
        other_manager = make_staff(self.partner, "inv_manager2", Role.MANAGER, 8810009)
        with self.assertRaises(StaffInviteError):
            self.decide(invite, other_manager)
        self.decide(invite, self.owner)
        self.assertEqual(
            get_staff_employee_by_telegram(self.partner.id, NEW_TG).user.role, Role.WAITER
        )

    def test_an_issuer_who_lost_the_right_cannot_confirm(self):
        invite = self.invite(self.manager, Role.MANAGER)
        self.claim(invite)
        User.objects.filter(id=self.manager.id).update(role=Role.WAITER)
        self.manager.refresh_from_db()
        with self.assertRaises(StaffInviteError):
            self.decide(invite, self.manager)
        EmployeeProfile.objects.filter(user=self.owner).update(is_active=False)
        User.objects.filter(id=self.owner.id).update(is_active=False)
        self.owner.refresh_from_db()
        with self.assertRaises(StaffInviteError):
            self.decide(invite, self.owner)

    def test_rejected_person_is_not_staff_and_the_link_is_spent(self):
        invite = self.invite()
        self.claim(invite)
        self.decide(invite, accept=False)
        invite.refresh_from_db()
        self.assertEqual(invite.status, StaffInvite.Status.REJECTED)
        with self.assertRaises(EmployeeProfile.DoesNotExist):
            get_staff_employee_by_telegram(self.partner.id, NEW_TG)
        with self.assertRaises(StaffInviteError):
            self.claim(invite)

    def test_a_second_decision_changes_nothing(self):
        invite = self.invite()
        self.claim(invite)
        self.decide(invite)
        with self.assertRaisesMessage(StaffInviteError, "уже"):
            self.decide(invite, accept=False)
        self.assertEqual(
            EmployeeProfile.objects.filter(telegram_account__telegram_id=NEW_TG).count(), 1
        )

    def test_a_link_nobody_pressed_cannot_be_confirmed(self):
        with self.assertRaises(StaffInviteError):
            self.decide(self.invite())

    def test_a_former_employee_comes_back_instead_of_a_duplicate(self):
        EmployeeProfile.objects.filter(user=self.waiter).update(is_active=False)
        invite = self.invite(role=Role.CASHIER)
        self.claim(invite, WAITER_TG)
        self.decide(invite)
        employee = get_staff_employee_by_telegram(self.partner.id, WAITER_TG)
        self.assertEqual(employee.user, self.waiter)
        self.assertEqual(employee.user.role, Role.CASHIER)
        self.assertEqual(EmployeeProfile.objects.filter(user=self.waiter).count(), 1)


@unittest.skipUnless(connection.vendor == "postgresql", "row locks need PostgreSQL")
class ClaimRaceTests(InviteFixture, TransactionTestCase):
    def setUp(self):
        self.make_partner()

    def test_two_people_pressing_one_link_at_once_only_one_gets_it(self):
        invite = self.invite()
        barrier = threading.Barrier(2)
        outcomes = []

        def press(telegram_id):
            try:
                barrier.wait()
                self.claim(invite, telegram_id)
                outcomes.append("claimed")
            except StaffInviteError:
                outcomes.append("refused")
            finally:
                close_old_connections()

        threads = [threading.Thread(target=press, args=(tg,)) for tg in (NEW_TG, OTHER_TG)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(outcomes), ["claimed", "refused"])

    def test_owner_and_issuer_confirming_at_once_add_one_employee(self):
        invite = self.invite(self.manager)
        self.claim(invite)
        barrier = threading.Barrier(2)
        outcomes = []

        def confirm(actor):
            try:
                barrier.wait()
                self.decide(invite, actor)
                outcomes.append("added")
            except StaffInviteError:
                outcomes.append("refused")
            finally:
                close_old_connections()

        threads = [threading.Thread(target=confirm, args=(a,)) for a in (self.owner, self.manager)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(outcomes), ["added", "refused"])
        self.assertEqual(
            EmployeeProfile.objects.filter(telegram_account__telegram_id=NEW_TG).count(), 1
        )


class InviteAdminTests(InviteFixture, TestCase):
    """The owner at a computer: Admin → Приглашения сотрудников."""

    def setUp(self):
        self.make_partner()
        BotInstance.objects.create(
            partner=self.partner, display_name="Inv", username="invite_cafe_bot",
            token_encrypted="", is_active=True,
        )
        User.objects.filter(id=self.owner.id).update(is_staff=True)
        AdminAccessProfile.objects.create(
            user=self.owner, partner=self.partner, preset=AdminAccessPreset.OWNER,
            is_partner_owner=True, can_access_admin=True,
        )
        self.client.force_login(self.owner)

    def test_owner_issues_a_link_and_confirms_the_person(self):
        add = reverse("admin:employees_staffinvite_add")
        page = self.client.get(add).content.decode()
        self.assertIn('value="waiter"', page)
        self.assertNotIn('value="owner"', page)
        response = self.client.post(add, {"role": "waiter"}, follow=True)
        self.assertEqual(response.status_code, 200)
        invite = StaffInvite.objects.get()
        self.assertEqual((invite.partner, invite.created_by), (self.partner, self.owner))
        change = self.client.get(reverse("admin:employees_staffinvite_change", args=[invite.id]))
        self.assertIn(
            f"https://t.me/invite_cafe_bot?start=inv_{invite.code}", change.content.decode()
        )

        self.claim(invite)
        self.client.post(
            reverse("admin:employees_staffinvite_changelist"),
            {"action": "confirm_invites", "_selected_action": [str(invite.id)]},
        )
        self.assertEqual(
            get_staff_employee_by_telegram(self.partner.id, NEW_TG).user.role, Role.WAITER
        )

    def test_another_venue_does_not_see_the_links(self):
        other = Partner.objects.create(name="Other", slug="other", status=Partner.Status.ACTIVE)
        create_staff_invite(
            partner_id=other.id, actor=make_staff(other, "o_owner", Role.OWNER, OTHER_TG),
            role=Role.WAITER,
        )
        response = self.client.get(reverse("admin:employees_staffinvite_changelist"))
        self.assertEqual(response.context["cl"].result_count, 0)
