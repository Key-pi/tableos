from django.contrib import admin

from apps.partners.forms import BotInstanceAdminForm
from apps.partners.models import BotInstance, Partner, PartnerBotSettings
from apps.users.constants import AdminSection
from core.admin_mixins import ScopedAdminMixin


class PartnerBotSettingsInline(admin.StackedInline):
    model = PartnerBotSettings
    extra = 0
    max_num = 1
    fieldsets = (
        (
            "Behavior",
            {
                "fields": (
                    "allow_menu_without_session",
                    "show_call_staff_button",
                    "show_request_bill_button",
                    "staff_call_waiter_enabled",
                    "staff_call_bartender_enabled",
                    "staff_call_hookah_enabled",
                    "guest_flow_code",
                    "staff_flow_code",
                    "extra_config",
                )
            },
        ),
        (
            "Buttons",
            {
                "fields": (
                    "show_help_button",
                    "show_session_button",
                    "show_cart_button",
                    "show_checkout_button",
                    "show_loyalty_button",
                    "button_menu_label",
                    "button_session_label",
                    "button_cart_label",
                    "button_checkout_label",
                    "button_help_label",
                    "button_loyalty_label",
                    "button_call_staff_label",
                    "button_request_bill_label",
                    "button_call_waiter_label",
                    "button_call_bartender_label",
                    "button_call_hookah_label",
                )
            },
        ),
        (
            "Messages",
            {
                "fields": (
                    "welcome_message_template",
                    "table_activated_message_template",
                    "menu_header_template",
                    "menu_requires_session_hint_template",
                    "menu_active_session_hint_template",
                    "menu_empty_message_template",
                    "ordering_help_message_template",
                    "cart_empty_message_template",
                    "cart_cleared_message_template",
                    "staff_call_prompt_template",
                    "staff_call_success_template",
                    "request_bill_prompt_template",
                    "request_bill_personal_success_template",
                    "request_bill_shared_success_template",
                    "request_bill_custom_success_template",
                    "order_created_message_template",
                )
            },
        ),
    )


@admin.register(Partner)
class PartnerAdmin(ScopedAdminMixin):
    admin_section = AdminSection.PARTNER_PROFILE
    partner_filter = "id"
    scope_partner_field = None
    allow_partner_add = False
    allow_partner_delete = False
    platform_only_fields = ("slug", "status")
    list_display = ("name", "slug", "status", "timezone", "created_at")
    list_filter = ("status", "timezone")
    search_fields = ("name", "slug", "contact_phone")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [PartnerBotSettingsInline]

    def get_list_display(self, request):
        if request.user.is_superuser:
            return self.list_display
        return ("name", "timezone", "contact_phone", "created_at")

    def get_list_filter(self, request):
        if request.user.is_superuser:
            return self.list_filter
        return ("timezone",)

    def get_search_fields(self, request):
        if request.user.is_superuser:
            return self.search_fields
        return ("name", "contact_phone")

    def get_prepopulated_fields(self, request, obj=None):
        if request.user.is_superuser:
            return self.prepopulated_fields
        return {}

    def get_fieldsets(self, request, obj=None):
        if request.user.is_superuser:
            return super().get_fieldsets(request, obj)
        return (
            (
                None,
                {
                    "fields": (
                        "name",
                        "timezone",
                        "contact_phone",
                    )
                },
            ),
        )

    def get_inline_instances(self, request, obj=None):
        if request.user.is_superuser:
            return super().get_inline_instances(request, obj)
        return []


@admin.register(BotInstance)
class BotInstanceAdmin(ScopedAdminMixin):
    admin_section = AdminSection.BOT_INSTANCES
    platform_only_fields = ("mode", "webhook_url")
    form = BotInstanceAdminForm
    list_display = (
        "display_name",
        "username",
        "partner",
        "mode",
        "token_status",
        "is_active",
        "created_at",
    )
    list_filter = ("mode", "is_active")
    search_fields = ("display_name", "username", "partner__name")
    readonly_fields = ("created_at", "updated_at")

    def get_list_display(self, request):
        if request.user.is_superuser:
            return self.list_display
        return ("display_name", "username", "token_status", "is_active", "created_at")

    def get_list_filter(self, request):
        if request.user.is_superuser:
            return self.list_filter
        return ("is_active",)

    def get_search_fields(self, request):
        if request.user.is_superuser:
            return self.search_fields
        return ("display_name", "username")

    @admin.display(description="Token status")
    def token_status(self, obj: BotInstance) -> str:
        return obj.token_status_label


@admin.register(PartnerBotSettings)
class PartnerBotSettingsAdmin(ScopedAdminMixin):
    admin_section = AdminSection.BOT_CONTENT
    platform_only_fields = ("guest_flow_code", "staff_flow_code", "extra_config")
    list_display = (
        "partner",
        "allow_menu_without_session",
        "show_help_button",
        "updated_at",
    )
    list_filter = ("allow_menu_without_session", "show_help_button")
    search_fields = ("partner__name", "partner__slug")
    fieldsets = PartnerBotSettingsInline.fieldsets

    def get_search_fields(self, request):
        if request.user.is_superuser:
            return self.search_fields
        return ("partner__name",)
