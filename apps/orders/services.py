from decimal import Decimal
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from apps.employees.models import EmployeeProfile
from apps.menu.models import MenuItem
from apps.notifications.models import StaffNotification
from apps.notifications.services import deliver_staff_notification, send_guest_order_status_update
from apps.orders.models import Cart, CartItem, Order, OrderItem, OrderStatusHistory
from apps.tables.models import TableSession
from apps.tables.services import get_active_table_session
from apps.users.models import User


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


def get_available_staff_actions(current_status: str) -> list[str]:
    """Return compact operational actions for staff-facing bot screens."""

    if current_status == Order.Status.NEW:
        return [Order.Status.ACCEPTED, Order.Status.CANCELED]
    if current_status in {Order.Status.ACCEPTED, Order.Status.PREPARING}:
        return [Order.Status.READY, Order.Status.CANCELED]
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
    order = Order.objects.create(
        partner_id=partner_id,
        guest=table_session.guest,
        table=table_session.table,
        table_session=table_session,
        comment=comment,
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
    _create_staff_notifications_for_order_created(order)
    return order


@transaction.atomic
def transition_order_status(
    *,
    order: Order,
    to_status: str,
    actor_user: User | None = None,
    note: str = "",
) -> Order:
    allowed_statuses = ORDER_STATUS_TRANSITIONS[order.status]
    if to_status not in allowed_statuses:
        raise OrderFlowError(f"Нельзя перевести заказ из `{order.status}` в `{to_status}`.")

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
    if table_session is None:
        raise OrderFlowError("Подтвердить получение можно только с активным столом.")

    try:
        order = Order.objects.select_for_update().get(
            partner_id=partner_id,
            public_id=order_public_id,
            guest=table_session.guest,
            table_session=table_session,
        )
    except Order.DoesNotExist as exc:
        raise OrderFlowError("Заказ не найден для этого гостя и стола.") from exc

    if order.status in {Order.Status.CANCELED, Order.Status.COMPLETED}:
        raise OrderFlowError("Этот заказ уже завершён или отменён.")
    if order.status == Order.Status.NEW:
        raise OrderFlowError("Нельзя подтвердить получение нового заказа.")

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
        note="Confirmed as received by guest.",
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
    if actor_user is None:
        return None
    try:
        employee = actor_user.employee_profile
    except EmployeeProfile.DoesNotExist:
        return None
    if employee.partner_id != order.partner_id or not employee.is_active:
        return None
    return employee


@transaction.atomic
def get_or_create_active_cart_for_telegram_user(
    *,
    partner_id: UUID | str,
    telegram_id: int,
) -> Cart:
    table_session = get_active_table_session(partner_id=partner_id, telegram_id=telegram_id)
    if table_session is None:
        raise OrderFlowError(
            "Корзина доступна только после сканирования QR-кода стола. Сначала откройте сессию."
        )

    cart = (
        Cart.objects.select_for_update()
        .filter(
            partner_id=partner_id,
            guest=table_session.guest,
            status=Cart.Status.ACTIVE,
        )
        .order_by("-updated_at")
        .first()
    )
    if cart is not None:
        if cart.table_session_id != table_session.id:
            cart.table_session = table_session
            cart.save(update_fields=["table_session", "updated_at"])
        return cart

    return Cart.objects.create(
        partner_id=partner_id,
        guest=table_session.guest,
        table_session=table_session,
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
    table_session = get_active_table_session(partner_id=partner_id, telegram_id=telegram_id)
    if table_session is None:
        return None
    return (
        Cart.objects.filter(
            partner_id=partner_id,
            guest=table_session.guest,
            status=Cart.Status.ACTIVE,
        )
        .select_related("table_session__table")
        .prefetch_related("items", "items__menu_item")
        .order_by("-updated_at")
        .first()
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
    cart_items = list(cart.items.all())
    if not cart_items:
        raise OrderFlowError("Корзина пуста. Добавьте позиции перед оформлением.")

    order = create_order_from_session(
        partner_id=partner_id,
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


def _staff_notification_recipients(order: Order):
    # Staff notifications must stay partner-scoped even if one Telegram account
    # is reused by the same person across different venues.
    return EmployeeProfile.objects.filter(
        partner_id=order.partner_id,
        is_active=True,
        bot_notifications_enabled=True,
        telegram_account__isnull=False,
    )


def _create_staff_notifications_for_order_created(order: Order) -> None:
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
                    f"Поступил новый заказ на стол #{order.table.number} "
                    f"с суммой {order.total_amount} грн."
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
                    f"Статус заказа для стола #{order.table.number} "
                    f"изменён с {from_status or '-'} на {to_status}."
                ),
            )
        )
    for notification in notifications:
        notification.save()
        deliver_staff_notification(notification)
