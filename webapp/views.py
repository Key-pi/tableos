"""Telegram Mini App for guests (PQ-048, stage 1): menu, cart, checkout.

An adapter like the bot: every action calls the services the bot calls
(``apps/orders/services.py`` for the cart and the table,
``apps/billing/remote_orders.py`` for pickup, delivery and shipping) with the
venue and the Telegram ID from the signed ``initData``
(``webapp/context.py``). Rules, locks and checks stay in those services.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal, InvalidOperation
from functools import wraps

from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import csrf_exempt

from apps.billing.remote_orders import (
    CHANNEL_TITLES,
    ON_RECEIPT,
    PREPAID,
    REMOTE_CHANNELS,
    TIMED_CHANNELS,
    carrier_title,
    carriers_for,
    closed_message,
    get_delivery_terms,
    get_remote_quote,
    get_shipping_terms,
    place_remote_order,
    save_guest_delivery_address,
    save_guest_phone,
    save_guest_shipping_carrier,
    save_guest_shipping_destination,
)
from apps.billing.services import BillingServiceError
from apps.bonuses.services import bonus_balance
from apps.menu.repositories import MenuItemRepository
from apps.orders.models import Order
from apps.orders.services import (
    OrderFlowError,
    add_menu_item_for_telegram_user,
    change_cart_item_quantity_for_telegram_user,
    checkout_active_cart_for_telegram_user,
    clear_active_cart_for_telegram_user,
    get_active_cart_for_telegram_user,
    ordering_channel_for_telegram_user,
    remove_cart_item_for_telegram_user,
    requested_time_label,
    switch_cart_channel_for_telegram_user,
)
from apps.partners.models import PartnerBotSettings
from apps.tables.services import get_active_table_session
from apps.users.models import GuestProfile
from bot.services.content import BotContent
from core.i18n import N_, _, texts
from webapp.context import GuestRequest, WebAppDenied, guest_request, points_of
from webapp.texts import ui_texts

# The bot's catalog texts (same entries, PQ-040).
SLOT_LABELS = {
    "now": N_("Как можно скорее"),
    "30": N_("Через 30 минут"),
    "60": N_("Через 1 час"),
}
ON_RECEIPT_WHERE = {
    Order.Channel.PICKUP: N_("на месте"),
    Order.Channel.DELIVERY: N_("курьеру"),
    Order.Channel.SHIPPING: N_("наложенным платежом"),
}
MAX_TEXT = 500


def _money(value) -> str:
    return f"{Decimal(value or 0):.2f}"


def _error(status: int, message: str, code: str = "") -> JsonResponse:
    return JsonResponse({"error": message, "code": code}, status=status)


def _denied(exc: WebAppDenied) -> JsonResponse:
    with texts(exc.language, exc.overrides):
        return _error(exc.status, _(exc.message), exc.code)


def _body(request) -> dict:
    if request.method != "POST" or not request.body:
        return {}
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise WebAppDenied(400, "bad json") from exc
    if not isinstance(data, dict):
        raise WebAppDenied(400, "bad json")
    return data


def _text(body: dict, key: str) -> str:
    value = body.get(key, "")
    return value.strip()[:MAX_TEXT] if isinstance(value, str) else ""


def guest_api(method: str, *, picks_point: bool = False):
    """An API view: method check, the guest from ``initData``, the guest's
    language, services' refusals as ``{"error": …}`` with status 400."""

    def decorate(view):
        @csrf_exempt  # no cookies: the signed header is the credential
        @wraps(view)
        def wrapper(request, bot_hex: str):
            if request.method != method:
                return _error(405, "method not allowed")
            try:
                body = _body(request)
                picked = None
                if picks_point:
                    try:
                        picked = uuid.UUID(hex=_text(body, "point"))
                    except ValueError as exc:
                        raise WebAppDenied(400, N_("Выберите заведение ещё раз.")) from exc
                guest = guest_request(request, bot_hex, picked_point=picked)
            except WebAppDenied as exc:
                return _denied(exc)
            with texts(guest.language, guest.overrides):
                try:
                    return JsonResponse(view(request, guest, body))
                except WebAppDenied as exc:
                    return _error(exc.status, _(exc.message), exc.code)
                except (OrderFlowError, BillingServiceError) as exc:
                    return _error(400, str(exc), "refused")

        return wrapper

    return decorate


# --- the page ------------------------------------------------------------------


