from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def build_menu_keyboard(
    *,
    categories,
    active_category_id,
    items,
    has_session: bool,
    supports_cart: bool = True,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    category_row: list[InlineKeyboardButton] = []
    for category in categories:
        prefix = "• " if str(category.id) == str(active_category_id) else ""
        category_row.append(
            InlineKeyboardButton(
                text=f"{prefix}{category.name}",
                callback_data=f"menucat:{category.id}",
            )
        )
        if len(category_row) == 2:
            rows.append(category_row)
            category_row = []
    if category_row:
        rows.append(category_row)

    if has_session and supports_cart:
        for item in items:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"➕ {item.name} • {item.price} грн",
                        callback_data=f"menuadd:{item.public_id}",
                    )
                ]
            )
        rows.append([InlineKeyboardButton(text="Открыть корзину", callback_data="cart:refresh")])

    rows.append([InlineKeyboardButton(text="Обновить меню", callback_data="menurefresh")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_cart_keyboard(*, cart=None, supports_cart: bool) -> InlineKeyboardMarkup | None:
    if not supports_cart:
        return None

    rows: list[list[InlineKeyboardButton]] = []
    has_items = bool(cart is not None and cart.items.all())
    if cart is not None:
        for item in cart.items.all():
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"➖ {item.item_name}",
                        callback_data=f"cart:item:sub:{item.menu_item.public_id}",
                    ),
                    InlineKeyboardButton(
                        text=str(item.quantity),
                        callback_data=f"cart:item:noop:{item.menu_item.public_id}",
                    ),
                    InlineKeyboardButton(
                        text="➕",
                        callback_data=f"cart:item:add:{item.menu_item.public_id}",
                    ),
                    InlineKeyboardButton(
                        text="✕",
                        callback_data=f"cart:item:remove:{item.menu_item.public_id}",
                    ),
                ]
            )

    if has_items:
        rows.append(
            [
                InlineKeyboardButton(text="Оформить", callback_data="cart:checkout"),
                InlineKeyboardButton(text="Очистить", callback_data="cart:clear"),
            ]
        )
    rows.append([InlineKeyboardButton(text="Обновить корзину", callback_data="cart:refresh")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
