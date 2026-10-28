from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from bot.services.content import BotContent
from bot.services.navigation import GuestAction, GuestNavigationState


def build_main_keyboard(
    content: BotContent | None = None,
    *,
    navigation_state: GuestNavigationState | None = None,
    has_active_session: bool | None = None,
) -> ReplyKeyboardMarkup:
    content = content or BotContent.defaults()
    if navigation_state is None:
        navigation_state = GuestNavigationState(
            journey="table" if has_active_session else "browse",
            available_actions=(
                (
                    GuestAction.MENU,
                    GuestAction.SESSION,
                    GuestAction.CART,
                    GuestAction.CHECKOUT,
                    GuestAction.DELIVERY,
                    GuestAction.PICKUP,
                    GuestAction.PROFILE,
                    GuestAction.HELP,
                    GuestAction.CALL_STAFF,
                    GuestAction.REQUEST_BILL,
                )
                if has_active_session
                else (
                    GuestAction.MENU,
                    GuestAction.DELIVERY,
                    GuestAction.PICKUP,
                    GuestAction.PROFILE,
                    GuestAction.HELP,
                )
            ),
            has_active_session=bool(has_active_session),
        )
    # Most used first: the menu and the cart, then ordering, then the table's
    # requests, then the guest's own pages.
    labels = [
        [
            (GuestAction.MENU, content.button_menu_label),
            (GuestAction.CART, content.button_cart_label),
        ],
        [(GuestAction.CHECKOUT, content.button_checkout_label)],
        [
            (GuestAction.PICKUP, content.button_pickup_label),
            (GuestAction.DELIVERY, content.button_delivery_label),
            (GuestAction.SHIPPING, content.button_shipping_label),
        ],
        [
            (GuestAction.CALL_STAFF, content.button_call_staff_label),
            (GuestAction.REQUEST_BILL, content.button_request_bill_label),
        ],
        [
            (GuestAction.SESSION, content.button_session_label),
            (GuestAction.PROFILE, content.button_my_profile),
            (GuestAction.HELP, content.button_help_label),
        ],
    ]
    rows = [
        [KeyboardButton(text=text) for action, text in row if navigation_state.has_action(action)]
        for row in labels
    ]
    rows = [row for row in rows if row]

    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
    )
