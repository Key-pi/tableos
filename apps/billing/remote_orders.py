"""Remote orders: quote, place, take the payment (transfer or on receipt).

One flow for every channel where the guest is not at a table (ADR-0009):
pickup, delivery and shipping by post. The channel only changes its own
details (pickup: when; delivery: when, where and the delivery fee; shipping:
carrier, destination, postage and the ТТН) — the order, bill and payment are
the same models for every channel.

Shipping (PQ-034, docs/architecture/SHIPPING.md): the owner turns on the
carriers, the postage options (by the carrier's tariff, paid by the recipient
at the post office, or a fixed price in the order) and the payment options
(transfer in advance, cash on delivery); the guest picks among them. There is
no carrier API: staff send the parcel and enter the ТТН, the guest gets it
with a tracking link. Orders are accepted at any time (no opening hours).

Delivery (PQ-032, docs/architecture/DELIVERY.md): the guest gives an address
as text (a Telegram location is optional), the last one is remembered; the
fee is the venue's fixed price, free from a set order sum, with an optional
minimum order; the fee is an ordinary order line «Доставка».

Owner decisions (2026-10-23, docs/architecture/PICKUP.md):

- payment options are the module's settings (PQ-033): in advance by bank
  transfer to the venue's requisites
  (``PartnerBotSettings.transfer_payment_details``) and/or on receipt (cash
  or card at the venue, to the courier); with both on, the guest chooses; the
  guest may spend bonuses either way;
- time: as soon as possible, or in 30 / 60 minutes (``Order.requested_for``);
- the guest's phone is required (shared once through Telegram);
- orders are accepted only within the venue's opening hours.

An order is placed when the guest says they have paid, or, paying on
receipt, when they confirm the order. It gets a personal bill at once;
bonuses, if chosen, are applied to that bill. A transfer is then confirmed
by staff (recorded as a ``transfer`` payment, the order is accepted) or
rejected (order cancelled, bill voided, bonuses returned). An order paid on
receipt is accepted as usual; staff record the cash or card payment when the
guest gets the order, or cancel it if the guest refuses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.billing.models import Bill, BillAdjustment, BillOrder, Payment
from apps.billing.services import (
    BillingServiceError,
    _void_locked_bill,
    create_bill_from_orders,
    issue_bill,
    pay_bill_with_bonuses,
    record_payment,
)
from apps.bonuses.services import get_redeemable_bonus_amount
from apps.orders.models import Cart, Order
from apps.orders.services import (
    CHANNEL_OFF_MESSAGES,
    create_order,
    notify_staff_about_new_order,
    requested_time_label,
    transition_order_status,
)
from apps.partners.models import Partner, PartnerBotSettings
from apps.partners.opening_hours import describe_opening_hours, intervals_for, is_open_at
from apps.users.models import GuestProfile, User
from core.i18n import N_, _

# Slot code -> minutes from now. ``now`` means as soon as possible.
TIME_SLOTS = {"now": 0, "30": 30, "60": 60}

# Remote channels and the PartnerBotSettings flag that turns each one on.
REMOTE_CHANNELS = {
    Order.Channel.PICKUP: "module_pickup_enabled",
    Order.Channel.DELIVERY: "module_delivery_enabled",
    Order.Channel.SHIPPING: "module_shipping_enabled",
}
CHANNEL_TITLES = {
    Order.Channel.PICKUP: N_("самовывоз"),
    Order.Channel.DELIVERY: N_("доставка"),
    Order.Channel.SHIPPING: N_("отправка почтой"),
}
# Channels with a time slot and opening hours; shipping has neither.
TIMED_CHANNELS = frozenset({Order.Channel.PICKUP, Order.Channel.DELIVERY})

MAX_ADDRESS_LENGTH = 500

PREPAID = Order.PaymentTiming.PREPAID
ON_RECEIPT = Order.PaymentTiming.ON_RECEIPT
# The settings that turn each payment option on, per module (PQ-033).
PAYMENT_OPTION_FIELDS = {
    Order.Channel.PICKUP: {PREPAID: "pickup_pay_transfer", ON_RECEIPT: "pickup_pay_on_receipt"},
    Order.Channel.DELIVERY: {
        PREPAID: "delivery_pay_transfer",
        ON_RECEIPT: "delivery_pay_on_receipt",
    },
    Order.Channel.SHIPPING: {
        PREPAID: "shipping_pay_transfer",
        ON_RECEIPT: "shipping_pay_on_receipt",
    },
}
# How staff take the money on receipt: cash or card at the venue or to the
# courier; by post the carrier passes on the cash on delivery.
ON_RECEIPT_METHODS = {
    Order.Channel.PICKUP: frozenset({Payment.Method.CASH, Payment.Method.TERMINAL}),
    Order.Channel.DELIVERY: frozenset({Payment.Method.CASH, Payment.Method.TERMINAL}),
    Order.Channel.SHIPPING: frozenset({Payment.Method.COD}),
}

# Shipping (PQ-034): the settings that turn each carrier and postage option on.
CARRIER_FIELDS = {
    Order.Carrier.NOVA_POSHTA: "shipping_nova_poshta",
    Order.Carrier.UKRPOSHTA: "shipping_ukrposhta",
    Order.Carrier.MEEST: "shipping_meest",
}
SHIPPING_COST_FIELDS = {
    Order.ShippingCost.CARRIER: "shipping_cost_carrier",
    Order.ShippingCost.FIXED: "shipping_cost_fixed",
}
MAX_DESTINATION_LENGTH = 500
# A ТТН / tracking number: letters and digits (spaces are dropped).
TRACKING_NUMBER_RE = re.compile(r"^[A-Z0-9]{5,40}$")
# Public tracking pages that take the number in the URL. Meest has no such
# link we rely on: the guest gets the number only.
TRACKING_URLS = {
    Order.Carrier.NOVA_POSHTA: "https://novaposhta.ua/tracking/?cargo_number={number}",
    Order.Carrier.UKRPOSHTA: "https://track.ukrposhta.ua/tracking_UA.html?barcode={number}",
}

# Staff roles that may confirm or reject a guest's transfer.
PAYMENT_CHECK_ROLES = frozenset({User.Role.OWNER, User.Role.MANAGER, User.Role.CASHIER})


class RemoteOrderError(BillingServiceError):
    """A checkout step cannot be completed; the message is shown to the user."""


class StaleQuoteError(RemoteOrderError):
    """The cart or the amount changed since the guest saw the summary."""


@dataclass(frozen=True)
class RemoteQuote:
    total: Decimal  # what the order costs, the delivery fee included
    bonus_available: Decimal
    bonus_to_spend: Decimal
    to_pay: Decimal
    payment_details: str
    slots: tuple[str, ...]
    items_total: Decimal = Decimal("0.00")
    delivery_fee: Decimal = Decimal("0.00")
    delivery_address: str = ""
    has_delivery_location: bool = False
    payment_modes: tuple[str, ...] = (PREPAID,)
    shipping_carrier: str = ""
    shipping_destination: str = ""
    shipping_cost: str = ""
    shipping_cost_options: tuple[str, ...] = ()


def _money(value) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"))


def _check_channel(channel: str) -> None:
    if channel not in REMOTE_CHANNELS:
        raise RemoteOrderError(_("Этот способ заказа пока недоступен."))


def _settings(partner_id, channel: str) -> PartnerBotSettings:
    _check_channel(channel)
    settings = (
        PartnerBotSettings.objects.select_related("partner").filter(partner_id=partner_id).first()
    )
    if settings is None or not (
        getattr(settings, REMOTE_CHANNELS[channel])
        and settings.module_orders_enabled
        and settings.module_menu_enabled
    ):
        raise RemoteOrderError(_(CHANNEL_OFF_MESSAGES[channel]))
    if not payment_modes_for(settings, channel):
        if getattr(settings, PAYMENT_OPTION_FIELDS[channel][PREPAID]):
            raise RemoteOrderError(_("Заведение ещё не указало реквизиты для оплаты переводом."))
        raise RemoteOrderError(_("Заведение ещё не настроило оплату заказов."))
    return settings


def payment_modes_for(settings: PartnerBotSettings, channel: str) -> tuple[str, ...]:
    """Payment options the module offers, transfer first.

    Transfer counts only with the venue's requisites filled in.
    """

    fields = PAYMENT_OPTION_FIELDS[channel]
    modes = []
    if getattr(settings, fields[PREPAID]) and settings.transfer_payment_details.strip():
        modes.append(PREPAID)
    if getattr(settings, fields[ON_RECEIPT]):
        modes.append(ON_RECEIPT)
    return tuple(modes)


def _guest(partner_id, telegram_id: int, *, lock: bool = False) -> GuestProfile:
    queryset = GuestProfile.objects.select_related("telegram_account")
    if lock:
        queryset = queryset.select_for_update(of=("self",))
    guest = queryset.filter(
        partner_id=partner_id,
        telegram_account__telegram_id=telegram_id,
    ).first()
    if guest is None:
        raise RemoteOrderError(_("Сначала нажмите /start, чтобы бот вас узнал."))
    return guest


def _active_cart(partner_id, guest: GuestProfile, channel: str, *, lock: bool = False) -> Cart:
    queryset = Cart.objects.prefetch_related("items__menu_item")
    if lock:
        queryset = queryset.select_for_update(of=("self",))
    cart = (
        queryset.filter(partner_id=partner_id, guest=guest, status=Cart.Status.ACTIVE)
        .order_by("-updated_at")
        .first()
    )
    if cart is None or not cart.items.all():
        raise RemoteOrderError(_("Корзина пуста. Добавьте позиции из меню."))
    if cart.channel != channel:
        raise RemoteOrderError(
            _(
                "Корзина собрана для другого способа заказа. Нажмите «{channel}».",
                channel=_(CHANNEL_TITLES[channel]),
            )
        )
    not_sold = [
        item.item_name
        for item in cart.items.all()
        if not (item.menu_item.is_available and item.menu_item.is_sold_in(channel))
    ]
    if not_sold:
        raise RemoteOrderError(_(
            "Недоступно ({channel}): {not_sold}.",
            channel=_(CHANNEL_TITLES[channel]),
            not_sold=', '.join(not_sold),
        ))
    return cart


def delivery_fee_for(settings: PartnerBotSettings, items_total: Decimal) -> Decimal:
    """The venue's delivery fee for an order of ``items_total``."""

    free_from = settings.delivery_free_from
    if free_from is not None and _money(items_total) >= _money(free_from):
        return Decimal("0.00")
    return _money(settings.delivery_fee or 0)


