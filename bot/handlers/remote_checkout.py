"""Guest checkout for remote channels (pickup, delivery): phone, address
(delivery), time, bonuses, transfer details, "I paid" (ADR-0009, PQ-032).

The flow is stateless: every step carries the channel and its choices in
``rc:<channel>:…`` callback data, so a bot restart does not lose it. The
amounts the guest saw travel in the final callback and are rechecked when
the order is placed (apps/billing/remote_orders.py:place_remote_order).

The delivery address is free text, so it is asked with a ForceReply prompt:
the guest's answer is recognised as a reply to that prompt, with no state
kept between messages. A Telegram location is optional and can be sent at any
time while the cart is a delivery cart.
"""

from __future__ import annotations

from decimal import Decimal

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from asgiref.sync import sync_to_async

from apps.billing.remote_orders import (
    CHANNEL_TITLES,
    RemoteOrderError,
    available_slots,
    closed_message,
    get_remote_quote,
    place_remote_order,
    save_guest_delivery_address,
    save_guest_delivery_location,
    save_guest_phone,
)
from apps.orders.models import Cart, Order
from apps.orders.services import requested_time_label
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
SHARE_LOCATION_LABEL = "📍 Отправить геолокацию"
# The guest's answer to this prompt is their delivery address (ForceReply).
ADDRESS_PROMPT = "🏠 Адрес доставки"

# One-letter channel codes keep callback_data short.
CHANNEL_CODES = {"p": Order.Channel.PICKUP, "d": Order.Channel.DELIVERY}
CODE_OF = {channel: code for code, channel in CHANNEL_CODES.items()}
TIME_QUESTIONS = {
    Order.Channel.PICKUP: "Когда заберёте заказ?",
    Order.Channel.DELIVERY: "Когда доставить?",
}


def _cents(value: Decimal) -> int:
    return int((Decimal(value) * 100).to_integral_value())


def _from_cents(value: str) -> Decimal:
    return (Decimal(int(value)) / 100).quantize(Decimal("0.01"))


def _cancel_row() -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text="Отмена", callback_data="rc:x")]


def _title(channel: str) -> str:
    return CHANNEL_TITLES[channel].capitalize()


def _supports(content: BotContent, channel: str) -> bool:
    if channel == Order.Channel.PICKUP:
        return content.supports_pickup_orders()
    if channel == Order.Channel.DELIVERY:
        return content.supports_delivery_orders()
    return False


def _cart_channel(partner_id, telegram_id: int) -> str | None:
    cart = (
        Cart.objects.filter(
            partner_id=partner_id,
            guest__telegram_account__telegram_id=telegram_id,
            status=Cart.Status.ACTIVE,
        )
        .order_by("-updated_at")
        .first()
    )
    return cart.channel if cart is not None else None


def _guest(partner_id, telegram_id: int) -> GuestProfile | None:
    return GuestProfile.objects.filter(
        partner_id=partner_id,
        telegram_account__telegram_id=telegram_id,
    ).first()


def _guest_phone(partner_id, telegram_id: int) -> str:
    guest = _guest(partner_id, telegram_id)
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


async def start_remote_checkout(target, bot: Bot, channel: str) -> None:
    """Entry point from the cart: ask for the phone once, then the time."""

    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    if channel not in CODE_OF or not _supports(content, channel):
        await _reply(target, "Этот способ заказа сейчас отключён.")
        return
    phone = await sync_to_async(_guest_phone)(partner.id, target.from_user.id)
    if not phone:
        await _reply(
            target,
            "Для заказа нужен ваш номер телефона — заведение свяжется, если что-то "
            "пойдёт не так. Нажмите кнопку ниже, номер сохранится один раз.",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text=SHARE_PHONE_LABEL, request_contact=True)]],
                resize_keyboard=True,
                one_time_keyboard=True,
            ),
        )
        return
    await _after_phone(target, partner, channel)


async def _after_phone(target, partner, channel: str) -> None:
    if channel == Order.Channel.DELIVERY:
        await _send_address_step(target, partner)
    else:
        await _send_time_step(target, partner, channel)


async def _ask_address(target) -> None:
    await message_of(target).answer(
        f"{ADDRESS_PROMPT}\nНапишите в ответ на это сообщение улицу, дом, квартиру, "
        "подъезд и этаж.",
        reply_markup=ForceReply(input_field_placeholder="Улица, дом, квартира"),
    )


