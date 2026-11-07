"""The Mini App addresses for buttons of this bot (PQ-048)."""

from __future__ import annotations

from apps.partners.mini_app import mini_app_link, staff_screen_link
from bot.services.runtime import get_runtime_config_by_token


def _instance_id(bot_token: str):
    try:
        return get_runtime_config_by_token(bot_token).bot_instance_id
    except LookupError:  # no registered bot (tests, emulator)
        return None


def mini_app_link_for(bot_token: str, partner_id) -> str:
    """The guests' Mini App; "" when it is not offered."""

    bot_instance_id = _instance_id(bot_token)
    return mini_app_link(bot_instance_id, partner_id) if bot_instance_id else ""


def staff_screen_link_for(bot_token: str, partner_id) -> str:
    """The staff screen (its own module); "" when it is not offered."""

    bot_instance_id = _instance_id(bot_token)
    return staff_screen_link(bot_instance_id, partner_id) if bot_instance_id else ""