@xframe_options_exempt  # Telegram Web shows Mini Apps in a frame
def guest_page(request, bot_hex: str) -> HttpResponse:
    response = render(request, "webapp/guest.html", {"bot_hex": bot_hex})
    response["Content-Security-Policy"] = (
        "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org"
    )
    response["Cache-Control"] = "no-store"
    return response


# --- state, venue, menu ----------------------------------------------------------


def _profile(guest: GuestRequest) -> GuestProfile | None:
    return (
        GuestProfile.objects.select_related("partner")
        .filter(partner=guest.venue(), telegram_account__telegram_id=guest.telegram_id)
        .first()
    )


def _cart_json(guest: GuestRequest) -> dict:
    partner = guest.venue()
    cart = get_active_cart_for_telegram_user(partner_id=partner.id, telegram_id=guest.telegram_id)
    channel = ordering_channel_for_telegram_user(
        partner_id=partner.id, telegram_id=guest.telegram_id
    )
    items = []
    if cart is not None:
        items = [
            {
                "item": cart_item.menu_item.public_id,
                "name": cart_item.item_name,
                "quantity": cart_item.quantity,
                "unit_price": _money(cart_item.unit_price),
                "total": _money(cart_item.unit_price * cart_item.quantity),
                "photo": cart_item.menu_item.photo_url,
            }
            for cart_item in cart.items.all()
        ]
    return {
        "channel": channel,
        "items": items,
        "count": sum(item["quantity"] for item in items),
        "total": _money(cart.total_amount if cart is not None else 0),
    }


def _ways(guest: GuestRequest, content: BotContent) -> list[dict]:
    partner = guest.venue()
    if not content.supports_cart():
        return []
    ways = []
    if content.supports_table_orders():
        session = get_active_table_session(partner_id=partner.id, telegram_id=guest.telegram_id)
        ways.append({
            "code": Order.Channel.TABLE,
            "title": "🍽 " + (_("Стол {number}", number=session.table.number) if session
                             else _("За столом")),
            "enabled": session is not None,
            "hint": "" if session else _("За столом — отсканируйте QR-код на столе."),
        })
    for code, supported, icon, label in (
        (Order.Channel.PICKUP, content.supports_pickup_orders(), "🥡",
         content.button_pickup_label),
        (Order.Channel.DELIVERY, content.supports_delivery_orders(), "🚚",
         content.button_delivery_label),
        (Order.Channel.SHIPPING, content.supports_shipping_orders(), "📦",
         content.button_shipping_label),
    ):
        if supported:
            ways.append({"code": code, "title": f"{icon} {label}", "enabled": True, "hint": ""})
    return ways


def _point_json(point) -> dict:
    return {"id": point.id.hex, "name": point.name, "address": point.address}


@guest_api("GET")
def state_view(request, guest: GuestRequest, body: dict) -> dict:
    points = points_of(guest)
    data = {
        "texts": ui_texts(),
        "points": [_point_json(point) for point in points] if len(points) > 1 else [],
        "needs_point": guest.partner is None,
    }
    if guest.partner is None:
        return data
    partner = guest.partner
    content = BotContent.for_partner(partner)
    profile = _profile(guest)
    data.update(
        venue={"name": partner.name, "address": partner.address},
        ways=_ways(guest, content),
        cart=_cart_json(guest),
        bonus_balance=(
            _money(bonus_balance(profile))
            if profile is not None and content.supports_loyalty() else None
        ),
        can_order=content.supports_cart(),
    )
    return data


@guest_api("POST", picks_point=True)
def point_view(request, guest: GuestRequest, body: dict) -> dict:
    guest.venue()  # the pick is saved by ``guest_request`` as in the bot
    return {"ok": True}


@guest_api("GET")
def menu_view(request, guest: GuestRequest, body: dict) -> dict:
    categories: dict = {}
    for item in MenuItemRepository.active_for_partner(guest.venue().id):
        category = categories.setdefault(
            item.category_id, {"id": item.category_id.hex, "name": item.category.name, "items": []}
        )
        category["items"].append({
            "id": item.public_id,
            "name": item.name,
            "description": item.description,
            "price": _money(item.price),
            "photo": item.photo_url,
            "sold_in": [channel for channel in item.CHANNEL_FIELDS if item.is_sold_in(channel)],
        })
    return {"categories": list(categories.values())}


# --- cart ----------------------------------------------------------------------


