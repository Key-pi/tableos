"""Kitchen and bar screens (PQ-048, stage 3): accepted orders to make.

A screen is a shared tablet without Telegram; it opens by a link from Admin
and does two things only: «Взять в работу» (``accepted`` → ``preparing``) and
«Готово». Statuses change through ``transition_order_status``, as from the
bot, so the guest and the staff are told the same way.

Split screens. A category of the menu says where it is made
(``MenuCategory.station``: bar, kitchen, or not said). A screen shows all,
only the bar or only the kitchen; a part not said is on every screen, and so
is a part of a place no working screen shows (``covered_stations``). On a
split screen «Готово» marks this screen's part (``OrderStationReady``) and
the order leaves it; the order becomes «Готов» when every part it has is
marked, so the guest gets one message when everything is made.

Locks: the order row only (``Order``), taken before reading the marks, so
two screens pressing at once see each other's mark.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone

from apps.menu.models import MenuCategory
from apps.orders.models import KitchenScreen, Order, OrderItem, OrderStationReady
from apps.orders.services import transition_order_status
from core.i18n import _

SCREEN_STATUSES = (Order.Status.ACCEPTED, Order.Status.PREPARING)
# Done parts stay in the «Готово» column this long.
DONE_MINUTES = 15
# «Последний раз на связи» is written at most this often.
SEEN_EVERY = timedelta(seconds=60)

ALL = KitchenScreen.Shows.ALL
ANY = MenuCategory.Station.ANY


class KitchenScreenError(Exception):
    """The order is not on this screen any more (out-of-date screen)."""


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_screen_link(screen: KitchenScreen) -> str:
    """A new secret for the screen's link; the old link stops working. Only
    the hash is kept, so the link is shown once (Admin)."""

    token = secrets.token_urlsafe(24)
    screen.token_hash = _hash(token)
    screen.link_issued_at = timezone.now()
    screen.save(update_fields=["token_hash", "link_issued_at", "updated_at"])
    return token


def screen_by_token(token: str) -> KitchenScreen | None:
    if not token or len(token) > 100:
        return None
    return (
        KitchenScreen.objects.select_related("partner__bot_settings")
        .filter(token_hash=_hash(token), is_active=True)
        .first()
    )


def note_seen(screen: KitchenScreen) -> None:
    now = timezone.now()
    if screen.last_seen_at is None or now - screen.last_seen_at >= SEEN_EVERY:
        screen.last_seen_at = now
        screen.save(update_fields=["last_seen_at"])


BAR = MenuCategory.Station.BAR
KITCHEN = MenuCategory.Station.KITCHEN


def covered_stations(partner_id) -> set[str]:
    """The places that have a working screen. A place without one counts as
    «не указано» (owner, 2026-11-06): its items are on every screen and do
    not hold the order back."""

    covered: set[str] = set()
    shows = KitchenScreen.objects.filter(partner_id=partner_id, is_active=True)
    for value in shows.values_list("shows", flat=True):
        covered |= {BAR, KITCHEN} if value == ALL else {value}
    return covered


def uncovered_categories(partner_id) -> dict[str, list[str]]:
    """Categories marked for a place no working screen shows (for Admin)."""

    covered = covered_stations(partner_id)
    found: dict[str, list[str]] = {}
    categories = MenuCategory.objects.filter(
        partner_id=partner_id, is_active=True, station__in=[BAR, KITCHEN]
    ).exclude(station__in=covered).order_by("sort_order", "name")
    for category in categories:
        found.setdefault(category.station, []).append(category.name)
    return found


def missing_screen_warnings(partner_id) -> list[str]:
    """For Admin: places marked on categories but without a working screen,
    while the module is on."""

    from apps.partners.models import PartnerBotSettings

    if partner_id is None:
        return []
    settings = PartnerBotSettings.objects.filter(partner_id=partner_id).first()
    if settings is None or not settings.module_kitchen_screen_enabled:
        return []
    places = {BAR: _("бара"), KITCHEN: _("кухни")}
    return [
        _(
            "Нет работающего экрана {place}: позиции категорий «{names}» показываются на "
            "всех экранах и не задерживают «Готов». Добавьте экран или уберите отметку "
            "«Где готовят».",
            place=places[station], names="», «".join(names),
        )
        for station, names in uncovered_categories(partner_id).items()
    ]


def stations_of(shows: str) -> set[str]:
    """The parts of an order a screen makes."""

    if shows == ALL:
        return {ANY, MenuCategory.Station.BAR, MenuCategory.Station.KITCHEN}
    return {ANY, shows}


def item_station(item: OrderItem, covered: set[str]) -> str:
    # A subcategory without its own place takes its parent's (catalog, 2026-11-07).
    station = item.menu_item.category.effective_station if item.menu_item_id else ANY
    return station if station in covered else ANY


def _is_complete(items: list[OrderItem], marks: set[str], covered: set[str]) -> bool:
    if ALL in marks:
        return True
    needed = {item_station(item, covered) for item in items} - {ANY}
    return bool(marks) and needed <= marks


def _items(queryset=None):
    return Prefetch(
        "items",
        queryset=(queryset or OrderItem.objects)
        .filter(kind=OrderItem.Kind.ITEM)
        .select_related("menu_item__category__parent"),
    )


def screen_orders(screen: KitchenScreen, covered: set[str]) -> list[Order]:
    """Accepted orders with a part this screen makes and has not marked."""

    orders = (
        Order.objects.filter(partner_id=screen.partner_id, status__in=SCREEN_STATUSES)
        .exclude(station_marks__station__in=[screen.shows, ALL])
        .select_related("partner", "table")
        .prefetch_related(_items(), "station_marks")
        .order_by("accepted_at", "created_at")
    )
    mine = stations_of(screen.shows)
    return [
        order for order in orders
        if any(item_station(item, covered) in mine for item in order.items.all())
    ]


def done_recently(screen: KitchenScreen):
    since = timezone.now() - timedelta(minutes=DONE_MINUTES)
    return (
        OrderStationReady.objects.filter(
            partner_id=screen.partner_id, station=screen.shows, created_at__gte=since
        )
        .select_related("order__table", "order__partner")
        .order_by("-created_at")[:30]
    )


def _locked_order(screen: KitchenScreen, public_id: str) -> Order:
    order = (
        Order.objects.select_for_update()
        .filter(partner_id=screen.partner_id, public_id=public_id)
        .first()
    )
    if order is None or order.status not in SCREEN_STATUSES:
        raise KitchenScreenError(_("Этого заказа на экране уже нет."))
    return order


def _note(screen: KitchenScreen) -> str:
    return f"🖥 {screen.name}"


@transaction.atomic
def start_order(screen: KitchenScreen, public_id: str) -> Order:
    """«Взять в работу»: ``accepted`` → ``preparing``; already preparing — nothing."""

    order = _locked_order(screen, public_id)
    if order.status == Order.Status.ACCEPTED:
        order = transition_order_status(
            order=order, to_status=Order.Status.PREPARING, note=_note(screen)
        )
    return order


@transaction.atomic
def mark_ready(screen: KitchenScreen, public_id: str) -> tuple[Order, bool]:
    """«Готово» for this screen's part; the order becomes «Готов» when all its
    parts are done. Returns the order and whether it became ready. Pressing
    again changes nothing."""

    order = _locked_order(screen, public_id)
    items = list(
        OrderItem.objects.filter(order=order, kind=OrderItem.Kind.ITEM)
        .select_related("menu_item__category__parent")
    )
    covered = covered_stations(screen.partner_id)
    mine = stations_of(screen.shows)
    if not any(item_station(item, covered) in mine for item in items):
        raise KitchenScreenError(_("Этого заказа на экране уже нет."))
    OrderStationReady.objects.get_or_create(
        order=order,
        station=screen.shows,
        defaults={"partner_id": order.partner_id, "screen": screen},
    )
    marks = set(
        OrderStationReady.objects.filter(order=order).values_list("station", flat=True)
    )
    if not _is_complete(items, marks, covered):
        return order, False
    order = transition_order_status(
        order=order, to_status=Order.Status.READY, note=_note(screen)
    )
    return order, True