def shipping_fee_for(settings: PartnerBotSettings, items_total: Decimal, cost: str) -> Decimal:
    """Postage in the order: none when the recipient pays the carrier."""

    if cost != Order.ShippingCost.FIXED:
        return Decimal("0.00")
    free_from = settings.shipping_free_from
    if free_from is not None and _money(items_total) >= _money(free_from):
        return Decimal("0.00")
    return _money(settings.shipping_fee or 0)


def _channel_fee(
    settings: PartnerBotSettings,
    channel: str,
    items_total: Decimal,
    shipping_cost: str = "",
) -> Decimal:
    if channel == Order.Channel.DELIVERY:
        minimum, word = _money(settings.delivery_min_order or 0), _("доставку")
    elif channel == Order.Channel.SHIPPING:
        minimum, word = _money(settings.shipping_min_order or 0), _("отправку")
    else:
        return Decimal("0.00")
    if _money(items_total) < minimum:
        raise RemoteOrderError(
            _(
                "Минимальная сумма заказа на {word} — {minimum} грн. Сейчас в корзине "
                "{items_total} грн.",
                word=word,
                minimum=minimum,
                items_total=_money(items_total),
            )
        )
    if channel == Order.Channel.SHIPPING:
        return shipping_fee_for(settings, items_total, shipping_cost)
    return delivery_fee_for(settings, items_total)


