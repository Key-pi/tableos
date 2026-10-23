"""Pickup ordering: quote, place, confirm or reject the guest's transfer.

Owner decisions (2026-10-23, docs/architecture/PICKUP.md):

- the guest pays in advance by bank transfer to the venue's requisites
  (``PartnerBotSettings.pickup_payment_details``) and may spend bonuses;
- pickup time: as soon as possible, or in 30 / 60 minutes;
- the guest's phone is required (shared once through Telegram);
- orders are accepted only within the venue's opening hours.

A pickup order is placed when the guest says they have paid. It gets a
personal bill at once; bonuses, if chosen, are applied to that bill. Staff
then confirm the transfer (the money part is recorded as a ``transfer``
payment and the order is accepted) or reject it (order cancelled, bill
voided, bonuses returned).
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
    create_order,
    notify_staff_about_new_order,
    pickup_time_label,
    transition_order_status,
)
from apps.partners.models import Partner, PartnerBotSettings
from apps.partners.opening_hours import describe_opening_hours, is_open_at
from apps.users.models import GuestProfile, User

# Slot code -> minutes from now. ``now`` means as soon as possible.
PICKUP_SLOTS = {"now": 0, "30": 30, "60": 60}

# Staff roles that may confirm or reject a guest's transfer.
PAYMENT_CHECK_ROLES = frozenset({User.Role.OWNER, User.Role.MANAGER, User.Role.CASHIER})


class PickupError(BillingServiceError):
    """A pickup step cannot be completed; the message is shown to the user."""


@dataclass(frozen=True)
class PickupQuote:
    total: Decimal
    bonus_available: Decimal
    bonus_to_spend: Decimal
    to_pay: Decimal
    payment_details: str
    slots: tuple[str, ...]


def _money(value) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"))


def _settings(partner_id) -> PartnerBotSettings:
    settings = PartnerBotSettings.objects.filter(partner_id=partner_id).first()
    if settings is None or not (
        settings.module_pickup_enabled
        and settings.module_orders_enabled
        and settings.module_menu_enabled
    ):
        raise PickupError("Самовывоз сейчас отключён.")
    if not settings.pickup_payment_details.strip():
        raise PickupError("Заведение ещё не указало реквизиты для оплаты самовывоза.")
    return settings


def _guest(partner_id, telegram_id: int, *, lock: bool = False) -> GuestProfile:
    queryset = GuestProfile.objects.select_related("telegram_account")
    if lock:
        queryset = queryset.select_for_update(of=("self",))
    guest = queryset.filter(
        partner_id=partner_id,
        telegram_account__telegram_id=telegram_id,
    ).first()
    if guest is None:
        raise PickupError("Сначала нажмите /start, чтобы бот вас узнал.")
    return guest


def _active_cart(partner_id, guest: GuestProfile, *, lock: bool = False) -> Cart:
    queryset = Cart.objects.prefetch_related("items")
    if lock:
        queryset = queryset.select_for_update(of=("self",))
    cart = (
        queryset.filter(partner_id=partner_id, guest=guest, status=Cart.Status.ACTIVE)
        .order_by("-updated_at")
        .first()
    )
    if cart is None or not cart.items.all():
        raise PickupError("Корзина пуста. Добавьте позиции из меню.")
    return cart


def closed_message(partner: Partner) -> str:
    hours = describe_opening_hours(partner)
    if hours:
        return f"Сейчас заказы на самовывоз не принимаем. Часы работы: {hours}."
    return "Сейчас заказы на самовывоз не принимаем."


def available_slots(partner: Partner, now: datetime | None = None) -> tuple[str, ...]:
    """Slots whose pickup moment falls within opening hours; none if closed now."""

    now = now or timezone.now()
    if not is_open_at(partner, now):
        return ()
    return tuple(
        code
        for code, minutes in PICKUP_SLOTS.items()
        if is_open_at(partner, now + timedelta(minutes=minutes))
    )


def get_pickup_quote(*, partner_id, telegram_id: int, use_bonuses: bool) -> PickupQuote:
    settings = _settings(partner_id)
    guest = _guest(partner_id, telegram_id)
    cart = _active_cart(partner_id, guest)
    total = _money(cart.total_amount)
    bonus_available = get_redeemable_bonus_amount(
        partner_id=partner_id,
        guest=guest,
        purchase_total=total,
    )
    bonus_to_spend = bonus_available if use_bonuses else Decimal("0.00")
    return PickupQuote(
        total=total,
        bonus_available=bonus_available,
        bonus_to_spend=bonus_to_spend,
        to_pay=_money(total - bonus_to_spend),
        payment_details=settings.pickup_payment_details.strip(),
        slots=available_slots(settings.partner),
    )


@transaction.atomic
def save_guest_phone(*, partner_id, telegram_id: int, phone: str) -> GuestProfile:
    phone = (phone or "").strip()
    if not phone:
        raise PickupError("Номер телефона пустой.")
    guest = _guest(partner_id, telegram_id, lock=True)
    guest.phone = phone[:32]
    guest.save(update_fields=["phone", "updated_at"])
    return guest


@transaction.atomic
def place_pickup_order(
    *,
    partner_id,
    telegram_id: int,
    slot: str,
    use_bonuses: bool,
    expected_total: Decimal | str,
    expected_to_pay: Decimal | str,
    now: datetime | None = None,
) -> Order:
    """Turn the guest's cart into a pickup order once they say they paid.

    ``expected_total`` / ``expected_to_pay`` are the amounts the guest saw;
    if the cart or the bonus amount changed since, nothing is created and the
    guest is asked to check the new amount.
    """

    now = now or timezone.now()
    settings = _settings(partner_id)
    partner = settings.partner
    if slot not in PICKUP_SLOTS:
        raise PickupError("Выберите время самовывоза ещё раз.")
    if slot not in available_slots(partner, now):
        raise PickupError(closed_message(partner))
    guest = _guest(partner_id, telegram_id, lock=True)
    if not guest.phone:
        raise PickupError("Поделитесь номером телефона, чтобы оформить самовывоз.")
    cart = _active_cart(partner_id, guest, lock=True)
    cart_items = list(cart.items.all())
    total = _money(sum((item.unit_price * item.quantity for item in cart_items), Decimal("0")))
    if total != _money(expected_total):
        raise PickupError("Корзина изменилась — проверьте новую сумму.")

    minutes = PICKUP_SLOTS[slot]
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
        channel=Order.Channel.PICKUP,
        pickup_at=now + timedelta(minutes=minutes) if minutes else None,
        notify_staff=False,
    )
    cart.status = Cart.Status.CHECKED_OUT
    cart.checked_out_at = now
    cart.save(update_fields=["status", "checked_out_at", "updated_at"])

    bill = create_bill_from_orders(
        partner_id=partner_id,
        order_ids=[str(order.id)],
        kind=Bill.Kind.PERSONAL,
        label=f"Самовывоз #{order.public_id}",
    )
    bill = issue_bill(bill)
    if use_bonuses:
        try:
            result = pay_bill_with_bonuses(bill=bill)
        except BillingServiceError as exc:
            raise PickupError("Бонусы сейчас списать нельзя — проверьте сумму ещё раз.") from exc
        bill = result.bill
    bill.refresh_from_db()
    to_pay = _money(bill.total_amount - bill.paid_amount)
    if bill.status == Bill.Status.PAID:
        to_pay = Decimal("0.00")
    if to_pay != _money(expected_to_pay):
        raise PickupError("Сумма к оплате изменилась — проверьте её ещё раз.")

    if to_pay > 0:
        note = (
            f"Гость сообщил о переводе {to_pay} грн. Проверьте поступление и "
            "подтвердите оплату в карточке заказа."
        )
    else:
        note = "Оплачен бонусами полностью."
    notify_staff_about_new_order(order, note=f"Телефон: {guest.phone}. {note}")
    return Order.objects.select_related("partner").get(id=order.id)


def _pickup_bill(order: Order) -> Bill:
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
        raise PickupError("У заказа нет действующего счёта.")
    return bill


def _check_actor(actor, partner_id) -> None:
    if (
        actor is None
        or not actor.is_active
        or str(actor.partner_id) != str(partner_id)
        or actor.role not in PAYMENT_CHECK_ROLES
    ):
        raise PickupError("Подтверждать оплату может владелец, менеджер или кассир.")


def _lock_pickup_order(order: Order) -> Order:
    order = (
        Order.objects.select_for_update(of=("self",))
        .select_related("partner")
        .get(id=order.id, partner_id=order.partner_id)
    )
    if order.channel != Order.Channel.PICKUP:
        raise PickupError("Это не заказ на самовывоз.")
    return order


def pickup_awaits_payment(order: Order) -> bool:
    return (
        order.channel == Order.Channel.PICKUP
        and order.paid_at is None
        and order.status == Order.Status.NEW
    )


@transaction.atomic
def confirm_pickup_payment(*, order: Order, actor) -> Order:
    """The transfer arrived: record it and take the order into work."""

    _check_actor(actor, order.partner_id)
    bill = _pickup_bill(order)
    order = _lock_pickup_order(order)
    if not pickup_awaits_payment(order):
        raise PickupError("Оплата по этому заказу уже обработана.")
    if bill.status != Bill.Status.PAID:
        remaining = _money(bill.total_amount - bill.paid_amount)
        record_payment(
            bill=bill,
            amount=remaining,
            method=Payment.Method.TRANSFER,
            created_by=actor,
            comment=f"Перевод за самовывоз #{order.public_id} подтверждён.",
        )
    order.refresh_from_db()
    return transition_order_status(
        order=order,
        to_status=Order.Status.ACCEPTED,
        actor_user=actor,
        note="Оплата переводом подтверждена.",
    )


@transaction.atomic
def reject_pickup_payment(*, order: Order, actor, comment: str = "") -> Order:
    """The transfer did not arrive: cancel the order and return bonuses."""

    _check_actor(actor, order.partner_id)
    bill = _pickup_bill(order)
    order = _lock_pickup_order(order)
    if not pickup_awaits_payment(order):
        raise PickupError("Оплата по этому заказу уже обработана.")
    if bill.status == Bill.Status.PAID or bill.paid_amount > 0:
        raise PickupError("По заказу уже есть оплата — отменить его так нельзя.")
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


__all__ = [
    "PICKUP_SLOTS",
    "PickupError",
    "PickupQuote",
    "available_slots",
    "closed_message",
    "confirm_pickup_payment",
    "get_pickup_quote",
    "pickup_awaits_payment",
    "pickup_time_label",
    "place_pickup_order",
    "reject_pickup_payment",
    "save_guest_phone",
]
