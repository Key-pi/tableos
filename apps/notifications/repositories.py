from django.db.models import Q

from apps.notifications.models import BroadcastCampaign, NotificationPreference, StaffNotification
from apps.users.models import GuestProfile


class NotificationPreferenceRepository:
    @staticmethod
    def subscribers(partner_id):
        return NotificationPreference.objects.filter(partner_id=partner_id, marketing_enabled=True)

    @staticmethod
    def marketing_guests(partner_id):
        return GuestProfile.objects.select_related("telegram_account").filter(
            partner_id=partner_id,
            is_subscribed=True,
        ).filter(
            Q(notification_preferences__isnull=True)
            | Q(notification_preferences__marketing_enabled=True)
        )

    @staticmethod
    def guest_allows_order_updates(guest_id):
        return not NotificationPreference.objects.filter(
            guest_id=guest_id,
            order_updates_enabled=False,
        ).exists()


class BroadcastCampaignRepository:
    @staticmethod
    def queued(partner_id):
        return BroadcastCampaign.objects.filter(
            partner_id=partner_id,
            status__in=[BroadcastCampaign.Status.DRAFT, BroadcastCampaign.Status.SCHEDULED],
        )


class StaffNotificationRepository:
    @staticmethod
    def unread_for_employee(employee_id):
        return StaffNotification.objects.filter(
            employee_id=employee_id,
            is_read=False,
        ).select_related("order", "partner")

    @staticmethod
    def by_id_for_employee(notification_id, *, employee_id, partner_id):
        return StaffNotification.objects.select_related("order", "partner").get(
            id=notification_id,
            employee_id=employee_id,
            partner_id=partner_id,
        )
