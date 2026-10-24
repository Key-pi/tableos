from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.i18n import _


def build_billing_request_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=_("Только мои заказы"), callback_data="billreq:personal")
    builder.button(text=_("Оплатить весь стол"), callback_data="billreq:shared")
    builder.button(text=_("Нужно разделить иначе"), callback_data="billreq:custom_split")
    builder.button(text=_("Отмена"), callback_data="billreq:cancel")
    builder.adjust(1)
    return builder.as_markup()
