"""Kitchen and bar screen (PQ-048, stage 3): a shared tablet without Telegram.

The page ``/app/screen/`` gets the screen's secret after ``#`` in its link
(the part after ``#`` is not sent to the server, so it stays out of logs) and
sends it in the ``X-Screen-Token`` header. Every request finds the screen
again and checks it is switched on, the venue is active and the module is on.
The screen sees what to make — numbers, places, items and comments — and no
guest data: no phone, address, name or sums. Actions go through
``apps/orders/kitchen.py``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from functools import wraps

from django.db.models import Count, Max
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from apps.menu.models import MenuCategory
from apps.orders.kitchen import (
    DONE_MINUTES,
    SCREEN_STATUSES,
    KitchenScreenError,
    covered_stations,
    done_recently,
    item_station,
    mark_ready,
    note_seen,
    screen_by_token,
    screen_orders,
    start_order,
    stations_of,
)
from apps.orders.models import KitchenScreen, Order, OrderStationReady
from apps.orders.services import OrderFlowError, requested_time_label
from apps.partners.models import Partner, PartnerBotSettings
from core.i18n import N_, _, texts
from webapp.context import WebAppDenied
from webapp.views import _body, _error, _text

TOKEN_HEADER = "X-Screen-Token"

UNKNOWN = N_(
    "Экран не найден: ссылку заменили или экран выключили. Откройте новую ссылку из Admin."
)

KITCHEN_TEXTS = {
    "new": N_("Новые"),
    "preparing": N_("Готовятся"),
    "done": N_("Готово"),
    "start": N_("Взять в работу"),
    "ready": N_("Готово"),
    "minutes": N_("{minutes} мин"),
    "elsewhere": N_("Ещё в заказе"),
    "bar": N_("бар"),
    "kitchen": N_("кухня"),
    "part_done": N_("готово"),
    "nothing": N_("Заказов нет — новые появятся сами."),
    "sound_on": N_("🔔 Звук включён"),
    "sound_off": N_("🔕 Включить звук"),
    "fullscreen": N_("⛶ На весь экран"),
    "error": N_("Не получилось. Попробуйте ещё раз."),
    "offline": N_("Нет связи с сервером — пробуем снова…"),
    "gone": N_("Этого заказа на экране уже нет."),
    "retry": N_("Повторить"),
    "shows_all": N_("всё"),
    "shows_bar": N_("бар"),
    "shows_kitchen": N_("кухня"),
}


def kitchen_page(request):
    response = render(request, "webapp/kitchen.html")
    # Not shown inside Telegram: no one may frame it.
    response["Content-Security-Policy"] = "frame-ancestors 'none'"
    response["X-Frame-Options"] = "DENY"
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response


def _settings(partner: Partner) -> PartnerBotSettings | None:
    try:
        return partner.bot_settings
    except PartnerBotSettings.DoesNotExist:
        return None


def _screen(request) -> KitchenScreen:
    screen = screen_by_token(request.headers.get(TOKEN_HEADER, "").strip())
    if screen is None:
        raise WebAppDenied(401, UNKNOWN, code="unknown_screen")
    denied_language = screen.language
    if screen.partner.status != Partner.Status.ACTIVE:
        denied = WebAppDenied(403, N_("Заведение сейчас не принимает заказы."), code="suspended")
        denied.language = denied_language
        raise denied
    settings = _settings(screen.partner)
    if not (
        settings is not None
        and settings.module_kitchen_screen_enabled
        and settings.module_orders_enabled
    ):
        denied = WebAppDenied(
            403, N_("Экран кухни и бара выключен в настройках заведения."), code="module_off"
        )
        denied.language = denied_language
        raise denied
    return screen


def kitchen_api(method: str):
    def decorate(view):
        @csrf_exempt  # no cookies: the screen's secret in the header is the credential
        @wraps(view)
        def wrapper(request):
            if request.method != method:
                return _error(405, "method not allowed")
            try:
                body = _body(request)
                screen = _screen(request)
            except WebAppDenied as exc:
                with texts(exc.language):
                    return _error(exc.status, _(exc.message), exc.code)
            with texts(screen.language):
                try:
                    return JsonResponse(view(request, screen, body))
                except (KitchenScreenError, OrderFlowError) as exc:
                    return _error(400, str(exc), "refused")

        return wrapper

    return decorate


def _place(order: Order) -> str:
    if order.channel == Order.Channel.PICKUP:
        return _("Самовывоз")
    if order.channel == Order.Channel.DELIVERY:
        return _("Доставка")
    if order.channel == Order.Channel.SHIPPING:
        return _("Почта")
    if order.table_id is not None:
        return _("Стол {number}", number=order.table.number)
    return _("Вне стола")


def _item_json(item) -> dict:
    return {"name": item.item_name, "quantity": item.quantity, "comment": item.comment}


def _order_json(order: Order, screen: KitchenScreen, covered: set[str]) -> dict:
    mine = stations_of(screen.shows)
    marks = {mark.station for mark in order.station_marks.all()}
    items, elsewhere = [], []
    for item in order.items.all():
        station = item_station(item, covered)
        if station in mine:
            # On a screen for everything a part another screen marked shows as done.
            items.append({**_item_json(item), "done": station in marks})
        else:
            elsewhere.append({**_item_json(item), "station": station, "done": station in marks})
    return {
        "id": order.public_id,
        "status": order.status,
        "place": _place(order),
        "when": requested_time_label(order) if order.channel in (
            Order.Channel.PICKUP, Order.Channel.DELIVERY
        ) else "",
        "since": (order.accepted_at or order.created_at).isoformat(),
        "comment": order.comment,
        "items": items,
        "elsewhere": elsewhere,
    }


def _version(screen: KitchenScreen) -> str:
    """What the screen shows changes only with these: the orders on screens,
    the marks of the last minutes, where categories are made, the venue's
    screens (which places have one) and this screen's own settings."""

    orders = Order.objects.filter(
        partner_id=screen.partner_id, status__in=SCREEN_STATUSES
    ).aggregate(count=Count("id"), last=Max("updated_at"))
    marks = OrderStationReady.objects.filter(
        partner_id=screen.partner_id,
        created_at__gte=timezone.now() - timedelta(minutes=DONE_MINUTES),
    ).aggregate(count=Count("id"), last=Max("created_at"))
    categories = MenuCategory.objects.filter(partner_id=screen.partner_id).aggregate(
        last=Max("updated_at")
    )
    screens = KitchenScreen.objects.filter(partner_id=screen.partner_id).aggregate(
        last=Max("updated_at")
    )
    parts = [
        screen.id.hex, screen.shows, screen.language, screen.name, screen.updated_at,
        orders["count"], orders["last"], marks["count"], marks["last"], categories["last"],
        screens["last"], sorted(covered_stations(screen.partner_id)),
    ]
    return hashlib.sha1(json.dumps(parts, default=str).encode()).hexdigest()[:16]


