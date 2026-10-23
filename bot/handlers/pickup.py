"""Guest pickup checkout: phone, time, bonuses, transfer details, "I paid".

The flow is stateless: every step carries its choices in ``pk:`` callback
data, so a bot restart does not lose it. The amounts the guest saw travel in
the final callback and are rechecked when the order is placed
(apps/billing/pickup.py:place_pickup_order).
"""

from __future__ import annotations

from decimal import Decimal

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from asgiref.sync import sync_to_async

from apps.billing.pickup import (
    PickupError,
    available_slots,
    closed_message,
    get_pickup_quote,
    place_pickup_order,
    save_guest_phone,
)
from apps.orders.services import pickup_time_label
from apps.users.models import GuestProfile
from bot.keyboards.main import build_main_keyboard
from bot.services.content import BotContent
from bot.services.context import resolve_partner_for_bot_token
from bot.services.navigation import resolve_guest_navigation_state
from bot.utils.html import escape_html

router = Router()

SLOT_LABELS = {
    "now": "Как можно скорее",
    "30": "Через 30 минут",
    "60": "Через 1 час",
}
SHARE_PHONE_LABEL = "📱 Поделиться номером"


def _cents(value: Decimal) -> int:
    return int((Decimal(value) * 100).to_integral_value())


def _from_cents(value: str) -> Decimal:
    return (Decimal(int(value)) / 100).quantize(Decimal("0.01"))


def _cancel_row() -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text="Отмена", callback_data="pk:x")]


def _guest_phone(partner_id, telegram_id: int) -> str:
    guest = GuestProfile.objects.filter(
        partner_id=partner_id,
        telegram_account__telegram_id=telegram_id,
    ).first()
    return guest.phone if guest is not None else ""


async def _main_keyboard(partner, content, telegram_id: int):
    return build_main_keyboard(
        content,
        navigation_state=await sync_to_async(resolve_guest_navigation_state)(
            partner_id=partner.id,
            telegram_id=telegram_id,
            content=content,
        ),
    )


def is_callback(target) -> bool:
    """A CallbackQuery carries ``data``; a Message does not."""

    return getattr(target, "data", None) is not None and hasattr(target, "message")


def message_of(target):
    return target.message if is_callback(target) else target


async def _reply(target, text: str, *, reply_markup=None, edit: bool = False) -> None:
    message = message_of(target)
    if edit and is_callback(target):
        try:
            await message.edit_text(text, reply_markup=reply_markup)
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return
            raise
    await message.answer(text, reply_markup=reply_markup)


async def start_pickup_checkout(target, bot: Bot) -> None:
    """Entry point from the cart: ask for the phone once, then the time."""

    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    if not content.supports_pickup_orders():
        await _reply(target, "Самовывоз сейчас отключён.")
        return
    phone = await sync_to_async(_guest_phone)(partner.id, target.from_user.id)
    if not phone:
        await _reply(
            target,
            "Для самовывоза нужен ваш номер телефона — заведение свяжется, если что-то "
            "пойдёт не так. Нажмите кнопку ниже, номер сохранится один раз.",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text=SHARE_PHONE_LABEL, request_contact=True)]],
                resize_keyboard=True,
                one_time_keyboard=True,
            ),
        )
        return
    await _send_time_step(target, partner)


async def _send_time_step(target, partner, *, edit: bool = False) -> None:
    slots = await sync_to_async(available_slots)(partner)
    if not slots:
        await _reply(target, await sync_to_async(closed_message)(partner), edit=edit)
        return
    rows = [
        [InlineKeyboardButton(text=SLOT_LABELS[slot], callback_data=f"pk:t:{slot}")]
        for slot in slots
    ]
    rows.append(_cancel_row())
    await _reply(
        target,
        "<b>Самовывоз</b>\nКогда заберёте заказ?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        edit=edit,
    )


def _summary(quote, slot: str, use_bonuses: bool) -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        "<b>Самовывоз</b>",
        f"Время: {SLOT_LABELS[slot].lower()}",
        f"Сумма заказа: {quote.total} грн",
    ]
    if quote.bonus_to_spend:
        lines.append(f"Бонусами: −{quote.bonus_to_spend} грн")
    if quote.to_pay > 0:
        lines.extend(
            [
                f"<b>К оплате переводом: {quote.to_pay} грн</b>",
                "",
                "Реквизиты:",
                escape_html(quote.payment_details),
                "",
                "Переведите сумму и нажмите «Я оплатил(а)». Заведение проверит "
                "поступление и начнёт готовить.",
            ]
        )
        button_text = "✅ Я оплатил(а)"
    else:
        lines.append("Заказ полностью оплачивается бонусами.")
        button_text = "✅ Оформить"
    callback_data = (
        f"pk:p:{slot}:{int(use_bonuses)}:{_cents(quote.total)}:{_cents(quote.to_pay)}"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=button_text, callback_data=callback_data)],
            _cancel_row(),
        ]
    )
    return "\n".join(lines), keyboard


