"""What staff may do with an order now, for every staff screen (PQ-044, PQ-048).

The bot's order card (``bot/handlers/staff.py:_order_actions_keyboard``) and
the Mini App ask the same question; the answer is a list of action codes in
the order the buttons are shown. The services check every action again when
it is pressed (``apps/orders/services.py``, ``apps/billing/remote_orders.py``).
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.billing.remote_orders import awaits_payment_on_receipt, awaits_transfer_check
from apps.orders.models import Order
from apps.orders.services import get_available_staff_actions
from core.i18n import N_, _

# Status buttons, the same on every staff screen: the bot, the Mini App and
# Admin (``status_labels_for``).
STATUS_LABELS = {
    "accepted": N_("Взять в работу"),
    "preparing": N_("Готовим"),
    "ready": N_("Готово"),
    "delivering": N_("Нести"),
    "completed": N_("Завершить"),
    "canceled": N_("Отменить"),
}
# Delivery (PQ-032): a ready order is handed to the courier, then delivered.
DELIVERY_STATUS_LABELS = {
    **STATUS_LABELS,
    "delivering": N_("🚚 В пути"),
    "completed": N_("✅ Доставлено"),
}
# Shipping by post (PQ-034): packed, sent with a ТТН, received.
SHIPPING_STATUS_LABELS = {
    **STATUS_LABELS,
    "ready": N_("📦 Собран"),
    "delivering": N_("🚚 Отправлен"),
    "completed": N_("✅ Получен"),
}


def status_labels_for(channel: str) -> dict[str, str]:
    if channel == Order.Channel.DELIVERY:
        return DELIVERY_STATUS_LABELS
    if channel == Order.Channel.SHIPPING:
        return SHIPPING_STATUS_LABELS
    return STATUS_LABELS

TRANSFER_OK = "transfer_ok"
TRANSFER_NO = "transfer_no"
RECEIPT_CASH = "receipt_cash"
RECEIPT_CARD = "receipt_card"
RECEIPT_COD = "receipt_cod"
# The guest did not take (or pay for) an order paid on receipt: cancelled,
# bonuses back (``cancel_unpaid_remote_order``).
RECEIPT_NO = "receipt_no"
# «Отправлен» of a parcel: needs the ТТН (``mark_order_shipped``).
SHIP = "ship"
STATUS = "status:"


def staff_order_actions(order: Order) -> list[str]:
    is_shipping = order.channel == Order.Channel.SHIPPING
    unpaid_on_receipt = awaits_payment_on_receipt(order)
    # A delivery is paid to the courier (PQ-044): payment and «гость не забрал»
    # appear from «В пути» on; before that «Отменить» still returns bonuses.
    courier_has_it = (
        order.channel != Order.Channel.DELIVERY or order.status == Order.Status.DELIVERING
    )
    receipt_pending = unpaid_on_receipt and courier_has_it
    cancel_unpaid = unpaid_on_receipt and not courier_has_it
    ship_needs_tracking = is_shipping and order.status == Order.Status.READY

    statuses = get_available_staff_actions(order.status, channel=order.channel)
    actions: list[str] = []
    if receipt_pending:
        actions += [RECEIPT_COD] if is_shipping else [RECEIPT_CASH, RECEIPT_CARD]
        statuses = [status for status in statuses if status != Order.Status.CANCELED]
    if awaits_transfer_check(order):
        # A prepaid order goes into work only after the transfer is checked.
        actions += [TRANSFER_OK, TRANSFER_NO]
        statuses = []
    for status in statuses:
        if ship_needs_tracking and status == Order.Status.DELIVERING:
            actions.append(SHIP)
        elif cancel_unpaid and status == Order.Status.CANCELED:
            actions.append(RECEIPT_NO)
        else:
            actions.append(STATUS + status)
    if receipt_pending:
        actions.append(RECEIPT_NO)
    return actions


@dataclass(frozen=True)
class ActionText:
    """How a button of ``staff_order_actions`` reads on the Mini App and in
    Admin (the bot keeps its own keyboard, ``bot/keyboards/staff.py``)."""

    label: str
    style: str = ""  # "primary" | "danger" | ""
    confirm: str = ""
    needs_tracking: bool = False


def describe_order_action(order: Order, code: str) -> ActionText:
    if code.startswith(STATUS):
        status = code[len(STATUS):]
        label = _(status_labels_for(order.channel).get(status, status))
        if status == Order.Status.CANCELED:
            return ActionText(label, "danger", _("Отменить заказ?"))
        return ActionText(label)
    if code == TRANSFER_OK:
        return ActionText(_("✅ Оплата получена"), "primary")
    if code == TRANSFER_NO:
        return ActionText(_("❌ Оплаты нет"), "danger", _(
            "Отменить заказ, потому что оплата не поступила? Списанные бонусы вернутся "
            "гостю, гость получит сообщение."
        ))
    if code == RECEIPT_CASH:
        return ActionText(_("💵 Оплачено наличными"), "primary")
    if code == RECEIPT_CARD:
        return ActionText(_("💳 Оплачено картой"), "primary")
    if code == RECEIPT_COD:
        return ActionText(_("💵 Наложенный платёж получен"), "primary")
    if code == RECEIPT_NO:
        courier_has_it = (
            order.channel != Order.Channel.DELIVERY or order.status == Order.Status.DELIVERING
        )
        label = (
            _("❌ Посылку не забрали") if order.channel == Order.Channel.SHIPPING
            else _("❌ Гость не забрал") if courier_has_it
            else _(STATUS_LABELS["canceled"])
        )
        return ActionText(label, "danger", _("Отменить заказ? Списанные бонусы вернутся гостю."))
    if code == SHIP:
        return ActionText(_(SHIPPING_STATUS_LABELS["delivering"]), needs_tracking=True)
    return ActionText(code)


# --- bills (the bot's bill card: bot/handlers/staff.py:_build_bill_card_message) --

BILL_ISSUE = "issue"
BILL_PAY_BONUS = "pay_bonus"
BILL_PAY_CASH = "pay_cash"
BILL_PAY_TERMINAL = "pay_terminal"
OPEN_BILL_STATUSES = frozenset({"draft", "issued", "partially_paid"})


def bill_bonus_offer(bill):
    """How much of the bill the guest's bonuses may pay now (0: no offer)."""

    from decimal import Decimal

    from apps.bonuses.services import get_redeemable_bonus_amount

    if not (
        bill.primary_guest_id
        and bill.status in OPEN_BILL_STATUSES
        and not bill.bonus_spent_amount
        and not bill.paid_amount
        and bill.total_amount > 0
    ):
        return Decimal("0.00")
    return get_redeemable_bonus_amount(
        partner_id=bill.partner_id, guest=bill.primary_guest, purchase_total=bill.total_amount
    )


def staff_bill_actions(bill) -> list[str]:
    """Issue and payments; changes and voiding stay in the bot's bill card."""

    remaining = bill.total_amount - bill.paid_amount
    actions = []
    if bill.status == "draft":
        actions.append(BILL_ISSUE)
    if bill_bonus_offer(bill) > 0:
        actions.append(BILL_PAY_BONUS)
    if remaining > 0 and bill.status in OPEN_BILL_STATUSES:
        actions += [BILL_PAY_CASH, BILL_PAY_TERMINAL]
    return actions


def bill_change_rights(bill, actor_user) -> tuple[bool, bool]:
    """(may change lines, may void) on the bill card (PQ-028); the services
    check again under the bill lock."""

    from apps.billing.services import user_can_adjust_bills

    if bill.status not in OPEN_BILL_STATUSES:
        return False, False
    if not user_can_adjust_bills(actor_user, bill.partner_id):
        return False, False
    has_active_lines = any(item.is_active for item in bill.items.all())
    return has_active_lines and bill.bonus_spent_amount == 0, bill.paid_amount == 0
