from django.apps import AppConfig

from core.i18n import gettext_lazy as _lazy


class PartnersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.partners"
    label = "partners"
    verbose_name = _lazy("Заведения")
