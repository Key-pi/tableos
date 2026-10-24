from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.billing.models import BillItem
from apps.employees.models import EmployeeProfile
from apps.menu.models import MenuItem
from apps.notifications.models import StaffNotification
from apps.notifications.services import deliver_staff_notification, send_guest_order_status_update
from apps.orders.models import Cart, CartItem, Order, OrderItem, OrderStatusHistory
from apps.partners.models import PartnerBotSettings
from apps.tables.models import TableSession
from apps.tables.services import get_active_table_session
from apps.users.models import GuestProfile, User


class OrderFlowError(Exception):
    """Raised when an order cannot be created under current business rules."""


ORDER_STATUS_TRANSITIONS: dict[str, set[str]] = {
    # Keep transitions explicit so staff flows stay predictable and auditable.
    Order.Status.NEW: {Order.Status.ACCEPTED, Order.Status.CANCELED},
    Order.Status.ACCEPTED: {Order.Status.PREPARING, Order.Status.READY, Order.Status.CANCELED},
    Order.Status.PREPARING: {Order.Status.READY, Order.Status.CANCELED},
    Order.Status.READY: {Order.Status.DELIVERING, Order.Status.COMPLETED, Order.Status.CANCELED},
    Order.Status.DELIVERING: {Order.Status.COMPLETED, Order.Status.CANCELED},
    Order.Status.COMPLETED: set(),
    Order.Status.CANCELED: set(),
}


# PQ-029: a guest may confirm receipt from any non-final status, including
# `new`, because staff may not update orders in time during rush hours. This is
# the only guest-initiated transition; staff transitions use the table above.
GUEST_RECEIPT_CONFIRMABLE_STATUSES: frozenset[str] = frozenset(
    {
        Order.Status.NEW,
        Order.Status.ACCEPTED,
        Order.Status.PREPARING,
        Order.Status.READY,
        Order.Status.DELIVERING,
    }
)


def can_guest_confirm_receipt(order: Order) -> bool:
    """Return whether the guest may mark this order as received."""

    return order.status in GUEST_RECEIPT_CONFIRMABLE_STATUSES


def get_available_next_statuses(current_status: str) -> list[str]:
    """Return allowed next statuses in a stable order for UI consumers."""

    ordered_statuses = [
        Order.Status.ACCEPTED,
        Order.Status.PREPARING,
        Order.Status.READY,
        Order.Status.DELIVERING,
        Order.Status.COMPLETED,
        Order.Status.CANCELED,
    ]
    allowed = ORDER_STATUS_TRANSITIONS[current_status]
    return [status for status in ordered_statuses if status in allowed]


def get_available_staff_actions(current_status: str, channel: str | None = None) -> list[str]:
    """Return compact operational actions for staff-facing bot screens.

    Delivery (PQ-032) adds a step: a ready order goes «в пути» first, then
    «доставлено».
    """

    if current_status == Order.Status.NEW:
        return [Order.Status.ACCEPTED, Order.Status.CANCELED]
    if current_status in {Order.Status.ACCEPTED, Order.Status.PREPARING}:
        return [Order.Status.READY, Order.Status.CANCELED]
    if (
        channel in {Order.Channel.DELIVERY, Order.Channel.SHIPPING}
        and current_status == Order.Status.READY
    ):
        # Shipping (PQ-034): «Отправлен» asks for the ТТН first (staff bot).
        return [Order.Status.DELIVERING, Order.Status.CANCELED]
    if current_status in {Order.Status.READY, Order.Status.DELIVERING}:
        return [Order.Status.COMPLETED, Order.Status.CANCELED]
    return []


def is_order_paid(order: Order) -> bool:
    """Return whether the order already has a recorded settlement."""

    return order.paid_at is not None


def is_order_received(order: Order) -> bool:
    """Return whether the guest already confirmed receiving the order."""

    return order.received_at is not None or order.status == Order.Status.COMPLETED


def is_order_final(order: Order) -> bool:
    """Return whether the order is both received and paid."""

    return (
        order.status != Order.Status.CANCELED
        and is_order_paid(order)
        and is_order_received(order)
    )


