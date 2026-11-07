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
    subcategories=(),
    active_subcategory_id=None,
    choices=None,
    sold_out=(),
) -> InlineKeyboardMarkup:
    """Top categories, the subcategories of the chosen one, then the items to
    add (only once the guest chose a way of ordering — ``has_session``; none
    while the category is out of its time), else the buttons that choose the
    way. Nothing to add for an item with nothing left (``sold_out``, ids)."""

    rows: list[list[InlineKeyboardButton]] = []

    def category_rows(entries, active_id, marker=""):
        row: list[InlineKeyboardButton] = []
        for category in entries:
            prefix = "• " if str(category.id) == str(active_id) else marker
            row.append(
                InlineKeyboardButton(
                    text=f"{prefix}{category.name}",
                    callback_data=f"menucat:{category.id}",
                )
            )
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)

    category_rows(categories, active_category_id)
    category_rows(subcategories, active_subcategory_id, marker="↳ ")

    if has_session and supports_cart:
        for item in items:
            if item.id in sold_out:
                continue
            # An item with variants or add-ons opens its choice first
            # (catalog step 2, bot/keyboards/options.py).
            item_choices = (choices or {}).get(item.id)
            if item_choices is not None and (item_choices.variants or item_choices.groups):
                price = min(variant.price for variant in item_choices.variants) \
                    if item_choices.variants else item.price
                text = (
                    _("➕ {name} — от {price} грн", name=item.name, price=price)
                    if item_choices.variants
                    else _("➕ {name} — {price} грн", name=item.name, price=price)
                )
                rows.append([InlineKeyboardButton(text=text, callback_data=f"mo:{item.public_id}")])
                continue
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
            # By the line's own id: one item may have lines with different
            # variants and add-ons (catalog step 2).
            line = item.id.hex
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"➖ {item.item_name}",
                        callback_data=f"cart:item:sub:{line}",
                    ),
                    InlineKeyboardButton(
                        text=str(item.quantity),
                        callback_data=f"cart:item:noop:{line}",
                    ),
                    InlineKeyboardButton(
                        text="➕",
                        callback_data=f"cart:item:add:{line}",
                    ),
                    InlineKeyboardButton(
                        text="✕",
                        callback_data=f"cart:item:remove:{line}",
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
