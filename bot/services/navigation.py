from __future__ import annotations

from dataclasses import dataclass

from apps.billing.models import Bill
from apps.orders.models import Cart, Order
from apps.tables.services import get_active_table_session


class GuestJourney:
    BROWSE = "browse"
    TABLE = "table"
    DELIVERY = "delivery"
    PICKUP = "pickup"


class GuestAction:
    MENU = "menu"
    SESSION = "session"
    CART = "cart"
    CHECKOUT = "checkout"
    PROFILE = "profile"
    HELP = "help"
    CALL_STAFF = "call_staff"
    REQUEST_BILL = "request_bill"


@dataclass(frozen=True, slots=True)
class GuestNavigationState:
    journey: str
    available_actions: tuple[str, ...]
    has_active_session: bool = False
    has_active_cart: bool = False
    has_billable_activity: bool = False
    active_table_number: int | None = None

    def has_action(self, action: str) -> bool:
        return action in self.available_actions


def resolve_guest_navigation_state(
    *,
    partner_id,
    telegram_id: int,
    content,
) -> GuestNavigationState:
    session = get_active_table_session(partner_id=partner_id, telegram_id=telegram_id)
    if session is None:
        actions = [GuestAction.MENU]
        if content.show_loyalty_button:
            actions.append(GuestAction.PROFILE)
        if content.show_help_button:
            actions.append(GuestAction.HELP)
        return GuestNavigationState(
            journey=GuestJourney.BROWSE,
            available_actions=tuple(actions),
        )

    has_active_cart = Cart.objects.filter(
        partner_id=partner_id,
        guest_id=session.guest_id,
        status=Cart.Status.ACTIVE,
        items__isnull=False,
    ).exists()
    has_billable_orders = Order.objects.filter(
        partner_id=partner_id,
        table_id=session.table_id,
    ).exclude(status=Order.Status.CANCELED).exists()
    has_open_bills = Bill.objects.filter(
        partner_id=partner_id,
        table_id=session.table_id,
        status__in=[
            Bill.Status.DRAFT,
            Bill.Status.ISSUED,
            Bill.Status.PARTIALLY_PAID,
        ],
    ).exists()
    has_billable_activity = has_billable_orders or has_open_bills

    actions = [GuestAction.MENU]
    if content.show_session_button:
        actions.append(GuestAction.SESSION)
    if content.supports_cart() and content.show_cart_button:
        actions.append(GuestAction.CART)
    if has_active_cart and content.supports_cart() and content.show_checkout_button:
        actions.append(GuestAction.CHECKOUT)
    if content.show_loyalty_button:
        actions.append(GuestAction.PROFILE)
    if content.show_help_button:
        actions.append(GuestAction.HELP)
    if content.supports_staff_call():
        actions.append(GuestAction.CALL_STAFF)
    if has_billable_activity and content.supports_billing_request():
        actions.append(GuestAction.REQUEST_BILL)

    return GuestNavigationState(
        journey=GuestJourney.TABLE,
        available_actions=tuple(actions),
        has_active_session=True,
        has_active_cart=has_active_cart,
        has_billable_activity=has_billable_activity,
        active_table_number=session.table.number,
    )