def carriers_for(settings: PartnerBotSettings) -> tuple[str, ...]:
    return tuple(carrier for carrier, field in CARRIER_FIELDS.items() if getattr(settings, field))


def shipping_cost_options_for(settings: PartnerBotSettings) -> tuple[str, ...]:
    return tuple(
        cost for cost, field in SHIPPING_COST_FIELDS.items() if getattr(settings, field)
    )


def carrier_title(carrier: str) -> str:
    return str(Order.Carrier(carrier).label) if carrier in Order.Carrier.values else ""


@dataclass(frozen=True)
class ShippingTerms:
    carriers: tuple[str, ...]
    cost_options: tuple[str, ...]
    fee: Decimal
    free_from: Decimal | None
    min_order: Decimal

    def fixed_label(self) -> str:
        """«70 грн, бесплатно от 1000 грн» — the fixed postage option."""

        if self.fee <= 0:
            return _("бесплатно")
        if self.free_from is not None:
            return _(
                "{fee} грн, бесплатно от {free_from} грн",
                fee=self.fee,
                free_from=self.free_from,
            )
        return _("{fee} грн", fee=self.fee)

    def describe(self) -> str:
        """«Отправляем: Нова Пошта, Укрпошта. Пересылка: …»"""

        carriers = ", ".join(carrier_title(carrier) for carrier in self.carriers)
        options = []
        if Order.ShippingCost.CARRIER in self.cost_options:
            options.append(_("по тарифу перевозчика, оплата на почте"))
        if Order.ShippingCost.FIXED in self.cost_options:
            options.append(_("в заказе — {fixed}", fixed=self.fixed_label()))
        text = _(
            "Отправляем: {carriers}. Пересылка: {options}.",
            carriers=carriers,
            options=_(' или ').join(options),
        )
        if self.min_order > 0:
            text += _(" Минимальный заказ {min_order} грн.", min_order=self.min_order)
        return text


def get_shipping_terms(partner_id) -> ShippingTerms:
    settings = _settings(partner_id, Order.Channel.SHIPPING)
    return ShippingTerms(
        carriers=carriers_for(settings),
        cost_options=shipping_cost_options_for(settings),
        fee=_money(settings.shipping_fee or 0),
        free_from=(
            _money(settings.shipping_free_from)
            if settings.shipping_free_from is not None
            else None
        ),
        min_order=_money(settings.shipping_min_order or 0),
    )


def tracking_link(carrier: str, number: str) -> str:
    template = TRACKING_URLS.get(carrier)
    return template.format(number=number) if template else ""


@dataclass(frozen=True)
class DeliveryTerms:
    fee: Decimal
    free_from: Decimal | None
    min_order: Decimal

    def describe(self) -> str:
        """«Доставка 60 грн, бесплатно от 1000 грн. Минимальный заказ 150 грн.»"""

        if self.fee <= 0:
            text = _("Доставка бесплатная.")
        elif self.free_from is not None:
            text = _(
                "Доставка {fee} грн, бесплатно от {free_from} грн.",
                fee=self.fee,
                free_from=self.free_from,
            )
        else:
            text = _("Доставка {fee} грн.", fee=self.fee)
        if self.min_order > 0:
            text += _(" Минимальный заказ {min_order} грн.", min_order=self.min_order)
        return text


def get_delivery_terms(partner_id) -> DeliveryTerms:
    settings = _settings(partner_id, Order.Channel.DELIVERY)
    return DeliveryTerms(
        fee=_money(settings.delivery_fee or 0),
        free_from=(
            _money(settings.delivery_free_from)
            if settings.delivery_free_from is not None
            else None
        ),
        min_order=_money(settings.delivery_min_order or 0),
    )


