from django.test import TestCase

from apps.partners.models import Partner
from apps.users.services import get_or_create_guest_profile


class GuestProfileServiceTests(TestCase):
    def test_get_or_create_guest_profile_prefetches_telegram_account(self):
        partner = Partner.objects.create(
            name="Profile Venue",
            slug="profile-venue",
            status=Partner.Status.ACTIVE,
        )

        guest_profile = get_or_create_guest_profile(
            partner_id=partner.id,
            telegram_id=99887766,
            username="profile_guest",
            first_name="Profile",
        )

        self.assertEqual(guest_profile.telegram_account.username, "profile_guest")
        self.assertIn("telegram_account", guest_profile._state.fields_cache)
