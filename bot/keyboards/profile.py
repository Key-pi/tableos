from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from core.i18n import LANGUAGE_FLAGS, LANGUAGE_NAMES, _

MY_ORDERS_LABEL = "📋 Мои заказы"


def build_profile_summary_keyboard(
    *, bonuses_label: str, show_language: bool = False
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=bonuses_label,
                callback_data="guestprofile:bonuses:0",
            )
        ],
        [InlineKeyboardButton(text=MY_ORDERS_LABEL, callback_data="guestorders:0")],
    ]
    if show_language:
        rows.append([InlineKeyboardButton(text=_("🌐 Язык"), callback_data="guestlang:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_language_keyboard(*, languages, chosen: str) -> InlineKeyboardMarkup:
    """The venue's languages, each named in itself, and «automatic» (PQ-040)."""

    rows = []
    for code in languages:
        name = f"{LANGUAGE_FLAGS[code]} {LANGUAGE_NAMES[code]}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"✓ {name}" if code == chosen else name,
                    callback_data=f"guestlang:{code}",
                )
            ]
        )
    auto = _("Автоматически — как в Telegram")
    rows.append(
        [
            InlineKeyboardButton(
                text=f"✓ {auto}" if not chosen else auto, callback_data="guestlang:auto"
            )
        ]
    )
    rows.append([InlineKeyboardButton(text=_("К профилю"), callback_data="guestprofile:summary")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
