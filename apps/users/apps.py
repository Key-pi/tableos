from django.apps import AppConfig

from core.i18n import gettext_lazy as _lazy


class UsersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.users"
    label = "users"
    verbose_name = _lazy("Пользователи и гости")