CLOSED_MESSAGES = {
    # The way that is closed now, not the whole venue: other ways may be open.
    Order.Channel.PICKUP: (
        N_("Самовывоз сейчас закрыт. Часы самовывоза: {hours}."),
        N_("Самовывоз сейчас закрыт."),
    ),
    Order.Channel.DELIVERY: (
        N_("Доставка сейчас не работает. Часы доставки: {hours}."),
        N_("Доставка сейчас не работает."),
    ),
}


def closed_message(partner: Partner, channel: str = "") -> str:
    hours = describe_opening_hours(partner, scope=channel)
    with_hours, without_hours = CLOSED_MESSAGES.get(
        channel,
        (
            N_("Сейчас заказы не принимаем. Часы работы: {hours}."),
            N_("Сейчас заказы не принимаем."),
        ),
    )
    if hours:
        return _(with_hours, hours=hours)
    return _(without_hours)


def available_slots(
    partner: Partner, now: datetime | None = None, *, channel: str = ""
) -> tuple[str, ...]:
    """Slots whose moment falls within the module's hours; none if closed now."""

    now = now or timezone.now()
    intervals = intervals_for(partner, channel)
    if not is_open_at(partner, now, intervals=intervals):
        return ()
    return tuple(
        code
        for code, minutes in TIME_SLOTS.items()
        if is_open_at(partner, now + timedelta(minutes=minutes), intervals=intervals)
    )



def get_remote_quote(
    *,
    partner_id,
    telegram_id: int,
    use_bonuses: bool,
    channel: str = Order.Channel.PICKUP,
    shipping_cost: str = "",
) -> RemoteQuote:
    settings = _settings(partner_id, channel)
    guest = _guest(partner_id, telegram_id)
    cart = _active_cart(partner_id, guest, channel)
    items_total = _money(cart.total_amount)
    is_shipping = channel == Order.Channel.SHIPPING
    cost_options = shipping_cost_options_for(settings) if is_shipping else ()
    if is_shipping and shipping_cost not in cost_options:
        shipping_cost = cost_options[0] if cost_options else ""
    fee = _channel_fee(settings, channel, items_total, shipping_cost)
    total = _money(items_total + fee)
    bonus_available = get_redeemable_bonus_amount(
        partner_id=partner_id,
        guest=guest,
        purchase_total=total,
    )
    bonus_to_spend = bonus_available if use_bonuses else Decimal("0.00")
    return RemoteQuote(
        total=total,
        bonus_available=bonus_available,
        bonus_to_spend=bonus_to_spend,
        to_pay=_money(total - bonus_to_spend),
        payment_details=settings.transfer_payment_details.strip(),
        slots=available_slots(settings.partner, channel=channel),
        items_total=items_total,
        delivery_fee=fee,
        delivery_address=guest.delivery_address if channel == Order.Channel.DELIVERY else "",
        has_delivery_location=(
            channel == Order.Channel.DELIVERY and guest.delivery_latitude is not None
        ),
        payment_modes=payment_modes_for(settings, channel),
        shipping_carrier=guest.shipping_carrier if is_shipping else "",
        shipping_destination=guest.shipping_destination if is_shipping else "",
        shipping_cost=shipping_cost,
        shipping_cost_options=cost_options,
    )


@transaction.atomic
def save_guest_phone(*, partner_id, telegram_id: int, phone: str) -> GuestProfile:
    phone = (phone or "").strip()
    if not phone:
        raise RemoteOrderError(_("Номер телефона пустой."))
    guest = _guest(partner_id, telegram_id, lock=True)
    guest.phone = phone[:32]
    guest.save(update_fields=["phone", "updated_at"])
    return guest


@transaction.atomic
def save_guest_delivery_address(*, partner_id, telegram_id: int, address: str) -> GuestProfile:
    """Remember the guest's delivery address; a new text drops an old map point."""

    address = " ".join((address or "").split())
    if not address:
        raise RemoteOrderError(_("Напишите адрес: улица, дом, квартира."))
    if len(address) > MAX_ADDRESS_LENGTH:
        raise RemoteOrderError(_("Адрес слишком длинный — сократите, пожалуйста."))
    guest = _guest(partner_id, telegram_id, lock=True)
    if guest.delivery_address != address:
        guest.delivery_latitude = None
        guest.delivery_longitude = None
    guest.delivery_address = address
    guest.save(
        update_fields=[
            "delivery_address",
            "delivery_latitude",
            "delivery_longitude",
            "updated_at",
        ]
    )
    return guest


@transaction.atomic
def save_guest_delivery_location(
    *, partner_id, telegram_id: int, latitude, longitude
) -> GuestProfile:
    """Attach a Telegram location to the guest's delivery address."""

    try:
        lat = Decimal(str(latitude)).quantize(Decimal("0.000001"))
        lon = Decimal(str(longitude)).quantize(Decimal("0.000001"))
    except ArithmeticError as exc:
        raise RemoteOrderError(_("Не удалось прочитать геолокацию.")) from exc
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise RemoteOrderError(_("Не удалось прочитать геолокацию."))
    guest = _guest(partner_id, telegram_id, lock=True)
    guest.delivery_latitude = lat
    guest.delivery_longitude = lon
    guest.save(update_fields=["delivery_latitude", "delivery_longitude", "updated_at"])
    return guest


