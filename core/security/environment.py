"""Which settings module the process runs with (AA-038)."""

from django.conf import settings

PRODUCTION_SETTINGS_MODULE = "core.settings.production"


def is_production() -> bool:
    return getattr(settings, "SETTINGS_MODULE", "") == PRODUCTION_SETTINGS_MODULE
