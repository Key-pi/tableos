from django.apps import AppConfig

from core.i18n import gettext_lazy as _lazy


class BotConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "bot"
    verbose_name = _lazy("Telegram-бот")
