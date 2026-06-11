from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def build_guest_call_keyboard(*, targets: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for target_code, target_label in targets:
        builder.button(
            text=target_label,
            callback_data=f"guestcall:request:{target_code}",
        )
    builder.button(
        text="Отмена",
        callback_data="guestcall:cancel",
    )
    builder.adjust(2, 1)
    return builder.as_markup()