def parse_order_input(raw_value: str) -> list[tuple[str, int]]:
    entries: list[tuple[str, int]] = []
    chunks = [chunk.strip() for chunk in raw_value.split(",") if chunk.strip()]
    if not chunks:
        raise OrderFlowError(
            "Укажите позиции в формате `code x qty`, например: `a1b2c3d4x2,e5f6a7b8x1`."
        )

    for chunk in chunks:
        normalized = chunk.replace(" ", "")
        parts = normalized.split("x", 1)
        item_code = parts[0].strip()
        if not item_code:
            raise OrderFlowError(f"Не удалось распознать позицию `{chunk}`.")

        quantity = 1
        if len(parts) == 2 and parts[1]:
            try:
                quantity = int(parts[1])
            except ValueError as exc:
                raise OrderFlowError(f"Некорректное количество в `{chunk}`.") from exc

        if quantity <= 0:
            raise OrderFlowError("Количество должно быть больше нуля.")

        entries.append((item_code, quantity))

    return entries


def create_order_from_session(
    *,
    partner_id,
    table_session: TableSession,
    items: list[dict],
    comment: str = "",
) -> Order:
    return create_order(
        partner_id=partner_id,
        guest=table_session.guest,
        table=table_session.table,
        table_session=table_session,
        items=items,
        comment=comment,
    )


def _validate_order_tenant_context(
    *,
    partner_id,
    guest,
    table,
    table_session: TableSession | None,
    items: list[dict],
) -> None:
    partner_scope_id = str(partner_id)
    if str(guest.partner_id) != partner_scope_id:
        raise OrderFlowError("Гость заказа относится к другому заведению.")

    if table_session is not None:
        if str(table_session.partner_id) != partner_scope_id:
            raise OrderFlowError("Сессия заказа относится к другому заведению.")
        if table_session.guest_id != guest.id:
            raise OrderFlowError("Гость заказа не совпадает с гостем активной сессии.")
        if table is not None and table_session.table_id != table.id:
            raise OrderFlowError("Стол заказа не совпадает со столом активной сессии.")

    if table is not None and str(table.partner_id) != partner_scope_id:
        raise OrderFlowError("Стол заказа относится к другому заведению.")

    menu_item_ids = {item_payload["menu_item_id"] for item_payload in items}
    if MenuItem.objects.filter(partner_id=partner_id, id__in=menu_item_ids).count() != len(
        menu_item_ids
    ):
        raise OrderFlowError("Позиция заказа относится к другому заведению.")


