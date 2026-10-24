from django.apps import AppConfig

from core.i18n import gettext_lazy as _lazy


class TablesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.tables"
    label = "tables"
    verbose_name = _lazy("Столы")
