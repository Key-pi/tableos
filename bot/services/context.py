from apps.partners.models import Partner
from bot.services.runtime import get_runtime_config_by_token


def resolve_partner_for_bot_token(bot_token: str) -> Partner:
    runtime_config = get_runtime_config_by_token(bot_token)
    return Partner.objects.select_related("bot_settings").get(id=runtime_config.partner_id)