@transaction.atomic
def create_order(
    *,
    partner_id,
    guest,
    items: list[dict],
    table=None,
    table_session: TableSession | None = None,
    comment: str = "",
    channel: str = Order.Channel.TABLE,
    requested_for=None,
    notify_staff: bool = True,
    delivery_address: str = "",
    delivery_latitude=None,
    delivery_longitude=None,
    delivery_fee: Decimal | str | None = None,
    payment_timing: str = "",
    shipping_carrier: str = "",
    shipping_destination: str = "",
    shipping_cost: str = "",
) -> Order:
    if not items:
        raise OrderFlowError("Нельзя создать заказ без позиций.")
    if payment_timing and payment_timing not in Order.PaymentTiming.values:
        raise OrderFlowError("Неизвестный способ оплаты заказа.")
    if bool(payment_timing) != (channel in Order.REMOTE_CHANNELS):
        raise OrderFlowError("Когда платить, выбирают только в заказах навынос.")
    if channel not in Order.Channel.values:
        raise OrderFlowError("Неизвестный способ получения заказа.")
    if channel != Order.Channel.TABLE and (table is not None or table_session is not None):
        raise OrderFlowError("Заказ навынос не привязывается к столу.")
    if channel == Order.Channel.TABLE and requested_for is not None:
        raise OrderFlowError("Время получения указывается только для заказов навынос.")
    is_delivery = channel == Order.Channel.DELIVERY
    is_shipping = channel == Order.Channel.SHIPPING
    if is_delivery and not delivery_address.strip():
        raise OrderFlowError("Для доставки нужен адрес.")
    if not is_delivery and (delivery_address or delivery_latitude is not None):
        raise OrderFlowError("Адрес доставки бывает только у доставки.")
    if delivery_fee is not None and not (is_delivery or is_shipping):
        raise OrderFlowError("Стоимость доставки бывает только у доставки и отправки.")
    if is_shipping and not (
        shipping_carrier in Order.Carrier.values
        and shipping_destination.strip()
        and shipping_cost in Order.ShippingCost.values
    ):
        raise OrderFlowError("Для отправки нужны служба, получатель и вариант пересылки.")
    if not is_shipping and (shipping_carrier or shipping_destination or shipping_cost):
        raise OrderFlowError("Служба и получатель бывают только у отправки.")
    if table_session is not None:
        if table is None:
            table = table_session.table
    _validate_order_tenant_context(
        partner_id=partner_id,
        guest=guest,
        table=table,
        table_session=table_session,
        items=items,
    )

    order = Order.objects.create(
        partner_id=partner_id,
        guest=guest,
        table=table,
        table_session=table_session,
        comment=comment,
        channel=channel,
        requested_for=requested_for,
        delivery_address=delivery_address.strip(),
        delivery_latitude=delivery_latitude,
        delivery_longitude=delivery_longitude,
        payment_timing=payment_timing,
        shipping_carrier=shipping_carrier,
        shipping_destination=shipping_destination.strip(),
        shipping_cost=shipping_cost,
    )

    subtotal = Decimal("0.00")
    for item_payload in items:
        quantity = item_payload["quantity"]
        unit_price = Decimal(item_payload["unit_price"]).quantize(Decimal("0.01"))
        subtotal += unit_price * quantity
        OrderItem.objects.create(
            partner_id=partner_id,
            order=order,
            menu_item_id=item_payload["menu_item_id"],
            item_name=item_payload["item_name"],
            unit_price=unit_price,
            quantity=quantity,
            comment=item_payload.get("comment", ""),
        )
    fee = Decimal(delivery_fee or 0).quantize(Decimal("0.01"))
    if fee < 0:
        raise OrderFlowError("Стоимость доставки не может быть отрицательной.")
    if fee > 0:
        # The fee is an ordinary order line (PQ-032; postage by post, PQ-034),
        # so bills, bonuses and reports treat it like any other line.
        subtotal += fee
        OrderItem.objects.create(
            partner_id=partner_id,
            order=order,
            kind=OrderItem.Kind.DELIVERY_FEE,
            menu_item=None,
            item_name="Пересылка" if is_shipping else "Доставка",
            unit_price=fee,
            quantity=1,
        )

    order.subtotal_amount = subtotal
    order.total_amount = subtotal
    order.save(update_fields=["subtotal_amount", "total_amount", "updated_at"])

    OrderStatusHistory.objects.create(
        partner_id=partner_id,
        order=order,
        from_status="",
        to_status=order.status,
        note="Order created",
    )
    if notify_staff:
        _create_staff_notifications_for_order_created(order)
    return order


def notify_staff_about_new_order(order: Order, *, note: str = "") -> None:
    """Notify staff about a created order; callers that build the order in
    several steps (pickup) call this once the order is complete."""

    _create_staff_notifications_for_order_created(order, note=note)