@transaction.atomic
def save_guest_shipping_carrier(*, partner_id, telegram_id: int, carrier: str) -> GuestProfile:
    settings = _settings(partner_id, Order.Channel.SHIPPING)
    if carrier not in carriers_for(settings):
        raise RemoteOrderError(_("Эта служба доставки сейчас недоступна — выберите другую."))
    guest = _guest(partner_id, telegram_id, lock=True)
    guest.shipping_carrier = carrier
    guest.save(update_fields=["shipping_carrier", "updated_at"])
    return guest


@transaction.atomic
def save_guest_shipping_destination(*, partner_id, telegram_id: int, text: str) -> GuestProfile:
    """Remember who receives the parcel and where: name, city, branch."""

    text = " ".join((text or "").split())
    if not text:
        raise RemoteOrderError(_("Напишите получателя, город и номер отделения или почтомата."))
    if len(text) > MAX_DESTINATION_LENGTH:
        raise RemoteOrderError(_("Слишком длинно — сократите, пожалуйста."))
    guest = _guest(partner_id, telegram_id, lock=True)
    guest.shipping_destination = text
    guest.save(update_fields=["shipping_destination", "updated_at"])
    return guest


def map_link(latitude, longitude) -> str:
    return f"https://maps.google.com/?q={latitude},{longitude}"


@transaction.atomic
def place_remote_order(
    *,
    partner_id,
    telegram_id: int,
    slot: str,
    use_bonuses: bool,
    expected_total: Decimal | str,
    expected_to_pay: Decimal | str,
    channel: str = Order.Channel.PICKUP,
    payment: str = PREPAID,
    shipping_cost: str = "",
    now: datetime | None = None,
) -> Order:
    """Turn the guest's cart into a remote order.

    Paying by transfer, the guest calls this once they say they paid; paying
    on receipt, once they confirm the order.

    ``expected_total`` / ``expected_to_pay`` are the amounts the guest saw;
    if the cart or the bonus amount changed since, nothing is created and the
    guest is asked to check the new amount.
    """

    now = now or timezone.now()
    settings = _settings(partner_id, channel)
    partner = settings.partner
    if payment not in payment_modes_for(settings, channel):
        raise RemoteOrderError(_("Этот способ оплаты сейчас недоступен — выберите другой."))
    is_delivery = channel == Order.Channel.DELIVERY
    is_shipping = channel == Order.Channel.SHIPPING
    if channel in TIMED_CHANNELS:
        if slot not in TIME_SLOTS:
            raise RemoteOrderError(_("Выберите время ещё раз."))
        if slot not in available_slots(partner, now, channel=channel):
            raise RemoteOrderError(closed_message(partner, channel))
    elif shipping_cost not in shipping_cost_options_for(settings):
        raise RemoteOrderError(_(
            "Этот вариант оплаты пересылки сейчас недоступен — выберите другой.",
        ))
    guest = _guest(partner_id, telegram_id, lock=True)
    if not guest.phone:
        raise RemoteOrderError(_("Поделитесь номером телефона, чтобы оформить заказ."))
    # AA-037 (PQ-036): the guest lock serialises this count with other
    # placements of the same guest.
    limit = settings.remote_unpaid_orders_limit
    if limit and open_unpaid_remote_orders(guest).count() >= limit:
        raise RemoteOrderError(
            _(
                "У вас уже есть неоплаченные заказы ({limit}). Оплатите или получите их, чтобы "
                "оформить новый.",
                limit=limit,
            )
        )
    if is_delivery and not guest.delivery_address:
        raise RemoteOrderError(_("Укажите адрес доставки."))
    if is_shipping:
        if not guest.shipping_destination:
            raise RemoteOrderError(_("Куда отправить? Укажите получателя и отделение."))
        if guest.shipping_carrier not in carriers_for(settings):
            raise RemoteOrderError(_("Эта служба доставки сейчас недоступна — выберите другую."))
    cart = _active_cart(partner_id, guest, channel, lock=True)
    cart_items = list(cart.items.all())
    items_total = _money(
        sum((item.unit_price * item.quantity for item in cart_items), Decimal("0"))
    )
    fee = _channel_fee(settings, channel, items_total, shipping_cost)
    total = _money(items_total + fee)
    if total != _money(expected_total):
        raise StaleQuoteError(_("Корзина изменилась — проверьте новую сумму."))

    minutes = TIME_SLOTS[slot] if channel in TIMED_CHANNELS else 0
    order = create_order(
        partner_id=partner_id,
        guest=guest,
        items=[
            {
                "menu_item_id": item.menu_item_id,
                "item_name": item.item_name,
                "unit_price": item.unit_price,
                "quantity": item.quantity,
                "comment": item.comment,
            }
            for item in cart_items
        ],
        comment=cart.comment,
        channel=channel,
        requested_for=now + timedelta(minutes=minutes) if minutes else None,
        notify_staff=False,
        payment_timing=payment,
        **(
            {
                "delivery_address": guest.delivery_address,
                "delivery_latitude": guest.delivery_latitude,
                "delivery_longitude": guest.delivery_longitude,
                "delivery_fee": fee,
            }
            if is_delivery
            else {}
        ),
        **(
            {
                "shipping_carrier": guest.shipping_carrier,
                "shipping_destination": guest.shipping_destination,
                "shipping_cost": shipping_cost,
                "delivery_fee": fee,
            }
            if is_shipping
            else {}
        ),
    )
    cart.status = Cart.Status.CHECKED_OUT
    cart.checked_out_at = now
    cart.save(update_fields=["status", "checked_out_at", "updated_at"])

    bill = create_bill_from_orders(
        partner_id=partner_id,
        order_ids=[str(order.id)],
        kind=Bill.Kind.PERSONAL,
        label=f"{_(CHANNEL_TITLES[channel]).capitalize()} #{order.public_id}",
    )
    bill = issue_bill(bill)
    if use_bonuses:
        try:
            result = pay_bill_with_bonuses(bill=bill)
        except BillingServiceError as exc:
            raise RemoteOrderError(_(
                "Бонусы сейчас списать нельзя — проверьте сумму ещё раз.",
            )) from exc
        bill = result.bill
    bill.refresh_from_db()
    if bill.bonus_spent_amount:
        Order.objects.filter(id=order.id).update(bonus_spent=bill.bonus_spent_amount)
    to_pay = _money(bill.total_amount - bill.paid_amount)
    if bill.status == Bill.Status.PAID:
        to_pay = Decimal("0.00")
    if to_pay != _money(expected_to_pay):
        raise StaleQuoteError(_("Сумма к оплате изменилась — проверьте её ещё раз."))

    phone = guest.phone
    address = guest.delivery_address
    point = (
        map_link(guest.delivery_latitude, guest.delivery_longitude)
        if guest.delivery_latitude is not None
        else ""
    )
    carrier = guest.shipping_carrier
    destination = guest.shipping_destination

    def staff_note() -> str:
        # Built per employee, in their language (PQ-040).
        lines = [_("Телефон: {phone}.", phone=phone)]
        if is_delivery:
            lines.append(_("Адрес: {delivery_address}.", delivery_address=address))
            if point:
                lines.append(_("На карте: {map_link}", map_link=point))
        if is_shipping:
            lines.append(
                _(
                    "{carrier}: {shipping_destination}.",
                    carrier=carrier_title(carrier),
                    shipping_destination=destination,
                )
            )
            cost = _shipping_cost_label(shipping_cost, fee)
            lines.append(_("Пересылка: {shipping_cost}.", shipping_cost=cost))
        if to_pay > 0 and payment == ON_RECEIPT and is_shipping:
            lines.append(
                _(
                    "Наложенный платёж: {to_pay} грн. Отметьте оплату в карточке заказа, когда "
                    "служба перечислит деньги.",
                    to_pay=to_pay,
                )
            )
        elif to_pay > 0 and payment == ON_RECEIPT:
            lines.append(
                _(
                    "Оплата при получении: {to_pay} грн (наличные или карта). Отметьте оплату в "
                    "карточке заказа, когда гость заплатит.",
                    to_pay=to_pay,
                )
            )
        elif to_pay > 0:
            lines.append(
                _(
                    "Гость сообщил о переводе {to_pay} грн. Проверьте поступление и подтвердите "
                    "оплату в карточке заказа.",
                    to_pay=to_pay,
                )
            )
        else:
            lines.append(_("Оплачен бонусами полностью."))
        return "\n".join(lines)

    notify_staff_about_new_order(order, note=staff_note)
    return Order.objects.select_related("partner").get(id=order.id)