async def _send_summary(callback: CallbackQuery, partner, slot: str, use_bonuses: bool) -> None:
    quote = await sync_to_async(get_pickup_quote)(
        partner_id=partner.id,
        telegram_id=callback.from_user.id,
        use_bonuses=use_bonuses,
    )
    text, keyboard = _summary(quote, slot, use_bonuses)
    await _reply(callback, text, reply_markup=keyboard, edit=True)


@router.message(F.contact)
async def pickup_contact_handler(message: Message, bot: Bot) -> None:
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    contact = message.contact
    if contact.user_id != message.from_user.id:
        await message.answer(
            "Отправьте, пожалуйста, свой номер — кнопкой ниже.",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text=SHARE_PHONE_LABEL, request_contact=True)]],
                resize_keyboard=True,
                one_time_keyboard=True,
            ),
        )
        return
    try:
        await sync_to_async(save_guest_phone)(
            partner_id=partner.id,
            telegram_id=message.from_user.id,
            phone=contact.phone_number,
        )
    except PickupError as exc:
        await message.answer(str(exc))
        return
    await message.answer(
        "Спасибо, номер сохранён.",
        reply_markup=await _main_keyboard(partner, content, message.from_user.id),
    )
    if content.supports_pickup_orders():
        await _send_time_step(message, partner)


@router.callback_query(F.data.startswith("pk:"))
async def pickup_callback_handler(callback: CallbackQuery, bot: Bot) -> None:
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    parts = (callback.data or "").split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "x":
        await _reply(callback, "Оформление отменено. Корзина сохранена.", edit=True)
        await callback.answer()
        return
    if not content.supports_pickup_orders():
        await callback.answer("Самовывоз сейчас отключён.", show_alert=True)
        return

    try:
        if action == "t" and len(parts) == 3 and parts[2] in SLOT_LABELS:
            slot = parts[2]
            quote = await sync_to_async(get_pickup_quote)(
                partner_id=partner.id,
                telegram_id=callback.from_user.id,
                use_bonuses=False,
            )
            if quote.bonus_available > 0:
                await _reply(
                    callback,
                    f"<b>Самовывоз</b>\nМожно списать {quote.bonus_available} бонусов: "
                    f"к оплате будет {quote.total - quote.bonus_available} грн вместо "
                    f"{quote.total} грн.",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text=f"Списать {quote.bonus_available} бонусов",
                                    callback_data=f"pk:b:{slot}:1",
                                )
                            ],
                            [
                                InlineKeyboardButton(
                                    text="Без бонусов",
                                    callback_data=f"pk:b:{slot}:0",
                                )
                            ],
                            _cancel_row(),
                        ]
                    ),
                    edit=True,
                )
            else:
                await _send_summary(callback, partner, slot, False)
            await callback.answer()
            return
        if action == "b" and len(parts) == 4 and parts[2] in SLOT_LABELS:
            await _send_summary(callback, partner, parts[2], parts[3] == "1")
            await callback.answer()
            return
        if action == "p" and len(parts) == 6 and parts[2] in SLOT_LABELS:
            slot, use_bonuses = parts[2], parts[3] == "1"
            order = await sync_to_async(place_pickup_order)(
                partner_id=partner.id,
                telegram_id=callback.from_user.id,
                slot=slot,
                use_bonuses=use_bonuses,
                expected_total=_from_cents(parts[4]),
                expected_to_pay=_from_cents(parts[5]),
            )
            await _send_placed(callback, partner, content, order, paid=parts[5] == "0")
            return
    except PickupError as exc:
        text = str(exc)
        if "изменилась" in text and action == "p":
            try:
                await _send_summary(callback, partner, parts[2], parts[3] == "1")
            except PickupError:
                pass
        await callback.answer(text, show_alert=True)
        return

    await callback.answer("Это действие устарело. Откройте корзину ещё раз.", show_alert=True)


async def _send_placed(callback: CallbackQuery, partner, content, order, *, paid: bool) -> None:
    time_label = await sync_to_async(pickup_time_label)(order)
    if paid:
        text = (
            f"Заказ #{order.public_id} на самовывоз оформлен и оплачен бонусами "
            f"({time_label}). Заведение скоро возьмёт его в работу."
        )
    else:
        text = (
            f"Заказ #{order.public_id} на самовывоз оформлен ({time_label}). "
            "Как только заведение подтвердит оплату, начнём готовить — пришлём сообщение."
        )
    await _reply(callback, text, edit=True)
    await callback.message.answer(
        "⁠",
        reply_markup=await _main_keyboard(partner, content, callback.from_user.id),
    )
    await callback.answer("Заказ оформлен.")
