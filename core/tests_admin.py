from django.contrib import admin
from django.contrib.admin.utils import flatten_fieldsets
from django.test import RequestFactory, TestCase

from apps.partners.admin import BotInstanceAdmin, PartnerAdmin, PartnerBotSettingsAdmin
from apps.partners.models import BotInstance, Partner, PartnerBotSettings
from apps.users.constants import AdminAccessPreset
from apps.users.models import AdminAccessProfile, User


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

    def _build_request(self):
        request = self.factory.get("/admin/")
        request.user = self.owner
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
