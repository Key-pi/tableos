from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def build_profile_summary_keyboard(*, bonuses_label: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=bonuses_label,
                    callback_data="guestprofile:bonuses:0",
                )
            ]
        ]
    )


def build_profile_bonuses_keyboard(*, page: int, total_pages: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    navigation_row: list[InlineKeyboardButton] = []
    if page > 0:
        navigation_row.append(
            InlineKeyboardButton(
                text="← Назад",
                callback_data=f"guestprofile:bonuses:{page - 1}",
            )
        )
    if page + 1 < total_pages:
        navigation_row.append(
            InlineKeyboardButton(
                text="Дальше →",
                callback_data=f"guestprofile:bonuses:{page + 1}",
            )
        )
    if navigation_row:
        rows.append(navigation_row)
    rows.append(
        [
            InlineKeyboardButton(
                text="К профилю",
                callback_data="guestprofile:summary",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
