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
            "Модули · Базовые",
            {
                "description": (
                    "Профиль гостя и бонусный баланс считаются базовой частью "
                    "продукта. Меню нужно для заказов, корзины и быстрых продаж."
                ),
                "fields": (
                    "module_menu_enabled",
                ),
            },
        ),
        (
            "Модули · Сценарии заказа",
            {
                "description": (
                    "Столы, доставка и самовывоз — независимые сценарии. "
                    "Можно включить любой набор; для заказов нужен хотя бы один."
                ),
                "fields": (
                    "module_tables_enabled",
                    "module_delivery_enabled",
                    "module_pickup_enabled",
                ),
            },
        ),
        (
            "Модули · Заказы и оплата",
            {
                "description": (
                    "Корзина требует меню и заказы. Счета требуют заказы; "
                    "запрос счёта гостем работает только при включённых столах."
                ),
                "fields": (
                    "module_orders_enabled",
                    "module_cart_enabled",
                    "module_billing_enabled",
                ),
            },
        ),
        (
            "Модули · Операции и отчёты",
            {
                "description": (
                    "Вызов персонала требует столы. Быстрые продажи требуют меню."
                ),
                "fields": (
                    "module_staff_call_enabled",
                    "module_quick_sale_enabled",
                    "module_reports_enabled",
                ),
            },
        ),
        (
            "Behavior",
            {
                "fields": (
                    "auto_close_table_session_after_payment",
                    "max_menu_items_per_category_message",
                    "duplicate_request_cooldown_seconds",
                    "allow_menu_without_session",
                    "staff_call_waiter_enabled",
                    "staff_call_bartender_enabled",
                    "staff_call_hookah_enabled",
                    "extra_config",
                )
            },
        ),
        (
            "Buttons",
            {
                "fields": (
                    "button_menu_label",
                    "button_session_label",
                    "button_cart_label",
                    "button_checkout_label",
                    "button_delivery_label",
                    "button_pickup_label",
                    "button_help_label",
                    "button_my_profile",
                    "button_profile_bonuses_label",
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
    platform_only_fields = ("extra_config",)
    list_display = (
        "partner",
        "module_menu_enabled",
        "module_delivery_enabled",
        "module_pickup_enabled",
        "module_orders_enabled",
        "module_billing_enabled",
        "allow_menu_without_session",
        "updated_at",
    )
    list_filter = (
        "module_menu_enabled",
        "module_delivery_enabled",
        "module_pickup_enabled",
        "module_orders_enabled",
        "module_billing_enabled",
        "module_staff_call_enabled",
        "allow_menu_without_session",
    )
    search_fields = ("partner__name", "partner__slug")
    fieldsets = PartnerBotSettingsInline.fieldsets

    def get_search_fields(self, request):
        if request.user.is_superuser:
            return self.search_fields
        return ("partner__name",)