@kitchen_api("GET")
def kitchen_state_view(request, screen: KitchenScreen, body: dict) -> dict:
    """Everything the screen shows; while nothing changed since ``?since=``,
    only ``{"unchanged": true}`` — the screen asks every 8 s."""

    note_seen(screen)
    version = _version(screen)
    if request.GET.get("since") == version:
        return {"unchanged": True, "version": version}
    covered = covered_stations(screen.partner_id)
    return {
        "version": version,
        "now": timezone.now().isoformat(),
        "screen": {
            "name": screen.name,
            "shows": screen.shows,
            "venue": screen.partner.name,
        },
        "texts": {key: _(msgid) for key, msgid in KITCHEN_TEXTS.items()},
        "orders": [
            _order_json(order, screen, covered) for order in screen_orders(screen, covered)
        ],
        "done": [
            {
                "id": mark.order.public_id,
                "place": _place(mark.order),
                "at": mark.created_at.isoformat(),
            }
            for mark in done_recently(screen)
        ],
    }


@kitchen_api("POST")
def kitchen_action_view(request, screen: KitchenScreen, body: dict) -> dict:
    public_id = _text(body, "order")
    action = _text(body, "action")
    try:
        if action == "start":
            start_order(screen, public_id)
        elif action == "ready":
            _order, became_ready = mark_ready(screen, public_id)
            return {"ok": True, "ready": became_ready}
        else:
            return {"stale": True}
    except KitchenScreenError:
        # The screen was out of date: show it as it is now.
        return {"stale": True}
    return {"ok": True}