@transaction.atomic
def transition_order_status(
    *,
    order: Order,
    to_status: str,
    actor_user: User | None = None,
    note: str = "",
) -> Order:
    try:
        order = Order.objects.select_for_update().get(
            id=order.id,
            partner_id=order.partner_id,
        )
    except Order.DoesNotExist as exc:
        raise OrderFlowError("Заказ не найден в этом заведении.") from exc

    allowed_statuses = ORDER_STATUS_TRANSITIONS[order.status]
    if to_status not in allowed_statuses:
        raise OrderFlowError(f"Нельзя перевести заказ из `{order.status}` в `{to_status}`.")
    if (
        order.payment_timing == Order.PaymentTiming.PREPAID
        and to_status == Order.Status.ACCEPTED
        and order.paid_at is None
    ):
        # An order paid in advance by transfer (pickup, delivery) goes into
        # work only after the transfer is confirmed
        # (apps/billing/remote_orders.py:confirm_transfer). One paid on
        # receipt is accepted as usual (PQ-033).
        raise OrderFlowError("Сначала подтвердите оплату переводом.")
    if (
        order.channel == Order.Channel.SHIPPING
        and to_status == Order.Status.DELIVERING
        and not order.tracking_number
    ):
        # Shipped means the parcel has a ТТН the guest can track
        # (apps/billing/remote_orders.py:mark_order_shipped).
        raise OrderFlowError("Сначала введите ТТН отправления.")
    if to_status == Order.Status.CANCELED and BillItem.objects.filter(
        partner_id=order.partner_id,
        order_id=order.id,
        is_active=True,
        # Refunded lines (PQ-037) no longer hold the order.
        refund_line__isnull=True,
    ).exists():
        # Checked after the order lock, so a concurrently created bill is seen.
        raise OrderFlowError(
            "Заказ уже в счёте. Отменить его можно через карточку счёта "
            "(нужно право изменять счета)."
        )

    from_status = order.status
    responsible_employee = _resolve_actor_employee(order=order, actor_user=actor_user)
    order.status = to_status
    if to_status == Order.Status.ACCEPTED and responsible_employee is not None:
        order.assigned_employee = responsible_employee
        if order.accepted_at is None:
            order.accepted_at = timezone.now()
    update_fields = ["status", "updated_at"]
    if to_status == Order.Status.COMPLETED and order.received_at is None:
        order.received_at = timezone.now()
        update_fields.append("received_at")
    if responsible_employee is not None and to_status == Order.Status.ACCEPTED:
        update_fields.append("assigned_employee")
        if order.accepted_at is not None:
            update_fields.append("accepted_at")
    order.save(update_fields=update_fields)

    OrderStatusHistory.objects.create(
        partner_id=order.partner_id,
        order=order,
        from_status=from_status,
        to_status=to_status,
        changed_by=actor_user,
        note=note,
    )
    _create_staff_notifications_for_status_change(
        order=order,
        from_status=from_status,
        to_status=to_status,
    )
    send_guest_order_status_update(
        order=order,
        from_status=from_status,
        to_status=to_status,
    )
    return order


@transaction.atomic
def confirm_order_received_by_guest(
    *,
    partner_id: UUID | str,
    telegram_id: int,
    order_public_id: str,
) -> Order:
    table_session = get_active_table_session(partner_id=partner_id, telegram_id=telegram_id)
    if table_session is not None:
        lookup = {"guest": table_session.guest, "table_session": table_session}
    else:
        # A parcel sent by post (PQ-034) is confirmed by its guest from the
        # «отправлен» message, with no table.
        lookup = {
            "guest__telegram_account__telegram_id": telegram_id,
            "channel": Order.Channel.SHIPPING,
        }

    try:
        order = Order.objects.select_for_update(of=("self",)).get(
            partner_id=partner_id,
            public_id=order_public_id,
            **lookup,
        )
    except Order.DoesNotExist as exc:
        if table_session is None:
            raise OrderFlowError("Подтвердить получение можно только с активным столом.") from exc
        raise OrderFlowError("Заказ не найден для этого гостя и стола.") from exc

    if not can_guest_confirm_receipt(order):
        raise OrderFlowError("Этот заказ уже завершён или отменён.")

    from_status = order.status
    order.status = Order.Status.COMPLETED
    if order.received_at is None:
        order.received_at = timezone.now()
        update_fields = ["status", "received_at", "updated_at"]
    else:
        update_fields = ["status", "updated_at"]
    order.save(update_fields=update_fields)

    OrderStatusHistory.objects.create(
        partner_id=order.partner_id,
        order=order,
        from_status=from_status,
        to_status=order.status,
        note="Guest action: confirmed as received.",
    )
    _create_staff_notifications_for_status_change(
        order=order,
        from_status=from_status,
        to_status=order.status,
    )
    send_guest_order_status_update(
        order=order,
        from_status=from_status,
        to_status=order.status,
    )
    return order


def _resolve_actor_employee(*, order: Order, actor_user: User | None) -> EmployeeProfile | None:
    if actor_user is None or not actor_user.is_active:
        return None
    try:
        employee = actor_user.employee_profile
    except EmployeeProfile.DoesNotExist:
        return None
    if (
        employee.partner_id != order.partner_id
        or employee.partner_id != actor_user.partner_id
        or not employee.is_active
    ):
        return None
    return employee


# Channels where the guest is not at a table, in the order a cart without a
# table falls back to them, and the setting that turns each one on.
REMOTE_CART_CHANNELS = {
    Order.Channel.PICKUP: "module_pickup_enabled",
    Order.Channel.DELIVERY: "module_delivery_enabled",
    Order.Channel.SHIPPING: "module_shipping_enabled",
}


