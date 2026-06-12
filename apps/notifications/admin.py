from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _

from apps.notifications.forms import BroadcastCampaignAdminForm
from apps.notifications.models import BroadcastCampaign, NotificationPreference, StaffNotification
from apps.notifications.services import (
    NotificationDeliveryError,
    ensure_broadcast_campaign_can_send,
    get_broadcast_audience_stats,
    send_broadcast_campaign,
)
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
    form = BroadcastCampaignAdminForm
    list_display = (
        "name",
        "partner",
        "status",
        "eligible_recipients_count",
        "delivered_count",
        "failed_count",
        "scheduled_at",
        "sent_at",
    )
    list_filter = ("partner", "status")
    search_fields = ("name", "message", "partner__name")
    readonly_fields = (
        "status",
        "delivery_readiness",
        "audience_summary",
        "delivered_count",
        "failed_count",
        "last_error",
        "delivery_started_at",
        "sent_at",
    )
    actions = ("send_now",)
    fieldsets = (
        (
            "Campaign",
            {
                "description": (
                    "Партнёрский flow: создайте кампанию, проверьте аудиторию и включите "
                    "«Отправить сразу после сохранения», если хотите немедленно поставить "
                    "её в очередь. Если указать `scheduled_at` и не включать отправку сразу, "
                    "кампания будет автоматически подхвачена Celery beat."
                ),
                "fields": (
                    "partner",
                    "name",
                    "message",
                    "scheduled_at",
                    "send_now",
                ),
            },
        ),
        (
            "Delivery",
            {
                "fields": (
                    "status",
                    "delivery_readiness",
                    "audience_summary",
                    "delivery_started_at",
                    "sent_at",
                    "delivered_count",
                    "failed_count",
                    "last_error",
                )
            },
        ),
    )

    def get_form(self, request, obj=None, **kwargs):
        form_class = super().get_form(request, obj, **kwargs)

        class RequestBoundForm(form_class):
            def __init__(self, *args, **inner_kwargs):
                inner_kwargs["request"] = request
                super().__init__(*args, **inner_kwargs)

        return RequestBoundForm

    @admin.display(description="Готовых получателей")
    def eligible_recipients_count(self, obj: BroadcastCampaign) -> int:
        if not obj or not obj.partner_id:
            return 0
        return get_broadcast_audience_stats(partner_id=obj.partner_id).eligible_recipients

    @admin.display(description="Готовность к отправке")
    def delivery_readiness(self, obj: BroadcastCampaign) -> str:
        if not obj or not obj.partner_id:
            return "Сохраните кампанию, чтобы проверить готовность отправки."
        try:
            stats = ensure_broadcast_campaign_can_send(partner_id=obj.partner_id)
        except NotificationDeliveryError as exc:
            return f"Не готово: {exc}"
        return (
            "Готово: есть активный polling-бот и аудитория для отправки "
            f"({stats.eligible_recipients} получателей)."
        )

    @admin.display(description="Аудитория")
    def audience_summary(self, obj: BroadcastCampaign) -> str:
        if not obj or not obj.partner_id:
            return "Сохраните кампанию, чтобы увидеть аудиторию."
        stats = get_broadcast_audience_stats(partner_id=obj.partner_id)
        return (
            f"Подписанных гостей: {stats.opted_in_guests}. "
            f"Готово к отправке: {stats.eligible_recipients}. "
            f"Заблокировали бота: {stats.blocked_recipients}."
        )

    @admin.action(description=_("Queue selected campaigns for sending"))
    def send_now(self, request, queryset):
        queued_count = 0
        for campaign in queryset:
            try:
                send_broadcast_campaign(campaign)
            except NotificationDeliveryError as exc:
                self.message_user(
                    request,
                    f"Кампания «{campaign.name}»: {exc}",
                    level=messages.WARNING,
                )
                continue
            queued_count += 1
        if queued_count:
            self.message_user(
                request,
                f"Кампаний поставлено в очередь: {queued_count}.",
                level=messages.INFO,
            )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if form.cleaned_data.get("send_now", False):
            send_broadcast_campaign(obj)


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