def _shipping_cost_label(cost: str, fee: Decimal) -> str:
    if cost == Order.ShippingCost.CARRIER:
        return _("по тарифу перевозчика, платит получатель на почте")
    return _("в заказе, {fee} грн", fee=fee) if fee > 0 else _("в заказе, бесплатно")


def shipping_cost_label(order: Order) -> str:
    fee = sum(
        (
            item.unit_price * item.quantity
            for item in order.items.all()
            if item.kind == item.Kind.DELIVERY_FEE
        ),
        Decimal("0.00"),
    )
    return _shipping_cost_label(order.shipping_cost, _money(fee))


def _order_bill(order: Order) -> Bill:
    bill_ids = BillOrder.objects.filter(
        partner_id=order.partner_id,
        order_id=order.id,
    ).values_list("bill_id", flat=True)
    bill = (
        Bill.objects.select_for_update()
        .filter(id__in=list(bill_ids))
        .exclude(status=Bill.Status.CANCELED)
        .first()
    )
    if bill is None:
        raise RemoteOrderError(_("У заказа нет действующего счёта."))
    return bill


def _check_actor(actor, partner_id) -> None:
    if (
        actor is None
        or not actor.is_active
        or str(actor.partner_id) != str(partner_id)
        or actor.role not in PAYMENT_CHECK_ROLES
    ):
        raise RemoteOrderError(_("Подтверждать оплату может владелец, менеджер или кассир."))


def _lock_remote_order(order: Order, payment: str = PREPAID) -> Order:
    order = (
        Order.objects.select_for_update(of=("self",))
        .select_related("partner")
        .get(id=order.id, partner_id=order.partner_id)
    )
    if order.payment_timing != payment:
        if payment == PREPAID:
            raise RemoteOrderError(_("У этого заказа нет оплаты переводом."))
        raise RemoteOrderError(_("Этот заказ оплачивается переводом заранее, а не при получении."))
    return order


