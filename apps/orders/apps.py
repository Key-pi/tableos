from django.apps import AppConfig

from core.i18n import gettext_lazy as _lazy


class OrdersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.orders"
    label = "orders"
    verbose_name = _lazy("Заказы")