async def _send_address_step(target, partner, *, edit: bool = False) -> None:
    """Offer the remembered address, or ask for one."""

    guest = await sync_to_async(_guest)(partner.id, target.from_user.id)
    if guest is None or not guest.delivery_address:
        await _ask_address(target)
        return
    code = CODE_OF[Order.Channel.DELIVERY]
    point = "\n📍 Точка на карте сохранена." if guest.delivery_latitude is not None else ""
    await _reply(
        target,
        f"<b>Доставка</b>\nДоставить сюда?\n{escape_html(guest.delivery_address)}{point}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Доставить сюда", callback_data=f"rc:{code}:a:ok")],
                [InlineKeyboardButton(text="✏️ Другой адрес", callback_data=f"rc:{code}:a:new")],
                _cancel_row(),
            ]
        ),
        edit=edit,
    )


def _is_address_answer(message: Message) -> bool:
    replied = message.reply_to_message
    return bool(
        message.text
        and replied is not None
        and replied.from_user is not None
        and replied.from_user.is_bot
        and (replied.text or "").startswith(ADDRESS_PROMPT)
    )


async def _send_time_step(target, partner, channel: str, *, edit: bool = False) -> None:
    slots = await sync_to_async(available_slots)(partner)
    if not slots:
        await _reply(target, await sync_to_async(closed_message)(partner), edit=edit)
        return
    code = CODE_OF[channel]
    rows = [
        [InlineKeyboardButton(text=SLOT_LABELS[slot], callback_data=f"rc:{code}:t:{slot}")]
        for slot in slots
    ]
    rows.append(_cancel_row())
    await _reply(
        target,
        f"<b>{_title(channel)}</b>\n{TIME_QUESTIONS[channel]}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        edit=edit,
    )


def _summary(
    quote, channel: str, slot: str, use_bonuses: bool
) -> tuple[str, InlineKeyboardMarkup]:
    lines = [f"<b>{_title(channel)}</b>", f"Время: {SLOT_LABELS[slot].lower()}"]
    if channel == Order.Channel.DELIVERY:
        lines.append(f"Адрес: {escape_html(quote.delivery_address)}")
        lines.append(f"Позиции: {quote.items_total} грн")
        lines.append(
            f"Доставка: {quote.delivery_fee} грн" if quote.delivery_fee else "Доставка: бесплатно"
        )
    lines.append(f"Сумма заказа: {quote.total} грн")
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
        f"rc:{CODE_OF[channel]}:p:{slot}:{int(use_bonuses)}:"
        f"{_cents(quote.total)}:{_cents(quote.to_pay)}"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=button_text, callback_data=callback_data)],
            _cancel_row(),
        ]
    )
    return "\n".join(lines), keyboard


async def _send_summary(
    callback: CallbackQuery, partner, channel: str, slot: str, use_bonuses: bool
) -> None:
    quote = await sync_to_async(get_remote_quote)(
        partner_id=partner.id,
        telegram_id=callback.from_user.id,
        use_bonuses=use_bonuses,
        channel=channel,
    )
    text, keyboard = _summary(quote, channel, slot, use_bonuses)
    await _reply(callback, text, reply_markup=keyboard, edit=True)


@router.message(F.contact)
async def contact_handler(message: Message, bot: Bot) -> None:
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
    except RemoteOrderError as exc:
        await message.answer(str(exc))
        return
    await message.answer(
        "Спасибо, номер сохранён.",
        reply_markup=await _main_keyboard(partner, content, message.from_user.id),
    )
    channel = await sync_to_async(_cart_channel)(partner.id, message.from_user.id)
    if channel in CODE_OF and _supports(content, channel):
        await _after_phone(message, partner, channel)


@router.message(_is_address_answer)
async def address_handler(message: Message, bot: Bot) -> None:
    """The guest answered the address prompt: remember it, go on to the time."""

    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    if not _supports(content, Order.Channel.DELIVERY):
        await message.answer("Доставка сейчас отключена.")
        return
    try:
        await sync_to_async(save_guest_delivery_address)(
            partner_id=partner.id,
            telegram_id=message.from_user.id,
            address=message.text,
        )
    except RemoteOrderError as exc:
        await message.answer(str(exc))
        await _ask_address(message)
        return
    await message.answer(
        "Адрес сохранён. Курьеру поможет точка на карте — отправьте геолокацию кнопкой "
        "ниже (по желанию).",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=SHARE_LOCATION_LABEL, request_location=True)]],
            resize_keyboard=True,
            one_time_keyboard=True,
        ),
    )
    await _send_time_step(message, partner, Order.Channel.DELIVERY)


@router.message(F.location)
async def location_handler(message: Message, bot: Bot) -> None:
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    channel = await sync_to_async(_cart_channel)(partner.id, message.from_user.id)
    if channel != Order.Channel.DELIVERY or not _supports(content, channel):
        await message.answer(
            "Геолокация нужна только для доставки.",
            reply_markup=await _main_keyboard(partner, content, message.from_user.id),
        )
        return
    try:
        await sync_to_async(save_guest_delivery_location)(
            partner_id=partner.id,
            telegram_id=message.from_user.id,
            latitude=message.location.latitude,
            longitude=message.location.longitude,
        )
    except RemoteOrderError as exc:
        await message.answer(str(exc))
        return
    await message.answer(
        "📍 Точка сохранена — курьер увидит её на карте.",
        reply_markup=await _main_keyboard(partner, content, message.from_user.id),
    )


