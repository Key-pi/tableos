from django.contrib import admin

from apps.notifications.services import notify_owners_about_transfer_details_change
from apps.partners.forms import BotInstanceAdminForm
from apps.partners.models import BotInstance, OpeningHours, Partner, PartnerBotSettings
from apps.users.constants import AdminSection
from core.admin_mixins import ScopedAdminMixin


class OpeningHoursInline(admin.TabularInline):
    """Venue hours in the venue timezone; closing before opening = past midnight.

    No rows means always open. Pickup orders are accepted only within hours.
    """

    model = OpeningHours
    extra = 0
    fields = ("weekday", "opens_at", "closes_at")
    verbose_name = "Интервал работы"
    verbose_name_plural = (
        "Часы работы (время заведения; закрытие раньше открытия — после полуночи; "
        "пусто — всегда открыто)"
    )


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
                    "Столы, самовывоз, доставка и отправка почтой — независимые "
                    "сценарии. Можно включить любой набор; для заказов нужен хотя бы "
                    "один. У каждого удалённого сценария включите способы оплаты: "
                    "переводом заранее (на реквизиты ниже) и/или при получении; если "
                    "включены оба, гость выбирает. У отправки так же включаются службы "
                    "и варианты оплаты пересылки — гость выбирает из включённых. "
                    "Часы работы (для самовывоза и доставки) — в карточке партнёра."
                ),
                "fields": (
                    "module_tables_enabled",
                    "module_pickup_enabled",
                    ("pickup_pay_transfer", "pickup_pay_on_receipt"),
                    "module_delivery_enabled",
                    ("delivery_pay_transfer", "delivery_pay_on_receipt"),
                    "transfer_payment_details",
                    ("remote_unpaid_orders_limit", "transfer_wait_minutes"),
                    "delivery_fee",
                    "delivery_free_from",
                    "delivery_min_order",
                    "module_shipping_enabled",
                    ("shipping_nova_poshta", "shipping_ukrposhta", "shipping_meest"),
                    ("shipping_pay_transfer", "shipping_pay_on_receipt"),
                    ("shipping_cost_carrier", "shipping_cost_fixed"),
                    "shipping_fee",
                    "shipping_free_from",
                    "shipping_min_order",
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
            "Поведение бота",
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
            "Кнопки",
            {
                "fields": (
                    "button_menu_label",
                    "button_session_label",
                    "button_cart_label",
                    "button_checkout_label",
                    "button_delivery_label",
                    "button_pickup_label",
                    "button_shipping_label",
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
            "Тексты сообщений",
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
    inlines = [PartnerBotSettingsInline, OpeningHoursInline]

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

    def save_formset(self, request, form, formset, change):
        super().save_formset(request, form, formset, change)
        if formset.model is PartnerBotSettings:
            for inline_form in formset.forms:
                if inline_form.instance.pk is not None:
                    _alert_owners_if_transfer_details_changed(
                        inline_form, inline_form.instance, request.user
                    )


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

    @admin.display(description="Токен")
    def token_status(self, obj: BotInstance) -> str:
        return obj.token_status_label


def _alert_owners_if_transfer_details_changed(form, obj, actor) -> None:
    # AA-035: the owner is told whenever the payment details change.
    if "transfer_payment_details" in getattr(form, "changed_data", ()):
        notify_owners_about_transfer_details_change(settings=obj, actor=actor)


@admin.register(PartnerBotSettings)
class PartnerBotSettingsAdmin(ScopedAdminMixin):
    admin_section = AdminSection.BOT_CONTENT
    # Guests pay to these details: only the owner sees and changes them.
    owner_only_fields = ("transfer_payment_details",)
    platform_only_fields = ("extra_config",)
    list_display = (
        "partner",
        "module_menu_enabled",
        "module_delivery_enabled",
        "module_pickup_enabled",
        "module_shipping_enabled",
        "module_orders_enabled",
        "module_billing_enabled",
        "allow_menu_without_session",
        "updated_at",
    )
    list_filter = (
        "module_menu_enabled",
        "module_delivery_enabled",
        "module_pickup_enabled",
        "module_shipping_enabled",
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

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        _alert_owners_if_transfer_details_changed(form, obj, request.user)
