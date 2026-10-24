from django.apps import AppConfig

from core.i18n import gettext_lazy as _lazy


class NotificationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.notifications"
    label = "notifications"
    verbose_name = _lazy("Уведомления и рассылки")
