from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.i18n import _


def build_guest_call_keyboard(*, targets: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for target_code, target_label in targets:
        builder.button(
            text=target_label,
            callback_data=f"guestcall:request:{target_code}",
        )
    builder.button(
        text=_("Отмена"),
        callback_data="guestcall:cancel",
    )
    builder.adjust(2, 1)
    return builder.as_markup()
