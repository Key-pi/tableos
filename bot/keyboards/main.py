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
    rows: list[list[KeyboardButton]] = []

    first_row = []
    if navigation_state.has_action(GuestAction.MENU):
        first_row.append(KeyboardButton(text=content.button_menu_label))
    if navigation_state.has_action(GuestAction.SESSION):
        first_row.append(KeyboardButton(text=content.button_session_label))
    if first_row:
        rows.append(first_row)

    second_row = []
    if navigation_state.has_action(GuestAction.CART):
        second_row.append(KeyboardButton(text=content.button_cart_label))
    if navigation_state.has_action(GuestAction.CHECKOUT):
        second_row.append(KeyboardButton(text=content.button_checkout_label))
    if second_row:
        rows.append(second_row)

    order_journey_row = []
    if navigation_state.has_action(GuestAction.DELIVERY):
        order_journey_row.append(KeyboardButton(text=content.button_delivery_label))
    if navigation_state.has_action(GuestAction.PICKUP):
        order_journey_row.append(KeyboardButton(text=content.button_pickup_label))
    if order_journey_row:
        rows.append(order_journey_row)

    third_row = []
    if navigation_state.has_action(GuestAction.PROFILE):
        third_row.append(KeyboardButton(text=content.button_my_profile))
    if navigation_state.has_action(GuestAction.HELP):
        third_row.append(KeyboardButton(text=content.button_help_label))
    if third_row:
        rows.append(third_row)

    fourth_row = []
    if navigation_state.has_action(GuestAction.CALL_STAFF):
        fourth_row.append(KeyboardButton(text=content.button_call_staff_label))
    if navigation_state.has_action(GuestAction.REQUEST_BILL):
        fourth_row.append(KeyboardButton(text=content.button_request_bill_label))
    if fourth_row:
        rows.append(fourth_row)

    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
    )