@guest_api("POST")
def channel_view(request, guest: GuestRequest, body: dict) -> dict:
    _cart, removed = switch_cart_channel_for_telegram_user(
        partner_id=guest.venue().id, telegram_id=guest.telegram_id, channel=_text(body, "channel")
    )
    return {"cart": _cart_json(guest), "removed": removed}


@guest_api("POST")
def cart_view(request, guest: GuestRequest, body: dict) -> dict:
    """``{"action": "add" | "change" | "remove" | "clear", "item": …, "delta": …}``."""

    partner_id, telegram_id = guest.venue().id, guest.telegram_id
    action, item = _text(body, "action"), _text(body, "item")
    if action == "add":
        add_menu_item_for_telegram_user(
            partner_id=partner_id, telegram_id=telegram_id, item_public_id=item
        )
    elif action == "change":
        delta = body.get("delta")
        if not isinstance(delta, int) or isinstance(delta, bool) or not -20 <= delta <= 20:
            raise WebAppDenied(400, N_("Количество должно изменяться хотя бы на 1."))
        change_cart_item_quantity_for_telegram_user(
            partner_id=partner_id, telegram_id=telegram_id, item_public_id=item, delta=delta
        )
    elif action == "remove":
        remove_cart_item_for_telegram_user(
            partner_id=partner_id, telegram_id=telegram_id, item_public_id=item
        )
    elif action == "clear":
        clear_active_cart_for_telegram_user(partner_id=partner_id, telegram_id=telegram_id)
    else:
        raise WebAppDenied(400, "unknown action")
    return {"cart": _cart_json(guest)}


# --- checkout ------------------------------------------------------------------


def _remote_channel(guest: GuestRequest) -> str:
    channel = ordering_channel_for_telegram_user(
        partner_id=guest.venue().id, telegram_id=guest.telegram_id
    )
    if channel is None:
        raise WebAppDenied(400, N_("Сначала выберите, как получите заказ."))
    return channel


def _yes(value) -> bool:
    return value in (True, "1", "true", 1)


@guest_api("GET")
def checkout_view(request, guest: GuestRequest, body: dict) -> dict:
    partner = guest.venue()
    channel = _remote_channel(guest)
    cart = _cart_json(guest)
    if channel == Order.Channel.TABLE:
        return {"channel": channel, "cart": cart, "total": cart["total"]}
    quote = get_remote_quote(
        partner_id=partner.id,
        telegram_id=guest.telegram_id,
        use_bonuses=_yes(request.GET.get("use_bonuses")),
        channel=channel,
        shipping_cost=request.GET.get("shipping_cost", ""),
    )
    settings = PartnerBotSettings.objects.get(partner=partner)
    profile = _profile(guest)
    data = {
        "channel": channel,
        "title": _(CHANNEL_TITLES[channel])[:1].upper() + _(CHANNEL_TITLES[channel])[1:],
        "cart": cart,
        "total": _money(quote.total),
        "items_total": _money(quote.items_total),
        "delivery_fee": _money(quote.delivery_fee),
        "bonus_available": _money(quote.bonus_available),
        "bonus_to_spend": _money(quote.bonus_to_spend),
        "to_pay": _money(quote.to_pay),
        "payment_details": quote.payment_details,
        "payment_modes": [
            {
                "code": mode,
                "label": _("💳 Переводом сейчас") if mode == PREPAID
                else _("💵 При получении ({where})", where=_(ON_RECEIPT_WHERE[channel])),
            }
            for mode in quote.payment_modes
        ],
        "phone": profile.phone if profile is not None else "",
        "slots": [{"code": slot, "label": _(SLOT_LABELS[slot])} for slot in quote.slots],
        "closed": (
            closed_message(partner, channel)
            if channel in TIMED_CHANNELS and not quote.slots else ""
        ),
    }
    if channel == Order.Channel.DELIVERY:
        terms = get_delivery_terms(partner.id)
        data.update(
            delivery_address=quote.delivery_address,
            terms=terms.describe(),
        )
    if channel == Order.Channel.SHIPPING:
        terms = get_shipping_terms(partner.id)
        data.update(
            terms=terms.describe(),
            carriers=[
                {"code": carrier, "label": carrier_title(carrier)}
                for carrier in carriers_for(settings)
            ],
            shipping_carrier=quote.shipping_carrier,
            shipping_destination=quote.shipping_destination,
            shipping_cost=quote.shipping_cost,
            shipping_cost_options=[
                {
                    "code": cost,
                    "label": _("📮 На почте по тарифу перевозчика")
                    if cost == Order.ShippingCost.CARRIER
                    else _("📦 В заказе: {fixed_label}", fixed_label=terms.fixed_label()),
                }
                for cost in quote.shipping_cost_options
            ],
        )
    return data


