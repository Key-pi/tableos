"""Owner records and rights are touched only by the owner (AA-036, PQ-035)."""

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase

from apps.partners.models import Partner
from apps.users.admin import AdminAccessProfileAdmin, UserAdmin
from apps.users.constants import AdminAccessPreset, AdminSection
from apps.users.models import AdminAccessProfile, AdminSectionPermission, User


class OwnerProtectionTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(name="P", slug="p", status=Partner.Status.ACTIVE)
        self.owner = User.objects.create_user(
            username="own", password="x", partner=self.partner, role=User.Role.OWNER
        )
        self.owner_profile = AdminAccessProfile.objects.create(
            user=self.owner,
            partner=self.partner,
            preset=AdminAccessPreset.OWNER,
            is_partner_owner=True,
            can_access_admin=True,
        )
        # A staff member the owner trusted with "Admin access" (custom rights).
        self.delegate = User.objects.create_user(
            username="deleg", password="x", partner=self.partner, role=User.Role.MANAGER
        )
        self.delegate_profile = AdminAccessProfile.objects.create(
            user=self.delegate,
            partner=self.partner,
            preset=AdminAccessPreset.CUSTOM,
            can_access_admin=True,
        )
        for section, flags in (
            (AdminSection.ADMIN_ACCESS, {"can_view": True, "can_add": True, "can_change": True}),
            (AdminSection.MENU, {"can_view": True, "can_change": True}),
        ):
            AdminSectionPermission.objects.create(
                profile=self.delegate_profile, section=section, **flags
            )
        self.waiter = User.objects.create_user(
            username="wait", password="x", partner=self.partner, role=User.Role.WAITER
        )
        self.waiter_profile = AdminAccessProfile.objects.create(
            user=self.waiter,
            partner=self.partner,
            preset=AdminAccessPreset.CUSTOM,
            can_access_admin=True,
        )
        self.profiles = AdminAccessProfileAdmin(AdminAccessProfile, admin.site)
        self.users = UserAdmin(User, admin.site)

    def _request(self, user):
        request = RequestFactory().get("/admin/")
        request.user = user
        return request

    def test_delegate_cannot_change_the_owner(self):
        request = self._request(self.delegate)
        self.assertFalse(self.profiles.has_change_permission(request, self.owner_profile))
        self.assertFalse(self.profiles.has_delete_permission(request, self.owner_profile))
        self.assertFalse(self.users.has_change_permission(request, self.owner))
        self.assertFalse(self.users.has_delete_permission(request, self.owner))

    def test_manager_cannot_change_the_owners_staff_profile(self):
        from apps.employees.admin import EmployeeProfileAdmin
        from apps.employees.models import EmployeeProfile

        employees = EmployeeProfileAdmin(EmployeeProfile, admin.site)
        owner_staff = EmployeeProfile.objects.create(partner=self.partner, user=self.owner)
        waiter_staff = EmployeeProfile.objects.create(partner=self.partner, user=self.waiter)
        manager = User.objects.create_user(
            username="man", password="x", partner=self.partner, role=User.Role.MANAGER
        )
        AdminAccessProfile.objects.create(
            user=manager,
            partner=self.partner,
            preset=AdminAccessPreset.MANAGER,
            can_access_admin=True,
        )
        request = self._request(manager)
        self.assertFalse(employees.has_change_permission(request, owner_staff))
        self.assertFalse(employees.has_delete_permission(request, owner_staff))
        self.assertTrue(employees.has_change_permission(request, waiter_staff))
        self.assertTrue(employees.has_change_permission(self._request(self.owner), owner_staff))

    def test_delegate_cannot_change_own_rights(self):
        request = self._request(self.delegate)
        self.assertFalse(self.profiles.has_change_permission(request, self.delegate_profile))

    def test_delegate_cannot_make_anyone_owner(self):
        request = self._request(self.delegate)
        form_class = self.profiles.get_form(request, self.waiter_profile)
        form = form_class(instance=self.waiter_profile)
        self.assertNotIn("is_partner_owner", form.fields)
        presets = [value for value, _label in form.fields["preset"].choices]
        self.assertNotIn(AdminAccessPreset.OWNER, presets)
        user_form = self.users.get_form(request, self.waiter)(instance=self.waiter)
        roles = [value for value, _label in user_form.fields["role"].choices]
        self.assertNotIn(User.Role.OWNER, roles)

    def test_delegate_cannot_grant_rights_above_own(self):
        request = self._request(self.delegate)
        self.assertEqual(
            self.profiles.rights_above_actor(
                request,
                {
                    AdminSection.MENU: {"can_view": True, "can_delete": True},
                    AdminSection.BILLING: {"can_view": True},
                },
            ),
            [(AdminSection.MENU, "can_delete"), (AdminSection.BILLING, "can_view")],
        )
        self.assertEqual(
            self.profiles.rights_above_actor(
                request, {AdminSection.MENU: {"can_view": True, "can_change": True}}
            ),
            [],
        )
        # A preset wider than the delegate's own rights is not offered.
        form = self.profiles.get_form(request, self.waiter_profile)(instance=self.waiter_profile)
        presets = [value for value, _label in form.fields["preset"].choices]
        self.assertNotIn(AdminAccessPreset.MANAGER, presets)
        with self.assertRaises(ValidationError):
            self.profiles.check_rights_not_above_actor(request, self.waiter_profile, {
                AdminSection.BILLING: {"can_view": True},
            })

    def test_delegate_cannot_change_a_profile_with_more_rights(self):
        AdminSectionPermission.objects.create(
            profile=self.waiter_profile, section=AdminSection.BILLING, can_view=True
        )
        request = self._request(self.delegate)
        self.assertFalse(self.profiles.has_change_permission(request, self.waiter_profile))
        AdminSectionPermission.objects.filter(profile=self.waiter_profile).delete()
        self.assertTrue(self.profiles.has_change_permission(request, self.waiter_profile))

    def test_owner_and_superuser_keep_full_control(self):
        for actor in (
            self.owner,
            User.objects.create_superuser(username="root", password="x", email="r@x.io"),
        ):
            request = self._request(actor)
            self.assertTrue(self.profiles.has_change_permission(request, self.owner_profile))
            self.assertTrue(self.profiles.has_change_permission(request, self.delegate_profile))
            form = self.profiles.get_form(request, self.waiter_profile)(
                instance=self.waiter_profile
            )
            self.assertIn("is_partner_owner", form.fields)
            self.assertEqual(
                self.profiles.rights_above_actor(
                    request, {AdminSection.BILLING: {"can_delete": True}}
                ),
                [],
            )