def _enabled_remote_channels(partner_id) -> list[str]:
    settings = PartnerBotSettings.objects.filter(
        partner_id=partner_id,
        module_menu_enabled=True,
        module_orders_enabled=True,
        module_cart_enabled=True,
    ).first()
    if settings is None:
        return []
    return [channel for channel, flag in REMOTE_CART_CHANNELS.items() if getattr(settings, flag)]


def _default_remote_channel(partner_id) -> str:
    channels = _enabled_remote_channels(partner_id)
    return channels[0] if channels else Order.Channel.PICKUP


def _resolve_cart_guest(*, partner_id, telegram_id: int):
    """The guest whose cart is used, and their active table session if any.

    At a table the cart belongs to the table session. Without a session a
    cart is allowed only when the venue offers pickup or delivery.
    """

    table_session = get_active_table_session(partner_id=partner_id, telegram_id=telegram_id)
    if table_session is not None:
        return table_session.guest, table_session
    if not _enabled_remote_channels(partner_id):
        raise OrderFlowError(
            "Корзина доступна только после сканирования QR-кода стола. Сначала откройте сессию."
        )
    guest = (
        GuestProfile.objects.select_related("telegram_account")
        .filter(partner_id=partner_id, telegram_account__telegram_id=telegram_id)
        .first()
    )
    if guest is None:
        raise OrderFlowError("Сначала нажмите /start, чтобы бот вас узнал.")
    return guest, None


CHANNEL_NAMES = {
    Order.Channel.TABLE: "к столу",
    Order.Channel.PICKUP: "на самовывоз",
    Order.Channel.DELIVERY: "на доставку",
    Order.Channel.SHIPPING: "на отправку почтой",
}
CHANNEL_OFF_MESSAGES = {
    Order.Channel.PICKUP: "Самовывоз сейчас отключён.",
    Order.Channel.DELIVERY: "Доставка сейчас отключена.",
    Order.Channel.SHIPPING: "Отправка почтой сейчас отключена.",
}


def _align_cart_with_session(cart: Cart, table_session: TableSession | None) -> Cart | None:
    """Keep the cart consistent with where the guest is now.

    The cart's channel is the flow the guest chose (``switch_cart_channel``).

    - table channel at a table: a cart of another session is abandoned if it
      has items, otherwise moved to the current session;
    - pickup and delivery: never tied to a session, even while the guest
      sits at a table (they ordered to take away);
    - table channel without a session (the guest left): the cart becomes a
      pickup cart (or delivery when only delivery is on) and keeps its items.
    """

    if cart.channel in REMOTE_CART_CHANNELS:
        if cart.table_session_id is not None:
            cart.table_session = None
            cart.save(update_fields=["table_session", "updated_at"])
        return cart
    if table_session is not None:
        if cart.table_session_id == table_session.id:
            return cart
        if cart.table_session_id is not None and cart.items.exists():
            cart.status = Cart.Status.ABANDONED
            cart.save(update_fields=["status", "updated_at"])
            return None
        cart.table_session = table_session
        cart.save(update_fields=["table_session", "updated_at"])
        return cart
    cart.table_session = None
    cart.channel = _default_remote_channel(cart.partner_id)
    cart.save(update_fields=["table_session", "channel", "updated_at"])
    return cart


def _new_cart_channel(table_session: TableSession | None, partner_id) -> str:
    if table_session is not None:
        return Order.Channel.TABLE
    return _default_remote_channel(partner_id)


@transaction.atomic
def get_or_create_active_cart_for_telegram_user(
    *,
    partner_id: UUID | str,
    telegram_id: int,
) -> Cart:
    guest, table_session = _resolve_cart_guest(partner_id=partner_id, telegram_id=telegram_id)

    def locked_active_cart():
        return (
            Cart.objects.select_for_update()
            .filter(partner_id=partner_id, guest=guest, status=Cart.Status.ACTIVE)
            .order_by("-updated_at")
            .first()
        )

    cart = locked_active_cart()
    if cart is not None:
        cart = _align_cart_with_session(cart, table_session)
        if cart is not None:
            return cart

    try:
        with transaction.atomic():
            return Cart.objects.create(
                partner_id=partner_id,
                guest=guest,
                table_session=table_session,
                channel=_new_cart_channel(table_session, partner_id),
            )
    except IntegrityError:
        cart = locked_active_cart()
        if cart is None:
            raise
        aligned = _align_cart_with_session(cart, table_session)
        if aligned is not None:
            return aligned
        return Cart.objects.create(
            partner_id=partner_id,
            guest=guest,
            table_session=table_session,
            channel=_new_cart_channel(table_session, partner_id),
        )


