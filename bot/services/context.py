from contextlib import contextmanager
from contextvars import ContextVar

from apps.partners.models import BotInstance, Partner
from bot.services.runtime import get_runtime_config_by_token

# The partner checked at the start of the update being handled (AA-047).
_update_partner: ContextVar[tuple[str, Partner] | None] = ContextVar(
    "update_partner", default=None
)


class BotRuntimeDisabledError(Exception):
    """Raised when a polling bot was disabled after runtime startup."""


class PartnerSuspendedError(Exception):
    """Raised when a partner is no longer active for a live bot update."""

    partner: Partner | None = None


@contextmanager
def partner_for_this_update(bot_token: str, partner: Partner):
    """Let handlers of this update reuse the partner checked at its start.

    The next update checks the bot and partner again, so a suspension or a
    disabled bot applies from the next update on, as before.
    """

    reset = _update_partner.set((bot_token, partner))
    try:
        yield
    finally:
        _update_partner.reset(reset)


def load_update_bot(bot_token: str) -> BotInstance:
    """The bot of this token with its partner and settings, one query; raises
    ``BotRuntimeDisabledError`` when it was switched off or removed."""

    runtime_config = get_runtime_config_by_token(bot_token)
    bot_instance = (
        BotInstance.objects.select_related("partner__bot_settings")
        .filter(id=runtime_config.bot_instance_id)
        .first()
    )
    if bot_instance is None:
        raise BotRuntimeDisabledError("Bot instance is no longer available.")
    if not bot_instance.is_active:
        raise BotRuntimeDisabledError("Bot instance is disabled.")
    return bot_instance


def checked_partner(bot_token: str, bot_instance: BotInstance) -> Partner:
    """The venue of an ordinary (not a network's) bot, still active."""

    runtime_config = get_runtime_config_by_token(bot_token)
    if str(bot_instance.partner_id) != str(runtime_config.partner_id):
        raise BotRuntimeDisabledError("Bot instance is no longer available.")
    if bot_instance.partner.status != Partner.Status.ACTIVE:
        error = PartnerSuspendedError("Partner is not active.")
        error.partner = bot_instance.partner
        raise error
    return bot_instance.partner


def resolve_partner_for_bot_token(bot_token: str) -> Partner:
    checked = _update_partner.get()
    if checked is not None and checked[0] == bot_token:
        return checked[1]
    return checked_partner(bot_token, load_update_bot(bot_token))
