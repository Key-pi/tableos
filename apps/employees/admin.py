from django.contrib import admin

from apps.employees.forms import EmployeeProfileAdminForm
from apps.employees.models import EmployeeProfile
from apps.users.admin import is_owner_user
from apps.users.constants import AdminSection
from core.admin_mixins import ScopedAdminMixin, is_partner_owner_or_superuser


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
        "can_adjust_bills",
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
            None,
            {
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
            "Права в счетах",
            {
                "fields": ("can_adjust_bills",),
                "description": (
                    "Изменение и аннулирование счетов. Выдаёт только владелец заведения."
                ),
            },
        ),
        (
            "Compensation",
            {
                "fields": (
                    "hourly_rate",
                    "commission_rate",
                )
            },
        ),
        (
            "Bot notifications",
            {
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

    def has_change_permission(self, request, obj=None):
        if obj is not None and not self._may_touch(request, obj):
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and not self._may_touch(request, obj):
            return False
        return super().has_delete_permission(request, obj)

    @staticmethod
    def _may_touch(request, employee) -> bool:
        # AA-036: only the owner changes the owner's staff profile (it carries
        # the owner's bot access and security alerts).
        return is_partner_owner_or_superuser(request.user) or not is_owner_user(employee.user)

    def get_form(self, request, obj=None, change=False, **kwargs):
        form_class = super().get_form(request, obj, change, **kwargs)

        class RequestBoundEmployeeProfileAdminForm(form_class):
            def __init__(self, *args, **form_kwargs):
                form_kwargs["actor_user"] = request.user
                super().__init__(*args, **form_kwargs)

        return RequestBoundEmployeeProfileAdminForm

    @admin.display(description="Telegram")
    def telegram_binding(self, obj: EmployeeProfile) -> str:
        if obj.telegram_account_id is None:
            return "Not linked"
        username = f"@{obj.telegram_account.username}" if obj.telegram_account.username else "-"
        return f"{obj.telegram_account.telegram_id} / {username}"
