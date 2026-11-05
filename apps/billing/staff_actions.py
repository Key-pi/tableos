"""What staff may do with an order now, for every staff screen (PQ-044, PQ-048).

The bot's order card (``bot/handlers/staff.py:_order_actions_keyboard``) and
the Mini App ask the same question; the answer is a list of action codes in
the order the buttons are shown. The services check every action again when
it is pressed (``apps/orders/services.py``, ``apps/billing/remote_orders.py``).
"""

from __future__ import annotations

from apps.billing.remote_orders import awaits_payment_on_receipt, awaits_transfer_check
from apps.orders.models import Order
from apps.orders.services import get_available_staff_actions

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