def awaits_transfer_check(order: Order) -> bool:
    return (
        order.payment_timing == PREPAID
        and order.paid_at is None
        and order.status == Order.Status.NEW
    )


def awaits_payment_on_receipt(order: Order) -> bool:
    """An order paid on receipt whose money staff have not recorded yet."""

    return (
        order.payment_timing == ON_RECEIPT
        and order.paid_at is None
        and order.status != Order.Status.CANCELED
    )


@transaction.atomic
def confirm_transfer(*, order: Order, actor) -> Order:
    """The transfer arrived: record it and take the order into work."""

    _check_actor(actor, order.partner_id)
    bill = _order_bill(order)
    order = _lock_remote_order(order)
    if not awaits_transfer_check(order):
        raise RemoteOrderError(_("Оплата по этому заказу уже обработана."))
    if bill.status != Bill.Status.PAID:
        remaining = _money(bill.total_amount - bill.paid_amount)
        record_payment(
            bill=bill,
            amount=remaining,
            method=Payment.Method.TRANSFER,
            created_by=actor,
            comment=_("Перевод за заказ #{public_id} подтверждён.", public_id=order.public_id),
        )
    order.refresh_from_db()
    return transition_order_status(
        order=order,
        to_status=Order.Status.ACCEPTED,
        actor_user=actor,
        note=_("Оплата переводом подтверждена."),
    )


@transaction.atomic
def reject_transfer(*, order: Order, actor, comment: str = "") -> Order:
    """The transfer did not arrive: cancel the order and return bonuses."""

    _check_actor(actor, order.partner_id)
    bill = _order_bill(order)
    order = _lock_remote_order(order)
    if not awaits_transfer_check(order):
        raise RemoteOrderError(_("Оплата по этому заказу уже обработана."))
    if bill.status == Bill.Status.PAID or bill.paid_amount > 0:
        raise RemoteOrderError(_("По заказу уже есть оплата — отменить его так нельзя."))
    _void_locked_bill(
        bill=bill,
        actor=actor,
        reason_code=BillAdjustment.ReasonCode.PAYMENT_NOT_RECEIVED,
        reason_text=comment,
    )
    return transition_order_status(
        order=order,
        to_status=Order.Status.CANCELED,
        actor_user=actor,
        note=_("Оплата переводом не поступила."),
    )


@transaction.atomic
def record_payment_on_receipt(*, order: Order, actor, method: str) -> Order:
    """The guest paid on receipt: record the rest of the bill in cash or by card.

    Lock order as for any payment: Bill → Order (AGENTS.md); the bill lock
    makes a repeated press see the bill already paid.
    """

    _check_actor(actor, order.partner_id)
    if method not in ON_RECEIPT_METHODS.get(order.channel, ()):
        if order.channel == Order.Channel.SHIPPING:
            raise RemoteOrderError(_("Отправка оплачивается наложенным платежом."))
        raise RemoteOrderError(_("При получении принимаем наличные или карту — выберите способ."))
    bill = _order_bill(order)
    order = _lock_remote_order(order, ON_RECEIPT)
    if bill.status == Bill.Status.PAID or order.paid_at is not None:
        raise RemoteOrderError(_("Заказ уже оплачен."))
    record_payment(
        bill=bill,
        amount=_money(bill.total_amount - bill.paid_amount),
        method=method,
        created_by=actor,
        comment=_("Оплата при получении за заказ #{public_id}.", public_id=order.public_id),
    )
    return Order.objects.select_related("partner").get(id=order.id)


@transaction.atomic
def cancel_unpaid_remote_order(*, order: Order, actor, comment: str = "") -> Order:
    """The guest did not take an order paid on receipt: cancel it, return bonuses."""

    _check_actor(actor, order.partner_id)
    bill = _order_bill(order)
    order = _lock_remote_order(order, ON_RECEIPT)
    if order.status in {Order.Status.COMPLETED, Order.Status.CANCELED}:
        raise RemoteOrderError(_("Заказ уже закрыт."))
    if bill.status == Bill.Status.PAID or bill.payments.exclude(
        method=Payment.Method.BONUSES
    ).filter(status=Payment.Status.PAID).exists():
        raise RemoteOrderError(_("По заказу уже есть оплата — отменить его так нельзя."))
    _void_locked_bill(
        bill=bill,
        actor=actor,
        reason_code=BillAdjustment.ReasonCode.GUEST_REFUSED,
        reason_text=comment,
    )
    return transition_order_status(
        order=order,
        to_status=Order.Status.CANCELED,
        actor_user=actor,
        note=_("Гость не забрал заказ."),
    )


def open_unpaid_remote_orders(guest: GuestProfile):
    """The guest's remote orders not yet paid, neither finished nor canceled."""

    return Order.objects.filter(
        partner_id=guest.partner_id,
        guest=guest,
        channel__in=Order.REMOTE_CHANNELS,
        paid_at__isnull=True,
    ).exclude(status__in=[Order.Status.COMPLETED, Order.Status.CANCELED])


