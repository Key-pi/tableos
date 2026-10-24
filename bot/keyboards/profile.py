from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

MY_ORDERS_LABEL = "📋 Мои заказы"


def build_profile_summary_keyboard(*, bonuses_label: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=bonuses_label,
                    callback_data="guestprofile:bonuses:0",
                )
            ],
            [InlineKeyboardButton(text=MY_ORDERS_LABEL, callback_data="guestorders:0")],
        ]
    )


def build_my_orders_keyboard(
    *, orders: list[tuple[str, str]], page: int, total_pages: int
) -> InlineKeyboardMarkup:
    """One button per order (public id, button text), paging, back to profile."""

    rows = [
        [InlineKeyboardButton(text=text, callback_data=f"guestorder:{public_id}")]
        for public_id, text in orders
    ]
    navigation: list[InlineKeyboardButton] = []
    if page > 0:
        navigation.append(
            InlineKeyboardButton(text="← Назад", callback_data=f"guestorders:{page - 1}")
        )
    if page + 1 < total_pages:
        navigation.append(
            InlineKeyboardButton(text="Дальше →", callback_data=f"guestorders:{page + 1}")
        )
    if navigation:
        rows.append(navigation)
    rows.append([InlineKeyboardButton(text="К профилю", callback_data="guestprofile:summary")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_my_order_keyboard(*, public_id: str, can_confirm: bool) -> InlineKeyboardMarkup:
    rows = []
    if can_confirm:
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ Получил(а)", callback_data=f"guestorderconfirm:{public_id}"
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="← К заказам", callback_data="guestorders:0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
