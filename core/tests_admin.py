from django.contrib import admin
from django.contrib.admin.utils import flatten_fieldsets
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, TestCase

from apps.employees.admin import EmployeeProfileAdmin
from apps.employees.models import EmployeeProfile
from apps.orders.admin import OrderAdmin
from apps.orders.models import Order
from apps.partners.admin import BotInstanceAdmin, PartnerAdmin, PartnerBotSettingsAdmin
from apps.partners.models import BotInstance, Partner, PartnerBotSettings
from apps.users.admin import TelegramAccountAdmin, UserAdmin
from apps.users.constants import AdminAccessPreset
from apps.users.models import AdminAccessProfile, TelegramAccount, User


class OwnerFacingAdminTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.partner = Partner.objects.create(
            name="Owner Venue",
            slug="owner-venue",
            timezone="Europe/Kiev",
            contact_phone="+380670000000",
            status=Partner.Status.ACTIVE,
        )
        self.owner = User.objects.create_user(
            username="owner_admin",
            email="owner@example.com",
            password="pass12345",
            partner=self.partner,
            role=User.Role.OWNER,
            is_staff=True,
            is_active=True,
        )
        AdminAccessProfile.objects.create(
            user=self.owner,
            partner=self.partner,
            preset=AdminAccessPreset.OWNER,
            is_partner_owner=True,
            can_access_admin=True,
            is_active=True,
        )
        self.bot_settings = PartnerBotSettings.objects.create(partner=self.partner)
        self.bot_instance = BotInstance.objects.create(
            partner=self.partner,
            display_name="Owner Bot",
            username="owner_venue_bot",
            mode=BotInstance.Mode.POLLING,
            is_active=False,
            token_encrypted="",
        )
        self.bot_instance.set_token("seed-placeholder-token-owner-venue")
        self.bot_instance.save(update_fields=["token_encrypted"])

    def _build_request(self, user=None):
        request = self.factory.get("/admin/")
        request.user = user or self.owner
        return request

    def _assert_fieldsets_match_form(self, model_admin, request, obj):
        form_class = model_admin.get_form(request, obj)
        form = form_class(instance=obj)
        fieldset_fields = flatten_fieldsets(model_admin.get_fieldsets(request, obj))
        readonly_fields = set(model_admin.get_readonly_fields(request, obj))
        missing_fields = [
            field_name
            for field_name in fieldset_fields
            if field_name not in form.fields and field_name not in readonly_fields
        ]
        self.assertEqual(missing_fields, [])

    def test_partner_admin_owner_fieldsets_match_form(self):
        request = self._build_request()
        model_admin = PartnerAdmin(Partner, admin.site)
        self._assert_fieldsets_match_form(model_admin, request, self.partner)
        self.assertEqual(
            model_admin.get_list_display(request),
            ("name", "timezone", "contact_phone", "created_at"),
        )

    def test_partner_bot_settings_admin_owner_fieldsets_match_form(self):
        request = self._build_request()
        model_admin = PartnerBotSettingsAdmin(PartnerBotSettings, admin.site)
        self._assert_fieldsets_match_form(model_admin, request, self.bot_settings)
        self.assertEqual(model_admin.get_search_fields(request), ("partner__name",))

    def test_bot_instance_admin_owner_hides_platform_only_columns(self):
        request = self._build_request()
        model_admin = BotInstanceAdmin(BotInstance, admin.site)
        form_class = model_admin.get_form(request, self.bot_instance)
        form = form_class(instance=self.bot_instance)
        self.assertNotIn("mode", form.fields)
        self.assertNotIn("webhook_url", form.fields)
        self.assertEqual(
            model_admin.get_list_display(request),
            ("display_name", "username", "token_status", "is_active", "created_at"),
        )

    def test_tenant_admin_does_not_receive_global_partner_list_filter(self):
        request = self._build_request()
        model_admin = OrderAdmin(Order, admin.site)

        self.assertNotIn("partner", model_admin.get_list_filter(request))

    def test_tenant_admin_related_user_choices_are_partner_scoped(self):
        other_partner = Partner.objects.create(
            name="Foreign Choice Venue",
            slug="foreign-choice-venue",
            status=Partner.Status.ACTIVE,
        )
        foreign_user = User.objects.create_user(
            username="foreign_choice_user",
            password="pass12345",
            partner=other_partner,
        )
        request = self._build_request()
        model_admin = EmployeeProfileAdmin(EmployeeProfile, admin.site)
        user_field = EmployeeProfile._meta.get_field("user")

        form_field = model_admin.formfield_for_foreignkey(user_field, request)

        self.assertIn(self.owner.id, form_field.queryset.values_list("id", flat=True))
        self.assertNotIn(foreign_user.id, form_field.queryset.values_list("id", flat=True))

    def test_tenant_admin_can_edit_employee_username_but_not_telegram_id(self):
        telegram_account = TelegramAccount.objects.create(
            telegram_id=910001,
            username="owner_employee",
        )
        employee = EmployeeProfile.objects.create(
            partner=self.partner,
            user=self.owner,
            telegram_account=telegram_account,
        )
        request = self._build_request()
        model_admin = EmployeeProfileAdmin(EmployeeProfile, admin.site)

        form_class = model_admin.get_form(request, employee)
        form = form_class(instance=employee)

        self.assertTrue(form.fields["telegram_id"].disabled)
        self.assertFalse(form.fields["telegram_username"].disabled)

    def test_owner_can_edit_a_venue_users_name_and_username(self):
        employee_user = User.objects.create_user(
            username="owner_editable_employee",
            first_name="Before",
            last_name="Name",
            password="pass12345",
            partner=self.partner,
            role=User.Role.WAITER,
        )
        request = self._build_request()
        model_admin = UserAdmin(User, admin.site)

        self.assertTrue(model_admin.has_change_permission(request, employee_user))
        form_class = model_admin.get_form(request, employee_user)
        form = form_class(instance=employee_user)

        self.assertIn("username", form.fields)
        self.assertIn("first_name", form.fields)
        self.assertIn("last_name", form.fields)

    def test_global_telegram_accounts_are_platform_only(self):
        platform_admin = User.objects.create_superuser(
            username="platform_identity_admin",
            email="platform-identity@example.com",
            password="pass12345",
        )
        model_admin = TelegramAccountAdmin(TelegramAccount, admin.site)

        self.assertFalse(model_admin.has_module_permission(self._build_request()))
        self.assertTrue(model_admin.has_module_permission(self._build_request(platform_admin)))

    def test_tenant_admin_inline_save_rejects_explicit_foreign_partner(self):
        other_partner = Partner.objects.create(
            name="Foreign Owner Venue",
            slug="foreign-owner-venue",
            status=Partner.Status.ACTIVE,
        )
        other_user = User.objects.create_user(
            username="foreign_employee",
            password="pass12345",
            partner=other_partner,
        )
        foreign_employee = EmployeeProfile(
            partner=other_partner,
            user=other_user,
        )

        class _Formset:
            deleted_objects = []

            def save(self, commit):
                self_test.assertFalse(commit)
                return [foreign_employee]

            def save_m2m(self):
                raise AssertionError("save_m2m must not run for a foreign inline")

        self_test = self
        request = self._build_request()
        model_admin = OrderAdmin(Order, admin.site)

        with self.assertRaises(PermissionDenied):
            model_admin.save_formset(request, form=None, formset=_Formset(), change=True)

    def test_tenant_admin_inline_delete_rejects_explicit_foreign_partner(self):
        other_partner = Partner.objects.create(
            name="Foreign Delete Venue",
            slug="foreign-delete-venue",
            status=Partner.Status.ACTIVE,
        )
        other_user = User.objects.create_user(
            username="foreign_delete_employee",
            password="pass12345",
            partner=other_partner,
        )
        foreign_employee = EmployeeProfile.objects.create(
            partner=other_partner,
            user=other_user,
        )

        class _Formset:
            deleted_objects = [foreign_employee]

            def save(self, commit):
                self_test.assertFalse(commit)
                return []

            def save_m2m(self):
                raise AssertionError("save_m2m must not run for a foreign inline")

        self_test = self
        request = self._build_request()
        model_admin = OrderAdmin(Order, admin.site)

        with self.assertRaises(PermissionDenied):
            model_admin.save_formset(request, form=None, formset=_Formset(), change=True)

        self.assertTrue(EmployeeProfile.objects.filter(id=foreign_employee.id).exists())
