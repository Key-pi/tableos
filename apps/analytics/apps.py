from django.apps import AppConfig

from core.i18n import gettext_lazy as _lazy


class AnalyticsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.analytics"
    label = "analytics"
    verbose_name = _lazy("Аналитика")
