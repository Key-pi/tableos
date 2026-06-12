from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def build_profile_overview_keyboard(*, bonuses_button_label: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=bonuses_button_label,
                    callback_data="profile:bonuses:1",
                )
            ]
        ]
    )


def build_profile_bonus_keyboard(*, page: int, total_pages: int) -> InlineKeyboardMarkup:
    navigation_row: list[InlineKeyboardButton] = []
    if page > 1:
        navigation_row.append(
            InlineKeyboardButton(
                text="← Назад",
                callback_data=f"profile:bonuses:{page - 1}",
            )
        )
    navigation_row.append(
        InlineKeyboardButton(
            text=f"{page}/{total_pages}",
            callback_data="profile:noop",
        )
    )
    if page < total_pages:
        navigation_row.append(
            InlineKeyboardButton(
                text="Дальше →",
                callback_data=f"profile:bonuses:{page + 1}",
            )
        )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            navigation_row,
            [
                InlineKeyboardButton(
                    text="← К профилю",
                    callback_data="profile:overview",
                )
            ],
        ]
    )