def _remove_items_not_sold_in(cart: Cart) -> list[str]:
    removed = []
    for item in cart.items.select_related("menu_item"):
        menu_item = item.menu_item
        if menu_item.is_available and menu_item.is_sold_in(cart.channel):
            continue
        removed.append(item.item_name)
        item.delete()
    if removed:
        _refresh_cart_totals(cart)
    return removed


@transaction.atomic
def switch_cart_channel_for_telegram_user(
    *,
    partner_id: UUID | str,
    telegram_id: int,
    channel: str,
) -> tuple[Cart, list[str]]:
    """The guest picks a flow (table, pickup or delivery). Returns the cart
    and the names of items removed because they are not sold in that channel."""

    if channel in REMOTE_CART_CHANNELS and channel not in _enabled_remote_channels(partner_id):
        raise OrderFlowError(CHANNEL_OFF_MESSAGES[channel])
    table_session = get_active_table_session(partner_id=partner_id, telegram_id=telegram_id)
    if channel == Order.Channel.TABLE and table_session is None:
        raise OrderFlowError("Сначала отсканируйте QR-код стола.")
    if channel not in CHANNEL_NAMES:
        raise OrderFlowError("Этот способ заказа пока недоступен.")
    cart = get_or_create_active_cart_for_telegram_user(
        partner_id=partner_id,
        telegram_id=telegram_id,
    )
    if cart.channel != channel:
        cart.channel = channel
        cart.table_session = table_session if channel == Order.Channel.TABLE else None
        cart.save(update_fields=["channel", "table_session", "updated_at"])
        cart = _align_cart_with_session(cart, table_session) or cart
    removed = _remove_items_not_sold_in(cart)
    return (
        Cart.objects.select_related("table_session__table")
        .prefetch_related("items", "items__menu_item")
        .get(id=cart.id),
        removed,
    )


@transaction.atomic
def add_items_to_cart_for_telegram_user(
    *,
    partner_id: UUID | str,
    telegram_id: int,
    raw_items: str,
) -> Cart:
    cart = get_or_create_active_cart_for_telegram_user(
        partner_id=partner_id,
        telegram_id=telegram_id,
    )
    parsed_entries = parse_order_input(raw_items)
    items_payload = _build_items_payload(partner_id=partner_id, parsed_entries=parsed_entries)
    not_sold = [
        item.name
        for item in MenuItem.objects.filter(
            id__in=[payload["menu_item_id"] for payload in items_payload]
        )
        if not item.is_sold_in(cart.channel)
    ]
    if not_sold:
        raise OrderFlowError(
            f"Недоступно для заказа {CHANNEL_NAMES[cart.channel]}: {', '.join(not_sold)}."
        )

    for item_payload in items_payload:
        cart_item, created = CartItem.objects.get_or_create(
            partner_id=partner_id,
            cart=cart,
            menu_item_id=item_payload["menu_item_id"],
            defaults={
                "item_name": item_payload["item_name"],
                "unit_price": item_payload["unit_price"],
                "quantity": item_payload["quantity"],
            },
        )
        if not created:
            cart_item.quantity += item_payload["quantity"]
            cart_item.unit_price = item_payload["unit_price"]
            cart_item.item_name = item_payload["item_name"]
            cart_item.save(update_fields=["quantity", "unit_price", "item_name", "updated_at"])

    _refresh_cart_totals(cart)
    return Cart.objects.select_related("table_session__table").prefetch_related(
        "items",
        "items__menu_item",
    ).get(id=cart.id)


