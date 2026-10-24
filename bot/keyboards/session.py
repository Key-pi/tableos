from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from core.i18n import _


def build_guest_session_overview_keyboard(
    *,
    order_shortcuts: list[tuple[str, str]],
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=_("Обновить мой стол"),
                callback_data="guestsession:back",
            )
        ]
    ]

    rows = [
        [
            InlineKeyboardButton(
                text=label,
                callback_data=f"guestorderopen:{order_public_id}",
            )
        ]
        for order_public_id, label in order_shortcuts
    ] + rows
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
                    text=_(
                        "Подтвердить получение #{order_public_id}", order_public_id=order_public_id
                    ),
                    callback_data=f"guestorderconfirm:{order_public_id}",
                )
            ]
        )
    keyboard.append(
        [
            InlineKeyboardButton(
                text=_("К моему столу"),
                callback_data="guestsession:back",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def build_visit_choice_keyboard(
    *,
    table_id,
    options: list[tuple[str, str]],
) -> InlineKeyboardMarkup:
    """Join-or-new choice for a table that already has a party (ADR-0007).

    ``options`` are ``(visit_id, label)`` pairs. Callback data carries only
    UUIDs (hex, 32 chars) to stay well under Telegram's 64-byte limit; the
    handler reloads and re-validates the table and visit.
    """

    rows = [
        [
            InlineKeyboardButton(
                text=label,
                callback_data=f"tv:j:{_hex(visit_id)}",
            )
        ]
        for visit_id, label in options
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text=_("Начать новый визит"),
                callback_data=f"tv:n:{_hex(table_id)}",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _hex(value) -> str:
    return str(value).replace("-", "")
