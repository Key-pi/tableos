from django.contrib import admin, messages

from apps.notifications.models import (
    BroadcastCampaign,
    BroadcastDelivery,
    NotificationPreference,
    StaffNotification,
)
from apps.notifications.services import send_broadcast_campaign
from apps.users.constants import AdminSection
from core.admin_mixins import ScopedAdminMixin


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(ScopedAdminMixin):
    admin_section = AdminSection.NOTIFICATIONS
    list_display = (
        "guest",
        "partner",
        "order_updates_enabled",
        "bonus_updates_enabled",
        "marketing_enabled",
    )
    list_filter = ("partner", "order_updates_enabled", "bonus_updates_enabled", "marketing_enabled")
    search_fields = ("guest__telegram_account__username", "partner__name")


@admin.register(BroadcastCampaign)
class BroadcastCampaignAdmin(ScopedAdminMixin):
    admin_section = AdminSection.NOTIFICATIONS
    list_display = (
        "name",
        "partner",
        "status",
        "delivered_count",
        "failed_count",
        "unknown_count",
        "scheduled_at",
        "sent_at",
    )
    list_filter = ("partner", "status")
    search_fields = ("name", "message", "partner__name")
    readonly_fields = (
        "delivered_count",
        "failed_count",
        "unknown_count",
        "last_error",
        "delivery_started_at",
        "sent_at",
    )
    actions = ("send_now",)

    @admin.action(description="Отправить выбранные рассылки")
    def send_now(self, request, queryset):
        queued_count = 0
        for campaign in queryset:
            send_broadcast_campaign(campaign)
            queued_count += 1
        self.message_user(
            request, f"Поставлено в отправку рассылок: {queued_count}.", level=messages.INFO
        )


@admin.register(BroadcastDelivery)
class BroadcastDeliveryAdmin(ScopedAdminMixin):
    """Read-only per-guest delivery log of broadcasts (PQ-014)."""

    admin_section = AdminSection.NOTIFICATIONS
    allow_partner_add = False
    allow_partner_delete = False
    list_display = ("campaign", "guest", "status", "finished_at", "partner")
    list_filter = ("partner", "status")
    search_fields = ("campaign__name", "guest__customer_code", "error")
    readonly_fields = (
        "partner",
        "campaign",
        "guest",
        "status",
        "claim_token",
        "claimed_at",
        "finished_at",
        "error",
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


@admin.register(StaffNotification)
class StaffNotificationAdmin(ScopedAdminMixin):
    admin_section = AdminSection.NOTIFICATIONS
    allow_partner_add = False
    allow_partner_delete = False
    list_display = (
        "title",
        "partner",
        "employee",
        "category",
        "order",
        "delivery_status",
        "is_read",
        "created_at",
    )
    list_filter = ("partner", "category", "delivery_status", "is_read")
    search_fields = (
        "title",
        "message",
        "partner__name",
        "employee__user__username",
        "order__public_id",
    )
