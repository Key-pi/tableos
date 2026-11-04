"""Networks of venues (PQ-047, stage 1): which venues one Admin login reaches.

A venue stays a partner: data, rights and locks keep working per venue.
The network adds only the list of venues a person may switch between in
Admin and act for:

- the owner of a network (``AdminAccessProfile.is_partner_owner`` of a venue
  in it) — every venue of that network;
- anyone else — their own venue plus ``AdminAccessProfile.extra_partners``
  that are in the same network (a venue moved to another network stops
  counting at once);
- a venue without a network — only itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone

from apps.partners.models import Partner
from apps.users.models import AdminAccessProfile


def _active_admin_profile(user) -> AdminAccessProfile | None:
    if user is None or not getattr(user, "is_authenticated", False) or not user.is_active:
        return None
    return (
        AdminAccessProfile.objects.select_related("partner")
        .filter(user=user, is_active=True, can_access_admin=True)
        .first()
    )


def admin_partner_ids(user) -> list:
    """Venues this person may open in Admin, their own venue first."""

    profile = _active_admin_profile(user)
    if profile is None:
        return []
    home = profile.partner
    if home.network_id is None:
        return [home.id]
    others = Partner.objects.filter(network_id=home.network_id).exclude(id=home.id)
    if not profile.is_partner_owner:
        others = others.filter(id__in=profile.extra_partners.values("id"))
    return [home.id, *others.order_by("name").values_list("id", flat=True)]


def acts_for_partner(user, partner_id) -> bool:
    """Whether a back-office actor may act for this venue: its own staff, or
    the network's owner/manager who was given it (PQ-047)."""

    if user is None or not getattr(user, "is_active", False):
        return False
    if user.partner_id is not None and str(user.partner_id) == str(partner_id):
        return True
    return any(str(point) == str(partner_id) for point in admin_partner_ids(user))


def owns_partner(user, partner_id) -> bool:
    """The owner of this venue or of the network it is in."""

    profile = _active_admin_profile(user)
    if profile is None or not profile.is_partner_owner:
        return False
    return any(str(point) == str(partner_id) for point in admin_partner_ids(user))


# --- stage 2: one bot for the network ---------------------------------------
#
# A bot with ``BotInstance.serves_network`` answers for every active venue
# of its venue's network. The venue of an update is, in this order:
#   1. the venue of the message whose button was pressed or that was replied
#      to (``BotMessagePoint``: a staff member's notification of venue B
#      works while they browse venue A);
#   2. a venue the guest just picked (``point:<hex>``) or that a /start link
#      names (a table QR, a staff invite, a source code only one venue has) —
#      saved as the guest's choice;
#   3. the guest's saved choice (``NetworkGuestChoice``);
#   4. the only active venue, when there is one.
# Otherwise the guest is asked to pick (``UpdatePoint.partner`` is None).


@dataclass(frozen=True)
class UpdatePoint:
    partner: Partner | None
    points: list  # active venues of the network, by name
    # The venue was found by 1. or 2. above (the guest's choice is not
    # changed by 1.).
    explicit: bool = False


def network_points(network_id) -> list:
    return list(
        Partner.objects.filter(network_id=network_id, status=Partner.Status.ACTIVE)
        .select_related("bot_settings")
        .order_by("name")
    )


def serves_network(bot_instance) -> bool:
    return bool(bot_instance.serves_network and bot_instance.partner.network_id)


def bot_for_partner(partner_id, *, active_only: bool = True):
    """The bot that speaks for this venue: its own, else its network's."""

    from apps.partners.models import BotInstance

    bots = BotInstance.objects.select_related("partner")
    if active_only:
        bots = bots.filter(is_active=True)
    own = bots.filter(partner_id=partner_id).order_by("created_at").first()
    if own is not None:
        return own
    network_id = (
        Partner.objects.filter(id=partner_id).values_list("network_id", flat=True).first()
    )
    if network_id is None:
        return None
    return (
        bots.filter(serves_network=True, partner__network_id=network_id)
        .order_by("created_at")
        .first()
    )


def _save_choice(network_id, telegram_id: int, partner) -> None:
    from apps.partners.models import NetworkGuestChoice
    from apps.users.models import TelegramAccount

    account, _created = TelegramAccount.objects.get_or_create(telegram_id=telegram_id)
    NetworkGuestChoice.objects.update_or_create(
        network_id=network_id, telegram_account=account, defaults={"partner": partner}
    )


def resolve_update_point(
    bot_instance,
    *,
    telegram_id: int | None,
    chat_id: int | None = None,
    message_id: int | None = None,
    picked_partner_id=None,
    pick_from_link=None,
) -> UpdatePoint:
    """The venue of one update of a network's bot (see the order above).

    ``pick_from_link(points)`` returns the venue a /start link names; the
    bot adapter owns parsing links (tables, invites, sources)."""

    from apps.partners.models import BotMessagePoint, NetworkGuestChoice

    network_id = bot_instance.partner.network_id
    points = network_points(network_id)
    by_id = {point.id: point for point in points}
    every_point = {
        point.id: point
        for point in Partner.objects.filter(network_id=network_id).select_related("bot_settings")
    }

    if chat_id is not None and message_id is not None:
        partner_id = (
            BotMessagePoint.objects.filter(
                bot_instance=bot_instance, chat_id=chat_id, message_id=message_id
            )
            .values_list("partner_id", flat=True)
            .first()
        )
        if partner_id in every_point:
            # Even a suspended venue: its message answers that it is suspended.
            return UpdatePoint(every_point[partner_id], points, explicit=True)

    chosen = None
    if picked_partner_id is not None:
        chosen = by_id.get(picked_partner_id)
    if chosen is None and pick_from_link is not None:
        chosen = pick_from_link(points)
    if chosen is not None:
        if telegram_id is not None:
            _save_choice(network_id, telegram_id, chosen)
        return UpdatePoint(chosen, points, explicit=True)

    if telegram_id is not None:
        partner_id = (
            NetworkGuestChoice.objects.filter(
                network_id=network_id, telegram_account__telegram_id=telegram_id
            )
            .values_list("partner_id", flat=True)
            .first()
        )
        if partner_id in every_point:
            return UpdatePoint(every_point[partner_id], points)
    if len(points) == 1:
        return UpdatePoint(points[0], points)
    return UpdatePoint(None, points)


def remember_message_point(bot_instance_id, chat_id: int, message_id: int, partner_id) -> None:
    from apps.partners.models import BotMessagePoint

    BotMessagePoint.objects.get_or_create(
        bot_instance_id=bot_instance_id,
        chat_id=chat_id,
        message_id=message_id,
        defaults={"partner_id": partner_id},
    )


# Buttons older than this act for the guest's chosen venue instead.
MESSAGE_POINT_DAYS = 90


def forget_old_message_points() -> int:
    from apps.partners.models import BotMessagePoint

    since = timezone.now() - timedelta(days=MESSAGE_POINT_DAYS)
    deleted, _detail = BotMessagePoint.objects.filter(created_at__lt=since).delete()
    return deleted
