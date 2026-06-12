from django.contrib import admin

from apps.employees.forms import EmployeeProfileAdminForm
from apps.employees.models import EmployeeProfile
from apps.users.constants import AdminSection
from core.admin_mixins import ScopedAdminMixin


@admin.register(EmployeeProfile)
class EmployeeProfileAdmin(ScopedAdminMixin):
    admin_section = AdminSection.EMPLOYEES
    form = EmployeeProfileAdminForm
    list_display = (
        "user",
        "partner",
        "telegram_binding",
        "title",
        "hourly_rate",
        "commission_rate",
        "bot_notifications_enabled",
        "notify_on_order_created",
        "notify_on_order_status_changed",
        "notify_on_guest_calls",
        "notify_on_billing_requests",
        "is_active",
    )
    list_filter = ("partner", "is_active")
    search_fields = (
        "user__username",
        "user__first_name",
        "user__last_name",
        "partner__name",
        "telegram_account__username",
        "telegram_account__telegram_id",
    )
    fieldsets = (
        (
            "Профиль",
            {
                "classes": ("tab",),
                "fields": (
                    "partner",
                    "user",
                    "telegram_id",
                    "telegram_username",
                    "title",
                    "is_active",
                )
            },
        ),
        (
            "Оплата",
            {
                "classes": ("tab",),
                "fields": (
                    "hourly_rate",
                    "commission_rate",
                )
            },
        ),
        (
            "Уведомления",
            {
                "classes": ("tab",),
                "fields": (
                    "bot_notifications_enabled",
                    "notify_on_order_created",
                    "notify_on_order_status_changed",
                    "notify_on_guest_calls",
                    "notify_on_billing_requests",
                ),
                "description": (
                    "Controls whether this employee receives operational notifications "
                    "in the Telegram staff bot."
                ),
            },
        ),
    )
    save_on_top = True

    @admin.display(description="Telegram")
    def telegram_binding(self, obj: EmployeeProfile) -> str:
        if obj.telegram_account_id is None:
            return "Not linked"
        username = f"@{obj.telegram_account.username}" if obj.telegram_account.username else "-"
        return f"{obj.telegram_account.telegram_id} / {username}"
