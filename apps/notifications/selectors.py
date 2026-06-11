from apps.notifications.models import NotificationPreference


def preferences_for_guest(guest_id):
    return NotificationPreference.objects.filter(guest_id=guest_id)
