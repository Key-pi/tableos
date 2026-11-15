from django.contrib import admin

from apps.bonuses.models import BonusProgram, BonusTransaction
from apps.users.constants import AdminSection
from core.admin_mixins import ScopedAdminMixin


@admin.register(BonusProgram)
class BonusProgramAdmin(ScopedAdminMixin):
    admin_section = AdminSection.BONUSES
    platform_only_fields = ("strategy_code", "config")
    list_display = (
        "name",
        "partner",
        "trigger_event",
        "program_type",
        "percent",
        "accrual_base",
        "fixed_amount",
        "milestone_order_count",
        "is_active",
    )
    list_filter = ("partner", "trigger_event", "program_type", "is_active")
    search_fields = ("name", "partner__name")


@admin.register(BonusTransaction)
class BonusTransactionAdmin(ScopedAdminMixin):
    admin_section = AdminSection.BONUSES
    allow_partner_add = False
    allow_partner_delete = False
    list_display = ("guest", "program", "partner", "transaction_type", "amount", "created_at")
    list_filter = ("partner", "transaction_type")
    search_fields = (
        "guest__customer_code",
        "guest__telegram_account__username",
        "partner__name",
        "comment",
    )
    readonly_fields = (
        "partner",
        "guest",
        "program",
        "order",
        "bill",
        "transaction_type",
        "amount",
        "expires_at",
        "comment",
        "source_type",
        "source_id",
        "idempotency_key",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        if obj is not None:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False
