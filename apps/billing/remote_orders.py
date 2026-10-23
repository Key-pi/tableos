"""Remote prepaid orders: quote, place, confirm or reject the guest's transfer.

One flow for every channel where the guest is not at a table (ADR-0009):
pickup and delivery. The channel only changes its own details (pickup:
when; delivery: when, where and the delivery fee) — the order, bill and
payment are the same models for every channel.

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
from apps.partners.opening_hours import describe_opening_hours, is_open_at
from apps.users.models import GuestProfile, User

# Slot code -> minutes from now. ``now`` means as soon as possible.
TIME_SLOTS = {"now": 0, "30": 30, "60": 60}

# Remote channels and the PartnerBotSettings flag that turns each one on.
REMOTE_CHANNELS = {
    Order.Channel.PICKUP: "module_pickup_enabled",
    Order.Channel.DELIVERY: "module_delivery_enabled",
}
CHANNEL_TITLES = {Order.Channel.PICKUP: "самовывоз", Order.Channel.DELIVERY: "доставка"}

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
}
# How staff take the money on receipt.
ON_RECEIPT_METHODS = frozenset({Payment.Method.CASH, Payment.Method.TERMINAL})

# Staff roles that may confirm or reject a guest's transfer.
PAYMENT_CHECK_ROLES = frozenset({User.Role.OWNER, User.Role.MANAGER, User.Role.CASHIER})


class RemoteOrderError(BillingServiceError):
    """A checkout step cannot be completed; the message is shown to the user."""


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


def _money(value) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"))


def _check_channel(channel: str) -> None:
    if channel not in REMOTE_CHANNELS:
        raise RemoteOrderError("Этот способ заказа пока недоступен.")


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
        raise RemoteOrderError(CHANNEL_OFF_MESSAGES[channel])
    if not payment_modes_for(settings, channel):
        if getattr(settings, PAYMENT_OPTION_FIELDS[channel][PREPAID]):
            raise RemoteOrderError("Заведение ещё не указало реквизиты для оплаты переводом.")
        raise RemoteOrderError("Заведение ещё не настроило оплату заказов.")
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
        raise RemoteOrderError("Сначала нажмите /start, чтобы бот вас узнал.")
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
        raise RemoteOrderError("Корзина пуста. Добавьте позиции из меню.")
    if cart.channel != channel:
        raise RemoteOrderError(
            f"Корзина собрана для другого способа заказа. Нажмите «{CHANNEL_TITLES[channel]}»."
        )
    not_sold = [
        item.item_name
        for item in cart.items.all()
        if not (item.menu_item.is_available and item.menu_item.is_sold_in(channel))
    ]
    if not_sold:
        raise RemoteOrderError(f"Недоступно ({CHANNEL_TITLES[channel]}): {', '.join(not_sold)}.")
    return cart


def delivery_fee_for(settings: PartnerBotSettings, items_total: Decimal) -> Decimal:
    """The venue's delivery fee for an order of ``items_total``."""

    free_from = settings.delivery_free_from
    if free_from is not None and _money(items_total) >= _money(free_from):
        return Decimal("0.00")
    return _money(settings.delivery_fee or 0)


def _channel_fee(settings: PartnerBotSettings, channel: str, items_total: Decimal) -> Decimal:
    if channel != Order.Channel.DELIVERY:
        return Decimal("0.00")
    minimum = _money(settings.delivery_min_order or 0)
    if _money(items_total) < minimum:
        raise RemoteOrderError(
            f"Минимальная сумма заказа на доставку — {minimum} грн. Сейчас в корзине "
            f"{_money(items_total)} грн."
        )
    return delivery_fee_for(settings, items_total)


@dataclass(frozen=True)
class DeliveryTerms:
    fee: Decimal
    free_from: Decimal | None
    min_order: Decimal

    def describe(self) -> str:
        """«Доставка 60 грн, бесплатно от 1000 грн. Минимальный заказ 150 грн.»"""

        if self.fee <= 0:
            text = "Доставка бесплатная."
        elif self.free_from is not None:
            text = f"Доставка {self.fee} грн, бесплатно от {self.free_from} грн."
        else:
            text = f"Доставка {self.fee} грн."
        if self.min_order > 0:
            text += f" Минимальный заказ {self.min_order} грн."
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


def closed_message(partner: Partner) -> str:
    hours = describe_opening_hours(partner)
    if hours:
        return f"Сейчас заказы не принимаем. Часы работы: {hours}."
    return "Сейчас заказы не принимаем."


def available_slots(partner: Partner, now: datetime | None = None) -> tuple[str, ...]:
    """Slots whose moment falls within opening hours; none if closed now."""

    now = now or timezone.now()
    if not is_open_at(partner, now):
        return ()
    return tuple(
        code
        for code, minutes in TIME_SLOTS.items()
        if is_open_at(partner, now + timedelta(minutes=minutes))
    )