@router.callback_query(F.data.startswith("rc:"))
async def checkout_callback_handler(callback: CallbackQuery, bot: Bot) -> None:
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    if callback.data == "rc:x":
        await _reply(callback, "Оформление отменено. Корзина сохранена.", edit=True)
        await callback.answer()
        return
    # rc:<channel code>:<action>:… — drop the channel so the steps below
    # read as before: parts[1] is the action.
    raw = (callback.data or "").split(":")
    channel = CHANNEL_CODES.get(raw[1]) if len(raw) > 2 else None
    parts = [raw[0], *raw[2:]]
    action = parts[1] if len(parts) > 1 else ""
    if channel is None or not _supports(content, channel):
        await callback.answer("Этот способ заказа сейчас отключён.", show_alert=True)
        return
    code = CODE_OF[channel]

    try:
        if action == "a" and len(parts) == 3 and channel == Order.Channel.DELIVERY:
            if parts[2] == "ok":
                await _send_time_step(callback, partner, channel, edit=True)
            else:
                await _reply(callback, "<b>Доставка</b>\nУкажите новый адрес.", edit=True)
                await _ask_address(callback)
            await callback.answer()
            return
        if action == "t" and len(parts) == 3 and parts[2] in SLOT_LABELS:
            slot = parts[2]
            quote = await sync_to_async(get_remote_quote)(
                partner_id=partner.id,
                telegram_id=callback.from_user.id,
                use_bonuses=False,
                channel=channel,
            )
            if quote.bonus_available > 0:
                await _reply(
                    callback,
                    f"<b>{_title(channel)}</b>\nМожно списать {quote.bonus_available} бонусов: "
                    f"к оплате будет {quote.total - quote.bonus_available} грн вместо "
                    f"{quote.total} грн.",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text=f"Списать {quote.bonus_available} бонусов",
                                    callback_data=f"rc:{code}:b:{slot}:1",
                                )
                            ],
                            [
                                InlineKeyboardButton(
                                    text="Без бонусов",
                                    callback_data=f"rc:{code}:b:{slot}:0",
                                )
                            ],
                            _cancel_row(),
                        ]
                    ),
                    edit=True,
                )
            else:
                await _send_summary(callback, partner, channel, slot, False)
            await callback.answer()
            return
        if action == "b" and len(parts) == 4 and parts[2] in SLOT_LABELS:
            await _send_summary(callback, partner, channel, parts[2], parts[3] == "1")
            await callback.answer()
            return
        if action == "p" and len(parts) == 6 and parts[2] in SLOT_LABELS:
            slot, use_bonuses = parts[2], parts[3] == "1"
            order = await sync_to_async(place_remote_order)(
                partner_id=partner.id,
                telegram_id=callback.from_user.id,
                slot=slot,
                use_bonuses=use_bonuses,
                expected_total=_from_cents(parts[4]),
                expected_to_pay=_from_cents(parts[5]),
                channel=channel,
            )
            await _send_placed(callback, partner, content, order, paid=parts[5] == "0")
            return
    except RemoteOrderError as exc:
        text = str(exc)
        if "изменилась" in text and action == "p":
            try:
                await _send_summary(callback, partner, channel, parts[2], parts[3] == "1")
            except RemoteOrderError:
                pass
        await callback.answer(text, show_alert=True)
        return

    await callback.answer("Это действие устарело. Откройте корзину ещё раз.", show_alert=True)


async def _send_placed(callback: CallbackQuery, partner, content, order, *, paid: bool) -> None:
    time_label = await sync_to_async(requested_time_label)(order)
    title = CHANNEL_TITLES[order.channel]
    if order.channel == Order.Channel.DELIVERY:
        time_label = f"{time_label}, {escape_html(order.delivery_address)}"
    if paid:
        text = (
            f"Заказ #{order.public_id} ({title}) оформлен и оплачен бонусами "
            f"({time_label}). Заведение скоро возьмёт его в работу."
        )
    else:
        text = (
            f"Заказ #{order.public_id} ({title}) оформлен ({time_label}). "
            "Как только заведение подтвердит оплату, начнём готовить — пришлём сообщение."
        )
    await _reply(callback, text, edit=True)
    await callback.message.answer(
        "⁠",
        reply_markup=await _main_keyboard(partner, content, callback.from_user.id),
    )
    await callback.answer("Заказ оформлен.")
