from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def build_guest_session_overview_keyboard(
    *,
    order_shortcuts: list[tuple[str, str]],
) -> InlineKeyboardMarkup | None:
    if not order_shortcuts:
        return None

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"guestorderopen:{order_public_id}",
                )
            ]
            for order_public_id, label in order_shortcuts
        ]
    )


def build_guest_order_card_keyboard(
    *,
    order_public_id: str,
    can_confirm_received: bool,
) -> InlineKeyboardMarkup:
    keyboard: list[list[InlineKeyboardButton]] = []
    if can_confirm_received:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=f"Подтвердить получение #{order_public_id}",
                    callback_data=f"guestorderconfirm:{order_public_id}",
                )
            ]
        )
    keyboard.append(
        [
            InlineKeyboardButton(
                text="К моему столу",
                callback_data="guestsession:back",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)