def get_remote_quote(
    *,
    partner_id,
    telegram_id: int,
    use_bonuses: bool,
    channel: str = Order.Channel.PICKUP,
) -> RemoteQuote:
    settings = _settings(partner_id, channel)
    guest = _guest(partner_id, telegram_id)
    cart = _active_cart(partner_id, guest, channel)
    items_total = _money(cart.total_amount)
    fee = _channel_fee(settings, channel, items_total)
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
        slots=available_slots(settings.partner),
        items_total=items_total,
        delivery_fee=fee,
        delivery_address=guest.delivery_address if channel == Order.Channel.DELIVERY else "",
        has_delivery_location=(
            channel == Order.Channel.DELIVERY and guest.delivery_latitude is not None
        ),
        payment_modes=payment_modes_for(settings, channel),
    )


@transaction.atomic
def save_guest_phone(*, partner_id, telegram_id: int, phone: str) -> GuestProfile:
    phone = (phone or "").strip()
    if not phone:
        raise RemoteOrderError("Номер телефона пустой.")
    guest = _guest(partner_id, telegram_id, lock=True)
    guest.phone = phone[:32]
    guest.save(update_fields=["phone", "updated_at"])
    return guest


@transaction.atomic
def save_guest_delivery_address(*, partner_id, telegram_id: int, address: str) -> GuestProfile:
    """Remember the guest's delivery address; a new text drops an old map point."""

    address = " ".join((address or "").split())
    if not address:
        raise RemoteOrderError("Напишите адрес: улица, дом, квартира.")
    if len(address) > MAX_ADDRESS_LENGTH:
        raise RemoteOrderError("Адрес слишком длинный — сократите, пожалуйста.")
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
        raise RemoteOrderError("Не удалось прочитать геолокацию.") from exc
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise RemoteOrderError("Не удалось прочитать геолокацию.")
    guest = _guest(partner_id, telegram_id, lock=True)
    guest.delivery_latitude = lat
    guest.delivery_longitude = lon
    guest.save(update_fields=["delivery_latitude", "delivery_longitude", "updated_at"])
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
        raise RemoteOrderError("Этот способ оплаты сейчас недоступен — выберите другой.")
    if slot not in TIME_SLOTS:
        raise RemoteOrderError("Выберите время ещё раз.")
    if slot not in available_slots(partner, now):
        raise RemoteOrderError(closed_message(partner))
    guest = _guest(partner_id, telegram_id, lock=True)
    if not guest.phone:
        raise RemoteOrderError("Поделитесь номером телефона, чтобы оформить заказ.")
    is_delivery = channel == Order.Channel.DELIVERY
    if is_delivery and not guest.delivery_address:
        raise RemoteOrderError("Укажите адрес доставки.")
    cart = _active_cart(partner_id, guest, channel, lock=True)
    cart_items = list(cart.items.all())
    items_total = _money(
        sum((item.unit_price * item.quantity for item in cart_items), Decimal("0"))
    )
    fee = _channel_fee(settings, channel, items_total)
    total = _money(items_total + fee)
    if total != _money(expected_total):
        raise RemoteOrderError("Корзина изменилась — проверьте новую сумму.")

    minutes = TIME_SLOTS[slot]
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
    )
    cart.status = Cart.Status.CHECKED_OUT
    cart.checked_out_at = now
    cart.save(update_fields=["status", "checked_out_at", "updated_at"])

    bill = create_bill_from_orders(
        partner_id=partner_id,
        order_ids=[str(order.id)],
        kind=Bill.Kind.PERSONAL,
        label=f"{CHANNEL_TITLES[channel].capitalize()} #{order.public_id}",
    )
    bill = issue_bill(bill)
    if use_bonuses:
        try:
            result = pay_bill_with_bonuses(bill=bill)
        except BillingServiceError as exc:
            raise RemoteOrderError("Бонусы сейчас списать нельзя — проверьте сумму ещё раз.") from exc
        bill = result.bill
    bill.refresh_from_db()
    if bill.bonus_spent_amount:
        Order.objects.filter(id=order.id).update(bonus_spent=bill.bonus_spent_amount)
    to_pay = _money(bill.total_amount - bill.paid_amount)
    if bill.status == Bill.Status.PAID:
        to_pay = Decimal("0.00")
    if to_pay != _money(expected_to_pay):
        raise RemoteOrderError("Сумма к оплате изменилась — проверьте её ещё раз.")

    if to_pay > 0 and payment == ON_RECEIPT:
        note = (
            f"Оплата при получении: {to_pay} грн (наличные или карта). Отметьте оплату "
            "в карточке заказа, когда гость заплатит."
        )
    elif to_pay > 0:
        note = (
            f"Гость сообщил о переводе {to_pay} грн. Проверьте поступление и "
            "подтвердите оплату в карточке заказа."
        )
    else:
        note = "Оплачен бонусами полностью."
    contact = f"Телефон: {guest.phone}."
    if is_delivery:
        contact += f"\nАдрес: {guest.delivery_address}."
        if guest.delivery_latitude is not None:
            contact += f"\nНа карте: {map_link(guest.delivery_latitude, guest.delivery_longitude)}"
    notify_staff_about_new_order(order, note=f"{contact}\n{note}")
    return Order.objects.select_related("partner").get(id=order.id)


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
        raise RemoteOrderError("У заказа нет действующего счёта.")
    return bill