@transaction.atomic
def expire_unconfirmed_transfer(*, order_id, now: datetime | None = None) -> bool:
    """Cancel one order whose transfer nobody confirmed in time (PQ-036).

    Same locks and effects as ``reject_transfer`` (Bill → Order; the bill is
    voided with ``payment_not_received``, bonuses come back), with no staff
    actor. Rechecked under the locks, so a transfer confirmed meanwhile wins.
    """

    now = now or timezone.now()
    order = Order.objects.select_related("partner").filter(id=order_id).first()
    if order is None or order.partner.status != Partner.Status.ACTIVE:
        return False
    settings = PartnerBotSettings.objects.filter(partner_id=order.partner_id).first()
    minutes = settings.transfer_wait_minutes if settings is not None else 0
    if not minutes or order.payment_timing != PREPAID:
        return False
    try:
        bill = _order_bill(order)
    except RemoteOrderError:
        return False
    order = _lock_remote_order(order)
    if not awaits_transfer_check(order) or order.created_at > now - timedelta(minutes=minutes):
        return False
    if bill.status == Bill.Status.PAID or bill.paid_amount > 0:
        return False
    _void_locked_bill(
        bill=bill,
        actor=None,
        reason_code=BillAdjustment.ReasonCode.PAYMENT_NOT_RECEIVED,
        reason_text=_("Автоотмена: перевод не подтверждён за {minutes} мин.", minutes=minutes),
    )
    transition_order_status(
        order=order,
        to_status=Order.Status.CANCELED,
        actor_user=None,
        note=_(
            "Оплата переводом не подтверждена за {minutes} мин — отменён автоматически.",
            minutes=minutes,
        ),
    )
    return True


def cancel_expired_transfer_orders(*, now: datetime | None = None) -> int:
    """Beat job: cancel every remote order whose transfer wait has passed.

    Each order is its own transaction; suspended partners are left alone (the
    decision is taken at run time, AGENTS.md).
    """

    now = now or timezone.now()
    canceled = 0
    waits = PartnerBotSettings.objects.filter(
        partner__status=Partner.Status.ACTIVE, transfer_wait_minutes__gt=0
    ).values_list("partner_id", "transfer_wait_minutes")
    for partner_id, minutes in waits:
        order_ids = Order.objects.filter(
            partner_id=partner_id,
            channel__in=Order.REMOTE_CHANNELS,
            payment_timing=PREPAID,
            paid_at__isnull=True,
            status=Order.Status.NEW,
            created_at__lte=now - timedelta(minutes=minutes),
        ).values_list("id", flat=True)
        for order_id in list(order_ids):
            if expire_unconfirmed_transfer(order_id=order_id, now=now):
                canceled += 1
    return canceled


def normalize_tracking_number(raw: str) -> str:
    number = "".join((raw or "").split()).upper()
    if not TRACKING_NUMBER_RE.match(number):
        raise RemoteOrderError(
            _("Не похоже на ТТН: нужны только цифры и латинские буквы, от 5 знаков.")
        )
    return number


@transaction.atomic
def mark_order_shipped(*, order: Order, actor, tracking_number: str) -> Order:
    """The parcel is sent: keep its ТТН and mark the order «Отправлен».

    Any active staff member of the partner may do it, as with other status
    steps. The guest gets the ТТН and a tracking link (notifications).
    """

    number = normalize_tracking_number(tracking_number)
    if (
        actor is None
        or not actor.is_active
        or str(actor.partner_id) != str(order.partner_id)
    ):
        raise RemoteOrderError(_("Отмечать отправку может только сотрудник этого заведения."))
    order = (
        Order.objects.select_for_update(of=("self",))
        .select_related("partner")
        .get(id=order.id, partner_id=order.partner_id)
    )
    if order.channel != Order.Channel.SHIPPING:
        raise RemoteOrderError(_("ТТН бывает только у отправки почтой."))
    if order.status != Order.Status.READY:
        if order.status == Order.Status.DELIVERING:
            raise RemoteOrderError(_("Заказ уже отправлен."))
        raise RemoteOrderError(_("Сначала отметьте, что заказ собран."))
    order.tracking_number = number
    order.save(update_fields=["tracking_number", "updated_at"])
    return transition_order_status(
        order=order,
        to_status=Order.Status.DELIVERING,
        actor_user=actor,
        note=_("Отправлен, ТТН {number}.", number=number),
    )


__all__ = [
    "CHANNEL_TITLES",
    "REMOTE_CHANNELS",
    "TIME_SLOTS",
    "RemoteOrderError",
    "RemoteQuote",
    "StaleQuoteError",
    "available_slots",
    "closed_message",
    "confirm_transfer",
    "DeliveryTerms",
    "delivery_fee_for",
    "get_delivery_terms",
    "map_link",
    "get_remote_quote",
    "awaits_payment_on_receipt",
    "awaits_transfer_check",
    "cancel_expired_transfer_orders",
    "cancel_unpaid_remote_order",
    "expire_unconfirmed_transfer",
    "open_unpaid_remote_orders",
    "carrier_title",
    "carriers_for",
    "get_shipping_terms",
    "mark_order_shipped",
    "normalize_tracking_number",
    "save_guest_shipping_carrier",
    "save_guest_shipping_destination",
    "shipping_cost_label",
    "shipping_cost_options_for",
    "shipping_fee_for",
    "ShippingTerms",
    "tracking_link",
    "payment_modes_for",
    "record_payment_on_receipt",
    "requested_time_label",
    "place_remote_order",
    "reject_transfer",
    "save_guest_delivery_address",
    "save_guest_delivery_location",
    "save_guest_phone",
]
