"""The Mini App address for buttons of this bot (PQ-048)."""

from __future__ import annotations

from apps.partners.mini_app import mini_app_link
from bot.services.runtime import get_runtime_config_by_token


def mini_app_link_for(bot_token: str, partner_id, page: str = "") -> str:
    """``page="staff/"`` — the staff screen; "" when the Mini App is not offered."""

    try:
        bot_instance_id = get_runtime_config_by_token(bot_token).bot_instance_id
    except LookupError:  # no registered bot (tests, emulator)
        return ""
    return mini_app_link(bot_instance_id, partner_id, page)
