from apps.partners.models import BotInstance, Partner
from bot.services.runtime import get_runtime_config_by_token


class BotRuntimeDisabledError(Exception):
    """Raised when a polling bot was disabled after runtime startup."""


class PartnerSuspendedError(Exception):
    """Raised when a partner is no longer active for a live bot update."""


def resolve_partner_for_bot_token(bot_token: str) -> Partner:
    runtime_config = get_runtime_config_by_token(bot_token)
    try:
        bot_instance = BotInstance.objects.select_related("partner__bot_settings").get(
            id=runtime_config.bot_instance_id,
            partner_id=runtime_config.partner_id,
        )
    except BotInstance.DoesNotExist as exc:
        raise BotRuntimeDisabledError("Bot instance is no longer available.") from exc

    if not bot_instance.is_active:
        raise BotRuntimeDisabledError("Bot instance is disabled.")
    if bot_instance.partner.status != Partner.Status.ACTIVE:
        raise PartnerSuspendedError("Partner is not active.")
    return bot_instance.partner