def get_active_cart_for_telegram_user(
    *,
    partner_id: UUID | str,
    telegram_id: int,
) -> Cart | None:
    try:
        guest, table_session = _resolve_cart_guest(
            partner_id=partner_id,
            telegram_id=telegram_id,
        )
    except OrderFlowError:
        return None

    cart = (
        Cart.objects.filter(
            partner_id=partner_id,
            guest=guest,
            status=Cart.Status.ACTIVE,
        )
        .order_by("-updated_at")
        .first()
    )
    if cart is None:
        return None
    if _align_cart_with_session(cart, table_session) is None:
        return None
    return (
        Cart.objects.select_related("table_session__table")
        .prefetch_related("items", "items__menu_item")
        .get(id=cart.id)
    )


@transaction.atomic
def change_cart_item_quantity_for_telegram_user(
    *,
    partner_id: UUID | str,
    telegram_id: int,
    item_public_id: str,
    delta: int,
) -> Cart:
    if delta == 0:
        raise OrderFlowError("Количество должно изменяться хотя бы на 1.")

    cart = get_or_create_active_cart_for_telegram_user(
        partner_id=partner_id,
        telegram_id=telegram_id,
    )
    try:
        cart_item = cart.items.select_related("menu_item").get(
            partner_id=partner_id,
            menu_item__public_id=item_public_id,
        )
    except CartItem.DoesNotExist as exc:
        raise OrderFlowError("Эта позиция не найдена в активной корзине.") from exc

    new_quantity = cart_item.quantity + delta
    if new_quantity <= 0:
        cart_item.delete()
    else:
        cart_item.quantity = new_quantity
        cart_item.save(update_fields=["quantity", "updated_at"])

    _refresh_cart_totals(cart)
    return Cart.objects.select_related("table_session__table").prefetch_related(
        "items",
        "items__menu_item",
    ).get(id=cart.id)


@transaction.atomic
def remove_cart_item_for_telegram_user(
    *,
    partner_id: UUID | str,
    telegram_id: int,
    item_public_id: str,
) -> Cart:
    cart = get_or_create_active_cart_for_telegram_user(
        partner_id=partner_id,
        telegram_id=telegram_id,
    )
    deleted_count, _details = cart.items.filter(
        partner_id=partner_id,
        menu_item__public_id=item_public_id,
    ).delete()
    if deleted_count == 0:
        raise OrderFlowError("Эта позиция уже отсутствует в корзине.")

    _refresh_cart_totals(cart)
    return Cart.objects.select_related("table_session__table").prefetch_related(
        "items",
        "items__menu_item",
    ).get(id=cart.id)


@transaction.atomic
def clear_active_cart_for_telegram_user(
    *,
    partner_id: UUID | str,
    telegram_id: int,
) -> None:
    cart = get_active_cart_for_telegram_user(partner_id=partner_id, telegram_id=telegram_id)
    if cart is None:
        raise OrderFlowError("Активная корзина пуста.")
    cart.items.all().delete()
    _refresh_cart_totals(cart)


@transaction.atomic
def checkout_active_cart_for_telegram_user(
    *,
    partner_id: UUID | str,
    telegram_id: int,
    comment: str = "",
) -> Order:
    cart = get_or_create_active_cart_for_telegram_user(
        partner_id=partner_id,
        telegram_id=telegram_id,
    )
    cart_items = list(cart.items.select_related("menu_item"))
    if not cart_items:
        raise OrderFlowError("Корзина пуста. Добавьте позиции перед оформлением.")
    if cart.channel != Order.Channel.TABLE or cart.table_session_id is None:
        raise OrderFlowError(
            "Корзина собрана не для стола. Оформите самовывоз или отсканируйте QR-код стола."
        )
    not_sold = [
        item.item_name for item in cart_items if not item.menu_item.is_sold_in(Order.Channel.TABLE)
    ]
    if not_sold:
        raise OrderFlowError(f"Недоступно для заказа к столу: {', '.join(not_sold)}.")

    order = create_order(
        partner_id=partner_id,
        guest=cart.guest,
        table=cart.table_session.table if cart.table_session_id else None,
        table_session=cart.table_session,
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
        comment=comment or cart.comment,
    )

    cart.status = Cart.Status.CHECKED_OUT
    cart.checked_out_at = timezone.now()
    cart.save(update_fields=["status", "checked_out_at", "updated_at"])
    return order


