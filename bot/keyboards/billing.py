from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def build_billing_request_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Только мои заказы", callback_data="billreq:personal")
    builder.button(text="Оплатить весь стол", callback_data="billreq:shared")
    builder.button(text="Нужно разделить иначе", callback_data="billreq:custom_split")
    builder.button(text="Отмена", callback_data="billreq:cancel")
    builder.adjust(1)
    return builder.as_markup()
