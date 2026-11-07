"""The network's owner works in the bot in every venue of the network, a
manager in the venues given to them (PQ-047, owner's decision 2026-11-05)."""

from django.test import TestCase

from apps.employees.models import EmployeeProfile
from apps.employees.network_staff import sync_network_staff
from apps.employees.selectors import get_staff_employee_by_telegram
from apps.partners.models import Network, Partner
from apps.users.constants import AdminAccessPreset
from apps.users.models import AdminAccessProfile, TelegramAccount, User

OWNER_TG, MANAGER_TG, WAITER_TG = 9910001, 9910002, 9910003


def staff_in(partner, telegram_id):
    try:
        return get_staff_employee_by_telegram(partner.id, telegram_id)
    except EmployeeProfile.DoesNotExist:
        return None


class NetworkStaffTests(TestCase):
    def setUp(self):
        self.network = Network.objects.create(name="Ночь")
        self.kyiv = self._venue("Ночь Київ")
        self.kharkiv = self._venue("Ночь Харків")
        self.odesa = self._venue("Ночь Одеса")
        self.owner = self._person("noch_owner", self.kyiv, User.Role.OWNER, OWNER_TG)
        self.manager = self._person("noch_manager", self.kyiv, User.Role.MANAGER, MANAGER_TG)

    def _venue(self, name, network="default"):
        return Partner.objects.create(
            name=name, slug=name.lower().replace(" ", "-"), status=Partner.Status.ACTIVE,
            network=self.network if network == "default" else network,
        )

    def _person(self, username, partner, role, telegram_id, *, admin=True):
        user = User.objects.create_user(
            username=username, password="x", partner=partner, role=role, first_name=username
        )
        EmployeeProfile.objects.create(
            partner=partner, user=user, can_adjust_bills=True, notify_on_guest_calls=False,
            telegram_account=TelegramAccount.objects.create(telegram_id=telegram_id),
        )
        if admin:
            AdminAccessProfile.objects.create(
                user=user, partner=partner, can_access_admin=True,
                is_partner_owner=role == User.Role.OWNER,
                preset=AdminAccessPreset.OWNER if role == User.Role.OWNER
                else AdminAccessPreset.MANAGER,
            )
        return user

    def test_the_owner_works_in_every_venue_with_their_settings(self):
        sync_network_staff(self.network.id)
        for venue in (self.kharkiv, self.odesa):
            employee = staff_in(venue, OWNER_TG)
            self.assertIsNotNone(employee, venue.name)
            self.assertEqual(employee.user.role, User.Role.OWNER)
            self.assertEqual(employee.mirrored_from.user, self.owner)
            # Their own notification choices and rights come along.
            self.assertFalse(employee.notify_on_guest_calls)
            self.assertTrue(employee.can_adjust_bills)
            # Admin stays with their own login.
            self.assertFalse(employee.user.is_staff)
            self.assertFalse(employee.user.has_usable_password())

    def test_a_manager_works_only_in_the_venues_given_to_them(self):
        profile = AdminAccessProfile.objects.get(user=self.manager)
        profile.extra_partners.add(self.kharkiv)  # the signal keeps it in step
        self.assertIsNotNone(staff_in(self.kharkiv, MANAGER_TG))
        self.assertIsNone(staff_in(self.odesa, MANAGER_TG))
        profile.extra_partners.remove(self.kharkiv)
        self.assertIsNone(staff_in(self.kharkiv, MANAGER_TG))

    def test_a_venue_joining_or_leaving_the_network_follows(self):
        lviv = self._venue("Ночь Львів", network=None)
        self.assertIsNone(staff_in(lviv, OWNER_TG))
        lviv.network = self.network
        lviv.save()
        self.assertIsNotNone(staff_in(lviv, OWNER_TG))
        lviv.network = None
        lviv.save()
        self.assertIsNone(staff_in(lviv, OWNER_TG))

    def test_a_switched_off_owner_is_switched_off_everywhere(self):
        sync_network_staff(self.network.id)
        AdminAccessProfile.objects.filter(user=self.owner).update(is_active=False)
        sync_network_staff(self.network.id)
        self.assertIsNone(staff_in(self.kharkiv, OWNER_TG))
        self.assertIsNotNone(staff_in(self.kyiv, OWNER_TG))  # their own profile stays

    def test_a_real_staff_profile_in_a_venue_is_left_alone(self):
        # The person already worked in Kharkiv before running the network.
        EmployeeProfile.objects.filter(partner=self.kharkiv, mirrored_from__isnull=False).delete()
        own = User.objects.create_user(
            username="owner_in_kharkiv", password="x", partner=self.kharkiv,
            role=User.Role.MANAGER,
        )
        real = EmployeeProfile.objects.create(
            partner=self.kharkiv, user=own,
            telegram_account=TelegramAccount.objects.get(telegram_id=OWNER_TG),
        )
        sync_network_staff(self.network.id)
        self.assertEqual(staff_in(self.kharkiv, OWNER_TG), real)
        self.assertIsNone(real.mirrored_from)

    def test_the_copies_follow_a_new_telegram_of_the_person(self):
        # The owner moved to another Telegram account in their own profile
        # (found by the demo seed, 2026-11-07): the bot of the other venues
        # must know them by the new one, not the old one.
        sync_network_staff(self.network.id)
        own = EmployeeProfile.objects.get(user=self.owner)
        own.telegram_account = TelegramAccount.objects.create(telegram_id=9910099)
        own.save()
        sync_network_staff(self.network.id)
        for venue in (self.kharkiv, self.odesa):
            self.assertIsNone(staff_in(venue, OWNER_TG), venue.name)
            self.assertEqual(staff_in(venue, 9910099).mirrored_from, own, venue.name)

    def test_a_new_telegram_that_has_a_real_profile_there_switches_the_copy_off(self):
        sync_network_staff(self.network.id)
        waiter_account = TelegramAccount.objects.create(telegram_id=WAITER_TG)
        waiter = EmployeeProfile.objects.create(
            partner=self.kharkiv, telegram_account=waiter_account,
            user=User.objects.create_user(
                username="kharkiv_waiter", password="x", partner=self.kharkiv,
                role=User.Role.WAITER,
            ),
        )
        own = EmployeeProfile.objects.get(user=self.owner)
        own.telegram_account = waiter_account
        own.save()
        sync_network_staff(self.network.id)
        self.assertEqual(staff_in(self.kharkiv, WAITER_TG), waiter)  # theirs, not touched
        old_copy = EmployeeProfile.objects.get(partner=self.kharkiv, mirrored_from=own)
        self.assertFalse(old_copy.is_active)
        self.assertEqual(staff_in(self.odesa, WAITER_TG).mirrored_from, own)

    def test_syncing_twice_changes_nothing(self):
        sync_network_staff(self.network.id)
        count = EmployeeProfile.objects.count()
        sync_network_staff(self.network.id)
        self.assertEqual(EmployeeProfile.objects.count(), count)

    def test_a_venue_outside_networks_gets_no_copies(self):
        alone = self._venue("Alone", network=None)
        self._person("alone_owner", alone, User.Role.OWNER, WAITER_TG)
        copies = EmployeeProfile.objects.filter(mirrored_from__isnull=False)
        self.assertFalse(copies.exclude(mirrored_from__partner__network=self.network).exists())
        self.assertFalse(copies.filter(partner=alone).exists())

    def test_the_owner_gets_work_mode_and_alerts_in_another_venue(self):
        from apps.notifications.models import StaffNotification
        from apps.notifications.services import notify_partner_owners
        from bot.services.content import BotContent
        from bot.services.navigation import GuestAction, resolve_guest_navigation_state

        state = resolve_guest_navigation_state(
            partner_id=self.kharkiv.id, telegram_id=OWNER_TG,
            content=BotContent.for_partner(self.kharkiv),
        )
        self.assertTrue(state.has_action(GuestAction.STAFF))
        sent = notify_partner_owners(
            partner_id=self.kharkiv.id, title=lambda: "Безопасность", message=lambda: "Харків",
        )
        self.assertEqual(sent, 1)
        self.assertTrue(
            StaffNotification.objects.filter(
                partner=self.kharkiv, employee__telegram_account__telegram_id=OWNER_TG
            ).exists()
        )
