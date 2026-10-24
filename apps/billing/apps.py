from django.apps import AppConfig

from core.i18n import gettext_lazy as _lazy


class BillingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.billing"
    label = "billing"
    verbose_name = _lazy("Счета и оплаты")

