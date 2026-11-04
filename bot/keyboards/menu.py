from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from core.i18n import _

CART_CALLBACK = "cart:refresh"


def cart_button_text(cart) -> str:
    items = list(cart.items.all()) if cart is not None else []
    if not items:
        return _("🛒 Корзина")
    return _(
        "🛒 Корзина: {count} шт. · {total} грн",
        count=sum(item.quantity for item in items),
        total=cart.total_amount,
    )


def refresh_cart_button(markup: InlineKeyboardMarkup | None, cart) -> InlineKeyboardMarkup | None:
    """The same keyboard with the cart button showing this cart."""

    if markup is None:
        return None
    rows = [
        [
            InlineKeyboardButton(text=cart_button_text(cart), callback_data=CART_CALLBACK)
            if button.callback_data == CART_CALLBACK
            else button
            for button in row
        ]
        for row in markup.inline_keyboard
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_menu_keyboard(
    *,
    categories,
    active_category_id,
    items,
    has_session: bool,
    supports_cart: bool = True,
    cart=None,
    choose_way=(),
) -> InlineKeyboardMarkup:
    """Categories, then the items to add (only once the guest chose a way of
    ordering — ``has_session``), else the buttons that choose the way."""

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
                        text=_("➕ {name} — {price} грн", name=item.name, price=item.price),
                        callback_data=f"menuadd:{item.public_id}",
                    )
                ]
            )
        rows.append(
            [InlineKeyboardButton(text=cart_button_text(cart), callback_data=CART_CALLBACK)]
        )
    elif choose_way:
        rows.append(
            [
                InlineKeyboardButton(text=text, callback_data=callback_data)
                for text, callback_data in choose_way
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_cart_keyboard(
    *,
    cart=None,
    supports_cart: bool,
    mode_switch: tuple[str, str] | None = None,
    choose_way=(),
) -> InlineKeyboardMarkup | None:
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

    if has_items and choose_way:
        # The guest left the table: the items wait until they choose a way.
        rows.append(
            [
                InlineKeyboardButton(text=text, callback_data=callback_data)
                for text, callback_data in choose_way
            ]
        )
    elif has_items:
        rows.append(
            [
                InlineKeyboardButton(text=_("✅ Оформить"), callback_data="cart:checkout"),
                InlineKeyboardButton(text=_("Очистить"), callback_data="cart:clear"),
            ]
        )
    if mode_switch is not None:
        text, callback_data = mode_switch
        rows.append([InlineKeyboardButton(text=text, callback_data=callback_data)])
    rows.append([InlineKeyboardButton(text=_("← К меню"), callback_data="menurefresh")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
