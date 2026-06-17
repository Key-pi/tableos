from __future__ import annotations

from dataclasses import dataclass

from apps.billing.models import Bill
from apps.orders.models import Cart, Order
from apps.tables.services import get_active_table_session


class GuestJourney:
    """Current *context* a guest is in right now — not the same as enabled modules.

    Modules (``PartnerBotSettings.module_*``) describe what a venue *offers*.
    A journey describes which scenario a *specific guest* is currently going
    through, so the bot can keep context across several messages and show the
    right buttons/texts for that scenario.

    - ``BROWSE``: no active table session (just looking at the menu / profile).
    - ``TABLE``: the guest has an active table session (ordering at a table).
    - ``DELIVERY`` / ``PICKUP``: placeholders for the upcoming multi-step
      delivery and pickup flows (address, phone, etc.). They are intentionally
      defined ahead of time but not assigned yet — the delivery/pickup handlers
      are still stubs. When those flows are built (most likely as aiogram FSM
      states), this is where the guest's current order journey will live.

    Today only BROWSE/TABLE are assigned, and that distinction also happens to be
    mirrored by ``GuestNavigationState.has_active_session``; the journey label is
    kept as the explicit, future-proof name for the guest's current scenario.
    """

    BROWSE = "browse"
    TABLE = "table"
    DELIVERY = "delivery"
    PICKUP = "pickup"


class GuestAction:
    MENU = "menu"
    SESSION = "session"
    CART = "cart"
    CHECKOUT = "checkout"
    DELIVERY = "delivery"
    PICKUP = "pickup"
    PROFILE = "profile"
    HELP = "help"
    CALL_STAFF = "call_staff"
    REQUEST_BILL = "request_bill"


@dataclass(frozen=True, slots=True)
class GuestNavigationState:
    # `journey` is the guest's current scenario (see GuestJourney). Buttons are
    # currently driven by `available_actions` + module checks, not by `journey`
    # itself; the field is kept for delivery/pickup flows that will branch on it.
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
    session = None
    if content.supports_tables():
        session = get_active_table_session(partner_id=partner_id, telegram_id=telegram_id)
    if session is None:
        actions = []
        if content.supports_menu():
            actions.append(GuestAction.MENU)
        if content.supports_delivery_orders():
            actions.append(GuestAction.DELIVERY)
        if content.supports_pickup_orders():
            actions.append(GuestAction.PICKUP)
        if content.supports_loyalty():
            actions.append(GuestAction.PROFILE)
        if content.supports_help():
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

    actions = []
    if content.supports_menu():
        actions.append(GuestAction.MENU)
    if content.supports_tables():
        actions.append(GuestAction.SESSION)
    if content.supports_cart():
        actions.append(GuestAction.CART)
    if has_active_cart and content.supports_cart():
        actions.append(GuestAction.CHECKOUT)
    if content.supports_delivery_orders():
        actions.append(GuestAction.DELIVERY)
    if content.supports_pickup_orders():
        actions.append(GuestAction.PICKUP)
    if content.supports_loyalty():
        actions.append(GuestAction.PROFILE)
    if content.supports_help():
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
