"""One bot for a network of venues (PQ-047, stage 2): the bot side.

``bot/middlewares/partner_runtime.py`` asks ``network_update_point`` which
venue an update is for and then runs the handler as if the bot were that
venue's own bot (``partner_for_this_update``): every handler keeps calling
``resolve_partner_for_bot_token``. The rules of the choice live in
``apps/partners/networks.py``; parsing /start links lives here, next to the
handlers that own those links.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from apps.partners.models import BotInstance, Partner
from apps.partners.networks import UpdatePoint, resolve_update_point, serves_network
from bot.services.runtime import get_runtime_config_by_token
from core.i18n import _

POINT_CALLBACK_PREFIX = "point:"

# The network bot of the update being handled: (bot instance id, how many
# venues the guest can pick from). Read by the main keyboard and by the
# request middleware that remembers which venue a sent message is for.
_update_network: ContextVar[tuple[str, int, str] | None] = ContextVar(
    "update_network", default=None
)


@dataclass(frozen=True)
class UpdateFacts:
    """What the middleware read from a Telegram update."""

    telegram_id: int | None = None
    chat_id: int | None = None
    message_id: int | None = None  # of the message pressed or replied to
    callback_data: str = ""
    start_payload: str = ""


@contextmanager
def network_update(bot_instance_id, points_count: int, partner_id):
    reset = _update_network.set((str(bot_instance_id), points_count, str(partner_id)))
    try:
        yield
    finally:
        _update_network.reset(reset)


def update_points_count() -> int:
    current = _update_network.get()
    return current[1] if current is not None else 0


def update_message_point() -> tuple[str, str] | None:
    """(bot instance id, venue id) for messages sent in this update."""

    current = _update_network.get()
    return (current[0], current[2]) if current is not None else None


def point_from_start_link(points, payload: str):
    """The venue a /start link names among the network's active venues: a
    table QR, a staff invite, or a source code only one venue has."""

    from apps.analytics.models import TrafficSource
    from apps.analytics.sources import parse_source_payload
    from apps.employees.invites import parse_invite_payload
    from apps.employees.models import StaffInvite
    from apps.tables.models import Table

    if not payload:
        return None
    ids = [point.id for point in points]
    by_id = {point.id: point for point in points}
    code = parse_invite_payload(payload)
    if code is not None:
        return by_id.get(
            StaffInvite.objects.filter(code=code, partner_id__in=ids)
            .values_list("partner_id", flat=True)
            .first()
        )
    source = parse_source_payload(payload)
    if source is not None:
        found = list(
            # A network-wide source names no venue: the guest picks.
            TrafficSource.objects.filter(code=source, partner_id__in=ids, whole_network=False)
            .values_list("partner_id", flat=True)[:2]
        )
        return by_id.get(found[0]) if len(found) == 1 else None
    parts = payload.split("_", 2)
    if len(parts) == 3 and parts[0] == "table" and parts[1].isdigit():
        return by_id.get(
            Table.objects.filter(
                partner_id__in=ids, number=int(parts[1]), qr_token=parts[2], is_active=True
            )
            .values_list("partner_id", flat=True)
            .first()
        )
    return None


def _picked(callback_data: str):
    if not callback_data.startswith(POINT_CALLBACK_PREFIX):
        return None
    try:
        from uuid import UUID

        return UUID(hex=callback_data[len(POINT_CALLBACK_PREFIX):])
    except ValueError:
        return None


def network_update_point(
    bot_token: str, facts: UpdateFacts
) -> tuple[BotInstance, UpdatePoint] | None:
    """The venue of this update when the bot serves a network; None for a
    venue's own bot (the usual path). Raises like ``resolve_partner_for_bot_token``
    when the bot was switched off."""

    from bot.services.context import BotRuntimeDisabledError

    config = get_runtime_config_by_token(bot_token)
    bot_instance = (
        BotInstance.objects.select_related("partner").filter(id=config.bot_instance_id).first()
    )
    if bot_instance is None or not bot_instance.is_active:
        raise BotRuntimeDisabledError("Bot instance is no longer available.")
    if not serves_network(bot_instance):
        return None
    # A button of a venue-choice message is the guest's pick, not "the
    # venue this message was sent for".
    picked = _picked(facts.callback_data)
    point = resolve_update_point(
        bot_instance,
        telegram_id=facts.telegram_id,
        chat_id=facts.chat_id if picked is None else None,
        message_id=facts.message_id if picked is None else None,
        picked_partner_id=picked,
        pick_from_link=(
            (lambda points: point_from_start_link(points, facts.start_payload))
            if facts.start_payload
            else None
        ),
        start_payload=facts.start_payload,
    )
    return bot_instance, point


def point_choice_markup(points: list[Partner]) -> InlineKeyboardMarkup:
    rows = []
    for point in points:
        label = f"{point.name} — {point.address}" if point.address else point.name
        rows.append(
            [
                InlineKeyboardButton(
                    text=label[:64], callback_data=f"{POINT_CALLBACK_PREFIX}{point.id.hex}"
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def point_choice_text(button_point_label: str) -> str:
    from bot.utils.html import escape_html

    return _(
        "📍 В какое заведение? Выбор запомнится, сменить можно кнопкой «{button_point_label}».",
        button_point_label=escape_html(button_point_label),
    )
