"""Who opens the Mini App and for which venue (PQ-048).

Every API request carries Telegram's signed ``initData``; it is checked with
the token of the bot whose address is opened. The venue is the bot's own, or
for a network's bot the guest's choice, as in the bot
(``apps/partners/networks.py:resolve_update_point``). The bot, the venue and
the Mini App module are checked on every request (AGENTS.md: a suspended
venue is decided at the moment of the request).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from apps.partners.mini_app import mini_app_on
from apps.partners.models import BotInstance, Partner
from apps.partners.networks import network_points, resolve_update_point, serves_network
from apps.users.services import get_or_create_guest_profile_with_created
from bot.services.language import language_context
from core.i18n import N_
from webapp.auth import InitDataError, TelegramUser, validate_init_data

INIT_DATA_HEADER = "X-Telegram-Init-Data"
# The guest's language is not known yet: these are in the platform's language.
GONE = N_("Это меню больше не работает. Откройте меню из бота ещё раз.")
REOPEN = N_("Откройте меню из бота ещё раз.")


class WebAppDenied(Exception):
    """The request is refused; ``message`` is a catalog text, translated in
    the guest's language when it is known (``language``)."""

    def __init__(self, status: int, message: str, *, code: str = ""):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code
        self.language = ""
        self.overrides: dict = {}
        # Other venues of the network the guest may pick instead.
        self.points: list = []


@dataclass
class GuestRequest:
    bot: BotInstance
    user: TelegramUser
    partner: Partner | None  # None: the guest of a network picks a venue first
    points: list = field(default_factory=list)  # venues of the network's bot
    language: str = ""
    overrides: dict = field(default_factory=dict)

    @property
    def telegram_id(self) -> int:
        return self.user.id

    def venue(self) -> Partner:
        if self.partner is None:
            raise WebAppDenied(409, N_("Сначала выберите заведение."), code="pick_point")
        return self.partner


def find_bot(bot_hex: str) -> BotInstance:
    try:
        bot_id = uuid.UUID(hex=bot_hex)
    except ValueError as exc:
        raise WebAppDenied(404, GONE) from exc
    bot = BotInstance.objects.select_related("partner").filter(id=bot_id, is_active=True).first()
    if bot is None:
        raise WebAppDenied(404, GONE)
    return bot


def guest_request(request, bot_hex: str, *, picked_point=None) -> GuestRequest:
    bot = find_bot(bot_hex)
    try:
        user = validate_init_data(request.headers.get(INIT_DATA_HEADER, ""), bot.token)
    except InitDataError as exc:
        raise WebAppDenied(401, REOPEN, code="unauthorized") from exc

    if serves_network(bot):
        point = resolve_update_point(
            bot, telegram_id=user.id, picked_partner_id=picked_point
        )
        partner, points = point.partner, point.points
    else:
        partner, points = bot.partner, []

    speaking_for = partner or bot.partner
    language, overrides = language_context(speaking_for, user.id, user.language_code)
    guest = GuestRequest(
        bot=bot, user=user, partner=partner, points=points,
        language=language, overrides=overrides,
    )
    if partner is not None:
        denied = None
        if partner.status != Partner.Status.ACTIVE:
            denied = WebAppDenied(
                403, N_("Заведение сейчас не принимает заказы."), code="suspended"
            )
        elif not mini_app_on(partner.id):
            denied = WebAppDenied(
                403, N_("Заказ в приложении здесь пока не включён — закажите в чате бота."),
                code="module_off",
            )
        if denied is not None:
            denied.language, denied.overrides = language, overrides
            denied.points = [point for point in points if point.id != partner.id]
            raise denied
        get_or_create_guest_profile_with_created(
            partner_id=partner.id,
            telegram_id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            language_code=user.language_code,
        )
    return guest


def points_of(guest: GuestRequest) -> list:
    if guest.points:
        return guest.points
    return network_points(guest.bot.partner.network_id) if serves_network(guest.bot) else []