def _build_items_payload(
    *,
    partner_id: UUID | str,
    parsed_entries: list[tuple[str, int]],
) -> list[dict]:
    menu_item_codes = [item_code for item_code, _ in parsed_entries]
    lookup_codes = {
        variant
        for item_code in menu_item_codes
        for variant in (item_code, item_code.lower(), item_code.upper())
    }
    available_items = {
        item.public_id.lower(): item
        for item in MenuItem.objects.filter(
            partner_id=partner_id,
            public_id__in=lookup_codes,
            is_available=True,
            category__is_active=True,
        )
    }
    missing_codes = [
        item_code for item_code in menu_item_codes if item_code.lower() not in available_items
    ]
    if missing_codes:
        raise OrderFlowError(
            "Некоторые позиции недоступны или не принадлежат этому заведению: "
            f"{', '.join(missing_codes)}."
        )

    items_payload = []
    for item_code, quantity in parsed_entries:
        menu_item = available_items[item_code.lower()]
        items_payload.append(
            {
                "menu_item_id": menu_item.id,
                "item_name": menu_item.name,
                "unit_price": menu_item.price,
                "quantity": quantity,
            }
        )
    return items_payload


def _refresh_cart_totals(cart: Cart) -> Cart:
    subtotal = Decimal("0.00")
    for item in cart.items.all():
        subtotal += item.unit_price * item.quantity
    cart.subtotal_amount = subtotal
    cart.total_amount = subtotal
    cart.save(update_fields=["subtotal_amount", "total_amount", "updated_at"])
    return cart


def _order_location_label(order: Order) -> str:
    if order.channel == Order.Channel.PICKUP:
        return f"самовывоз, {requested_time_label(order)}"
    if order.channel == Order.Channel.DELIVERY:
        return f"доставка, {requested_time_label(order)}, {order.delivery_address}"
    if order.channel == Order.Channel.SHIPPING:
        return (
            f"отправка, {order.get_shipping_carrier_display()}, {order.shipping_destination}"
        )
    if order.table_id is not None:
        return f"стол #{order.table.number}"
    return "заказ без привязки к столу"


def requested_time_label(order: Order) -> str:
    if order.requested_for is None:
        return "как можно скорее"
    local = order.requested_for.astimezone(ZoneInfo(order.partner.timezone))
    return f"к {local:%H:%M}"


def _staff_notification_recipients(order: Order):
    # Staff notifications must stay partner-scoped even if one Telegram account
    # is reused by the same person across different venues.
    recipients = EmployeeProfile.objects.filter(
        partner_id=order.partner_id,
        is_active=True,
        bot_notifications_enabled=True,
        telegram_account__isnull=False,
        user__is_active=True,
        user__partner_id=order.partner_id,
    )
    # Each employee chooses the ordering modules they hear about.
    channel_field = EmployeeProfile.CHANNEL_NOTIFY_FIELDS.get(order.channel)
    if channel_field:
        recipients = recipients.filter(**{channel_field: True})
    return recipients


def _create_staff_notifications_for_order_created(order: Order, *, note: str = "") -> None:
    notifications = []
    for employee in _staff_notification_recipients(order).filter(
        notify_on_order_created=True,
    ):
        notifications.append(
            StaffNotification(
                partner_id=order.partner_id,
                employee=employee,
                order=order,
                category=StaffNotification.Category.ORDER_CREATED,
                title=f"Новый заказ #{order.public_id}",
                message=(
                    f"Поступил новый заказ: {_order_location_label(order)}, "
                    f"сумма {order.total_amount} грн."
                    + (f"\n{note}" if note else "")
                ),
            )
        )
    for notification in notifications:
        notification.save()
        deliver_staff_notification(notification)


def _create_staff_notifications_for_status_change(
    *,
    order: Order,
    from_status: str,
    to_status: str,
) -> None:
    notifications = []
    for employee in _staff_notification_recipients(order).filter(
        notify_on_order_status_changed=True,
    ):
        notifications.append(
            StaffNotification(
                partner_id=order.partner_id,
                employee=employee,
                order=order,
                category=StaffNotification.Category.ORDER_STATUS_CHANGED,
                title=f"Заказ #{order.public_id}: {to_status}",
                message=(
                    f"Статус заказа ({_order_location_label(order)}) "
                    f"изменён с {from_status or '-'} на {to_status}."
                ),
            )
        )
    for notification in notifications:
        notification.save()
        deliver_staff_notification(notification)
