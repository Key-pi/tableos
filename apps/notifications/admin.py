from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _

from apps.notifications.models import BroadcastCampaign, NotificationPreference, StaffNotification
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
    fieldsets = (
        (
            "Кампания",
            {
                "classes": ("tab",),
                "fields": ("partner", "name", "message", "scheduled_at", "status"),
            },
        ),
        (
            "Доставка",
            {
                "classes": ("tab",),
                "description": (
                    "Служебное состояние отправки и counters обновляются автоматически."
                ),
                "fields": (
                    "delivered_count",
                    "failed_count",
                    "last_error",
                    "delivery_started_at",
                    "sent_at",
                ),
            },
        ),
    )
    list_display = (
        "name",
        "partner",
        "status",
        "delivered_count",
        "failed_count",
        "scheduled_at",
        "sent_at",
    )
    list_filter = ("partner", "status")
    search_fields = ("name", "message", "partner__name")
    readonly_fields = (
        "delivered_count",
        "failed_count",
        "last_error",
        "delivery_started_at",
        "sent_at",
    )
    actions = ("send_now",)
    save_on_top = True

    @admin.action(description=_("Queue selected campaigns for sending"))
    def send_now(self, request, queryset):
        queued_count = 0
        for campaign in queryset:
            send_broadcast_campaign(campaign)
            queued_count += 1
        self.message_user(request, f"Campaigns queued: {queued_count}.", level=messages.INFO)


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
