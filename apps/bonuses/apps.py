from django.apps import AppConfig

from core.i18n import gettext_lazy as _lazy


class BonusesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.bonuses"
    label = "bonuses"
    verbose_name = _lazy("Бонусы")