@guest_api("POST")
def details_view(request, guest: GuestRequest, body: dict) -> dict:
    """The guest's phone and where the order goes; saved as in the bot."""

    partner_id, telegram_id = guest.venue().id, guest.telegram_id
    channel = _remote_channel(guest)
    save_guest_phone(partner_id=partner_id, telegram_id=telegram_id, phone=_text(body, "phone"))
    if channel == Order.Channel.DELIVERY:
        save_guest_delivery_address(
            partner_id=partner_id, telegram_id=telegram_id, address=_text(body, "address")
        )
    if channel == Order.Channel.SHIPPING:
        save_guest_shipping_carrier(
            partner_id=partner_id, telegram_id=telegram_id, carrier=_text(body, "carrier")
        )
        save_guest_shipping_destination(
            partner_id=partner_id, telegram_id=telegram_id, text=_text(body, "destination")
        )
    return {"ok": True}


def _amount(body: dict, key: str) -> Decimal:
    try:
        return Decimal(str(body.get(key, "")))
    except InvalidOperation as exc:
        raise WebAppDenied(400, N_("Проверьте сумму ещё раз.")) from exc


def _placed_text(order: Order, to_pay: Decimal) -> str:
    """What the bot says after placing (``bot/handlers/remote_checkout.py``)."""

    title = _(CHANNEL_TITLES[order.channel])
    time_label = requested_time_label(order)
    if order.channel == Order.Channel.DELIVERY:
        time_label = f"{time_label}, {order.delivery_address}"
    if order.channel == Order.Channel.SHIPPING:
        time_label = f"{order.get_shipping_carrier_display()}, {order.shipping_destination}"
    work = _("собирать") if order.channel == Order.Channel.SHIPPING else _("готовить")
    if to_pay > 0 and order.payment_timing == ON_RECEIPT:
        how = (
            _("наложенным платежом на почте")
            if order.channel == Order.Channel.SHIPPING
            else _("при получении")
        )
        return _(
            "Заказ #{public_id} ({title}) оформлен ({time_label}). Оплата {how}: "
            "{to_pay} грн. Пришлём сообщение, когда начнём {work}.",
            public_id=order.public_id, title=title, time_label=time_label, how=how,
            to_pay=to_pay, work=work,
        )
    if to_pay <= 0:
        return _(
            "Заказ #{public_id} ({title}) оформлен и оплачен бонусами ({time_label}). "
            "Заведение скоро возьмёт его в работу.",
            public_id=order.public_id, title=title, time_label=time_label,
        )
    return _(
        "Заказ #{public_id} ({title}) оформлен ({time_label}). Как только "
        "заведение подтвердит оплату, начнём {work} — пришлём сообщение.",
        public_id=order.public_id, title=title, time_label=time_label, work=work,
    )


@guest_api("POST")
def place_view(request, guest: GuestRequest, body: dict) -> dict:
    partner = guest.venue()
    channel = _remote_channel(guest)
    if channel == Order.Channel.TABLE:
        order = checkout_active_cart_for_telegram_user(
            partner_id=partner.id, telegram_id=guest.telegram_id
        )
        return {
            "order": order.public_id,
            "text": _(
                "Заказ #{public_id} принят: {total} грн. Статус — «{status}».",
                public_id=order.public_id, total=order.total_amount,
                status=order.get_status_display(),
            ),
        }
    if channel not in REMOTE_CHANNELS:
        raise WebAppDenied(400, N_("Этот способ заказа пока недоступен."))
    expected_to_pay = _amount(body, "expected_to_pay")
    order = place_remote_order(
        partner_id=partner.id,
        telegram_id=guest.telegram_id,
        slot=_text(body, "slot"),
        use_bonuses=_yes(body.get("use_bonuses")),
        expected_total=_amount(body, "expected_total"),
        expected_to_pay=expected_to_pay,
        channel=channel,
        payment=_text(body, "payment") or PREPAID,
        shipping_cost=_text(body, "shipping_cost"),
    )
    return {"order": order.public_id, "text": _placed_text(order, expected_to_pay)}
