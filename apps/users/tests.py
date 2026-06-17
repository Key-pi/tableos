from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.partners.models import Partner
from apps.users.services import get_or_create_guest_profile

User = get_user_model()


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

    def test_get_or_create_guest_profile_syncs_blank_telegram_fields(self):
        partner = Partner.objects.create(
            name="Blank Sync Venue",
            slug="blank-sync-venue",
            status=Partner.Status.ACTIVE,
        )

        get_or_create_guest_profile(
            partner_id=partner.id,
            telegram_id=99887767,
            username="profile_guest",
            first_name="Profile",
            last_name="Guest",
            language_code="ru",
        )

        guest_profile = get_or_create_guest_profile(
            partner_id=partner.id,
            telegram_id=99887767,
            username="",
            first_name="",
            last_name="",
            language_code="",
        )

        self.assertEqual(guest_profile.telegram_account.username, "")
        self.assertEqual(guest_profile.telegram_account.first_name, "")
        self.assertEqual(guest_profile.telegram_account.last_name, "")
        self.assertEqual(guest_profile.telegram_account.language_code, "")


class UserAdminCreateTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="root",
            email="root@tableos.local",
            password="sup3r-secret-pw",
        )
        self.client.force_login(self.admin)

    def test_add_page_renders_creation_password_fields(self):
        response = self.client.get(reverse("admin:users_user_add"))
        self.assertEqual(response.status_code, 200)
        # The add page must use add_fieldsets (password1/password2), not the
        # change fieldset with a single "password" field.
        self.assertContains(response, "password1")
        self.assertContains(response, "password2")

    def test_superuser_can_create_user(self):
        response = self.client.post(
            reverse("admin:users_user_add"),
            {
                "username": "danilka",
                "email": "danilka@tableos.local",
                "first_name": "Danil",
                "last_name": "Kolodiazhnyi",
                "role": User.Role.MANAGER,
                "is_active": "on",
                "password1": "Sup3rSecret-123",
                "password2": "Sup3rSecret-123",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(User.objects.filter(username="danilka").exists())
