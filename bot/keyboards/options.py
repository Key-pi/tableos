"""Choosing a variant and add-ons in the bot (catalog step 2, owner 2026-11-07).

No FSM: the choice lives in the callback — ``mo:<item code>:<variant>:<add-ons>``
redraws the message with that choice, ``ma:…`` adds it to the cart. The
variant is its number in the list (hex, ``-`` for none), the add-ons a bit
mask over all add-ons in the order the guest sees them (hex). At most
``MAX_ADD_ONS`` add-ons are offered in the bot, so the data stays under
Telegram's 64 bytes. The numbers are read against what is on sale when the
button is pressed; the cart service checks the choice itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from apps.menu.stock import LOW_STOCK
from bot.keyboards.menu import CART_CALLBACK, cart_button_text
from core.i18n import _

OPEN = "mo"
ADD = "ma"
NOOP = "mo:-"
MAX_ADD_ONS = 60


@dataclass(frozen=True)
class Pick:
    variant: int | None = None
    mask: int = 0


def add_ons(choices) -> list[tuple[object, object]]:
    """(group entry, add-on) for every add-on the bot offers, in order."""

    found = [(entry, modifier) for entry in choices.groups for modifier in entry.modifiers]
    return found[:MAX_ADD_ONS]


def encode(prefix: str, public_id: str, pick: Pick) -> str:
    variant = "-" if pick.variant is None else format(pick.variant, "x")
    return f"{prefix}:{public_id}:{variant}:{format(pick.mask, 'x')}"


def decode(data: str) -> tuple[str, Pick | None]:
    """(item code, pick); the pick is None for a bare ``mo:<code>``."""

    parts = (data or "").split(":")
    if len(parts) == 2 and parts[1]:
        return parts[1], None
    if len(parts) != 4 or not parts[1]:
        raise ValueError(data)
    variant = None if parts[2] == "-" else int(parts[2], 16)
    return parts[1], Pick(variant, int(parts[3], 16))


def starting_pick(choices, *, stock: bool = False) -> Pick:
    """The first variant (in stock, when stock is counted — ``stock``) and
    the first add-on of each required single choice, as in the Mini App; the
    guest changes what they want."""

    variant = None
    if choices.variants:
        on_sale = [
            index for index, row in enumerate(choices.variants)
            if not stock or row.stock_quantity > 0
        ]
        variant = on_sale[0] if on_sale else 0
    mask = 0
    for index, (entry, modifier) in enumerate(add_ons(choices)):
        group = entry.group
        if group.min_choices > 0 and group.max_choices == 1 and modifier is entry.modifiers[0]:
            mask |= 1 << index
    return Pick(variant, mask)


def valid(choices, pick: Pick) -> bool:
    if (pick.variant is None) != (not choices.variants):
        return False
    if pick.variant is not None and not 0 <= pick.variant < len(choices.variants):
        return False
    return pick.mask >> len(add_ons(choices)) == 0


def over_limit(choices, pick: Pick):
    """The group whose «at most» the pick goes over, or None."""

    for entry in choices.groups:
        limit = entry.group.max_choices
        if limit and len(_chosen_in(choices, pick, entry)) > limit:
            return entry.group
    return None


def _chosen_in(choices, pick: Pick, entry) -> list:
    return [
        modifier
        for index, (owner, modifier) in enumerate(add_ons(choices))
        if owner is entry and pick.mask >> index & 1
    ]


def toggled(choices, pick: Pick, index: int) -> Pick:
    """The pick after pressing add-on ``index``: a single choice replaces
    the other add-ons of its group (a required one cannot be emptied)."""

    flat = add_ons(choices)
    entry = flat[index][0]
    bit = 1 << index
    if entry.group.max_choices == 1:
        group_bits = 0
        for other, (owner, _modifier) in enumerate(flat):
            if owner is entry:
                group_bits |= 1 << other
        if pick.mask & bit:
            if entry.group.min_choices > 0:
                return pick
            return Pick(pick.variant, pick.mask & ~bit)
        return Pick(pick.variant, (pick.mask & ~group_bits) | bit)
    return Pick(pick.variant, pick.mask ^ bit)


def chosen(choices, pick: Pick) -> tuple[object, list]:
    """(variant, add-ons) the pick stands for."""

    variant = choices.variants[pick.variant] if pick.variant is not None else None
    modifiers = [
        modifier for index, (_entry, modifier) in enumerate(add_ons(choices))
        if pick.mask >> index & 1
    ]
    return variant, modifiers


def price_of(item, choices, pick: Pick) -> Decimal:
    variant, modifiers = chosen(choices, pick)
    base = Decimal(variant.price if variant is not None else item.price)
    return (base + sum((Decimal(m.price) for m in modifiers), Decimal("0"))).quantize(
        Decimal("0.01")
    )


def _group_label(group) -> str:
    parts = [group.name]
    if group.min_choices > 0:
        parts.append(_("обязательно"))
    if group.max_choices == 0:
        parts.append(_("можно несколько"))
    elif group.max_choices > 1:
        parts.append(_("до {count}", count=group.max_choices))
    return " · ".join(parts)


def _rows_of_two(buttons) -> list[list[InlineKeyboardButton]]:
    return [buttons[index:index + 2] for index in range(0, len(buttons), 2)]


# The staff quick sale chooses the same way (owner, 2026-11-07).
STAFF_OPEN = "so"
STAFF_ADD = "sa"


def _variant_text(variant, mark: str, *, staff: bool, stock: bool) -> str:
    text = f"{mark} {variant.name} · {variant.price}"
    if not stock:
        return text
    if staff:
        # The staff see how many are left (the till may go below zero).
        return text + _(" · ост. {count}", count=variant.stock_quantity)
    if variant.stock_quantity <= LOW_STOCK:
        return text + _(" · осталось мало")
    return text


def build_choice_keyboard(
    item, choices, pick: Pick, *, back_callback: str, cart=None, staff: bool = False,
    stock: bool = False,
):
    """Variants, each group with its add-ons, «Добавить · сумма», the cart
    (guests) and back to the menu or the sale. ``stock`` — the item's stock
    is counted (catalog step 3): a guest cannot pick a variant with nothing
    left and never sees the number; the staff see it."""

    open_prefix, add_prefix = (STAFF_OPEN, STAFF_ADD) if staff else (OPEN, ADD)

    rows: list[list[InlineKeyboardButton]] = []
    variant_buttons = []
    for index, variant in enumerate(choices.variants):
        if stock and not staff and variant.stock_quantity <= 0:
            variant_buttons.append(InlineKeyboardButton(
                text=_("✖ {name} — нет в наличии", name=variant.name), callback_data=NOOP
            ))
            continue
        mark = "✅" if pick.variant == index else "○"
        variant_buttons.append(InlineKeyboardButton(
            text=_variant_text(variant, mark, staff=staff, stock=stock),
            callback_data=encode(open_prefix, item.public_id, Pick(index, pick.mask)),
        ))
    rows += _rows_of_two(variant_buttons)
    flat = add_ons(choices)
    for entry in choices.groups:
        rows.append([InlineKeyboardButton(text=_group_label(entry.group), callback_data=NOOP)])
        buttons = []
        for index, (owner, modifier) in enumerate(flat):
            if owner is not entry:
                continue
            on = bool(pick.mask >> index & 1)
            single = entry.group.min_choices > 0 and entry.group.max_choices == 1
            mark = "✅" if on else ("○" if single else "▫️")
            extra = f" +{modifier.price}" if modifier.price > 0 else ""
            buttons.append(InlineKeyboardButton(
                text=f"{mark} {modifier.name}{extra}",
                callback_data=encode(open_prefix, item.public_id, toggled(choices, pick, index)),
            ))
        rows += _rows_of_two(buttons)
    rows.append([InlineKeyboardButton(
        text=_("➕ Добавить · {price} грн", price=price_of(item, choices, pick)),
        callback_data=encode(add_prefix, item.public_id, pick),
    )])
    if not staff:
        rows.append(
            [InlineKeyboardButton(text=cart_button_text(cart), callback_data=CART_CALLBACK)]
        )
    rows.append([InlineKeyboardButton(text=_("← К меню"), callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)