def _check_actor(actor, partner_id) -> None:
    if (
        actor is None
        or not actor.is_active
        or str(actor.partner_id) != str(partner_id)
        or actor.role not in PAYMENT_CHECK_ROLES
    ):
        raise RemoteOrderError("Подтверждать оплату может владелец, менеджер или кассир.")


def _lock_remote_order(order: Order, payment: str = PREPAID) -> Order:
    order = (
        Order.objects.select_for_update(of=("self",))
        .select_related("partner")
        .get(id=order.id, partner_id=order.partner_id)
    )
    if order.payment_timing != payment:
        if payment == PREPAID:
            raise RemoteOrderError("У этого заказа нет оплаты переводом.")
        raise RemoteOrderError("Этот заказ оплачивается переводом заранее, а не при получении.")
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
        raise RemoteOrderError("Оплата по этому заказу уже обработана.")
    if bill.status != Bill.Status.PAID:
        remaining = _money(bill.total_amount - bill.paid_amount)
        record_payment(
            bill=bill,
            amount=remaining,
            method=Payment.Method.TRANSFER,
            created_by=actor,
            comment=f"Перевод за заказ #{order.public_id} подтверждён.",
        )
    order.refresh_from_db()
    return transition_order_status(
        order=order,
        to_status=Order.Status.ACCEPTED,
        actor_user=actor,
        note="Оплата переводом подтверждена.",
    )


@transaction.atomic
def reject_transfer(*, order: Order, actor, comment: str = "") -> Order:
    """The transfer did not arrive: cancel the order and return bonuses."""

    _check_actor(actor, order.partner_id)
    bill = _order_bill(order)
    order = _lock_remote_order(order)
    if not awaits_transfer_check(order):
        raise RemoteOrderError("Оплата по этому заказу уже обработана.")
    if bill.status == Bill.Status.PAID or bill.paid_amount > 0:
        raise RemoteOrderError("По заказу уже есть оплата — отменить его так нельзя.")
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
        note="Оплата переводом не поступила.",
    )


@transaction.atomic
def record_payment_on_receipt(*, order: Order, actor, method: str) -> Order:
    """The guest paid on receipt: record the rest of the bill in cash or by card.

    Lock order as for any payment: Bill → Order (AGENTS.md); the bill lock
    makes a repeated press see the bill already paid.
    """

    _check_actor(actor, order.partner_id)
    if method not in ON_RECEIPT_METHODS:
        raise RemoteOrderError("При получении принимаем наличные или карту — выберите способ.")
    bill = _order_bill(order)
    order = _lock_remote_order(order, ON_RECEIPT)
    if bill.status == Bill.Status.PAID or order.paid_at is not None:
        raise RemoteOrderError("Заказ уже оплачен.")
    record_payment(
        bill=bill,
        amount=_money(bill.total_amount - bill.paid_amount),
        method=method,
        created_by=actor,
        comment=f"Оплата при получении за заказ #{order.public_id}.",
    )
    return Order.objects.select_related("partner").get(id=order.id)


@transaction.atomic
def cancel_unpaid_remote_order(*, order: Order, actor, comment: str = "") -> Order:
    """The guest did not take an order paid on receipt: cancel it, return bonuses."""

    _check_actor(actor, order.partner_id)
    bill = _order_bill(order)
    order = _lock_remote_order(order, ON_RECEIPT)
    if order.status in {Order.Status.COMPLETED, Order.Status.CANCELED}:
        raise RemoteOrderError("Заказ уже закрыт.")
    if bill.status == Bill.Status.PAID or bill.payments.exclude(
        method=Payment.Method.BONUSES
    ).filter(status=Payment.Status.PAID).exists():
        raise RemoteOrderError("По заказу уже есть оплата — отменить его так нельзя.")
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
        note="Гость не забрал заказ.",
    )


__all__ = [
    "CHANNEL_TITLES",
    "REMOTE_CHANNELS",
    "TIME_SLOTS",
    "RemoteOrderError",
    "RemoteQuote",
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
    "cancel_unpaid_remote_order",
    "payment_modes_for",
    "record_payment_on_receipt",
    "requested_time_label",
    "place_remote_order",
    "reject_transfer",
    "save_guest_delivery_address",
    "save_guest_delivery_location",
    "save_guest_phone",
]
