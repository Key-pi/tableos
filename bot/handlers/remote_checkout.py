"""Guest checkout for remote channels (pickup, delivery, shipping by post):
phone, address (delivery) or carrier and destination (shipping), time or
postage option, bonuses, how to pay, summary (ADR-0009, PQ-032–PQ-034).

The step after the address carries an "option" in the callback: a time slot
for pickup and delivery (``now`` / ``30`` / ``60``), the postage option for
shipping (``c`` — carrier's tariff, ``f`` — fixed price in the order).

Payment options are the module's settings: transfer in advance (requisites,
"I paid") and/or on receipt; with both on, the guest picks one
(``rc:<c>:m:<slot>:<bonus>:<t|r>``).

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
    ON_RECEIPT,
    PREPAID,
    RemoteOrderError,
    StaleQuoteError,
    available_slots,
    carrier_title,
    closed_message,
    get_remote_quote,
    get_shipping_terms,
    place_remote_order,
    save_guest_delivery_address,
    save_guest_delivery_location,
    save_guest_phone,
    save_guest_shipping_carrier,
    save_guest_shipping_destination,
)
from apps.orders.models import Cart, Order
from apps.orders.services import requested_time_label
from apps.users.models import GuestProfile
from bot.keyboards.main import build_main_keyboard
from bot.services.content import BotContent
from bot.services.context import resolve_partner_for_bot_token
from bot.services.navigation import resolve_guest_navigation_state
from bot.utils.html import escape_html
from core.i18n import N_, _, variants

router = Router()

# Catalog texts (PQ-040): translated where shown, ``_(SLOT_LABELS[slot])``.
SLOT_LABELS = {
    "now": N_("Как можно скорее"),
    "30": N_("Через 30 минут"),
    "60": N_("Через 1 час"),
}
SHARE_PHONE_LABEL = N_("📱 Поделиться номером")
SHARE_LOCATION_LABEL = N_("📍 Отправить геолокацию")
# The guest's answer to this prompt is their delivery address (ForceReply);
# the prompt is recognized in any language.
ADDRESS_PROMPT = N_("🏠 Адрес доставки")
# ... and this one, who receives a parcel by post and where (PQ-034).
DESTINATION_PROMPT = N_("📦 Куда отправить")

# One-letter channel codes keep callback_data short.
CHANNEL_CODES = {
    "p": Order.Channel.PICKUP,
    "d": Order.Channel.DELIVERY,
    "s": Order.Channel.SHIPPING,
}
CARRIER_CODES = {
    "np": Order.Carrier.NOVA_POSHTA,
    "up": Order.Carrier.UKRPOSHTA,
    "mm": Order.Carrier.MEEST,
}
CARRIER_CODE_OF = {carrier: code for code, carrier in CARRIER_CODES.items()}
SHIPPING_COST_CODES = {"c": Order.ShippingCost.CARRIER, "f": Order.ShippingCost.FIXED}
CODE_OF = {channel: code for code, channel in CHANNEL_CODES.items()}
# Payment option codes in callback data; a summary sent before PQ-033 has none
# and means transfer.
PAYMENT_CODES = {"t": PREPAID, "r": ON_RECEIPT}
PAYMENT_CODE_OF = {mode: code for code, mode in PAYMENT_CODES.items()}
# Where the guest pays on receipt.
ON_RECEIPT_WHERE = {
    Order.Channel.PICKUP: N_("на месте"),
    Order.Channel.DELIVERY: N_("курьеру"),
    Order.Channel.SHIPPING: N_("наложенным платежом"),
}
TIME_QUESTIONS = {
    Order.Channel.PICKUP: N_("Когда заберёте заказ?"),
    Order.Channel.DELIVERY: N_("Когда доставить?"),
}


def _cents(value: Decimal) -> int:
    return int((Decimal(value) * 100).to_integral_value())


def _from_cents(value: str) -> Decimal:
    return (Decimal(int(value)) / 100).quantize(Decimal("0.01"))


def _cancel_row() -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text=_("Отмена"), callback_data="rc:x")]


def _title(channel: str) -> str:
    return _(CHANNEL_TITLES[channel]).capitalize()


def _supports(content: BotContent, channel: str) -> bool:
    if channel == Order.Channel.PICKUP:
        return content.supports_pickup_orders()
    if channel == Order.Channel.DELIVERY:
        return content.supports_delivery_orders()
    if channel == Order.Channel.SHIPPING:
        return content.supports_shipping_orders()
    return False


def _option_ok(channel: str, option: str) -> bool:
    if channel == Order.Channel.SHIPPING:
        return option in SHIPPING_COST_CODES
    return option in SLOT_LABELS


def _shipping_cost(channel: str, option: str) -> str:
    return SHIPPING_COST_CODES.get(option, "") if channel == Order.Channel.SHIPPING else ""


def _quote(partner, telegram_id: int, channel: str, option: str, use_bonuses: bool):
    return get_remote_quote(
        partner_id=partner.id,
        telegram_id=telegram_id,
        use_bonuses=use_bonuses,
        channel=channel,
        shipping_cost=_shipping_cost(channel, option),
    )


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
        await _reply(target, _("Этот способ заказа сейчас отключён."))
        return
    phone = await sync_to_async(_guest_phone)(partner.id, target.from_user.id)
    if not phone:
        await _reply(
            target,
            _(
                "Для заказа нужен ваш номер телефона — заведение свяжется, если что-то "
                "пойдёт не так. Нажмите кнопку ниже, номер сохранится один раз."
            ),
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text=_(SHARE_PHONE_LABEL), request_contact=True)]],
                resize_keyboard=True,
                one_time_keyboard=True,
            ),
        )
        return
    await _after_phone(target, partner, channel)


async def _after_phone(target, partner, channel: str) -> None:
    if channel == Order.Channel.DELIVERY:
        await _send_address_step(target, partner)
    elif channel == Order.Channel.SHIPPING:
        await _send_destination_step(target, partner)
    else:
        await _send_time_step(target, partner, channel)


async def _send_destination_step(target, partner, *, edit: bool = False) -> None:
    """Shipping: offer the remembered carrier and destination, or ask anew."""

    guest = await sync_to_async(_guest)(partner.id, target.from_user.id)
    terms = await sync_to_async(get_shipping_terms)(partner.id)
    if guest is None or not guest.shipping_destination or (
        guest.shipping_carrier not in terms.carriers
    ):
        await _send_carrier_step(target, partner, terms, edit=edit)
        return
    await _reply(
        target,
        _(
            "<b>Отправка почтой</b>\nОтправить сюда?\n{carrier_title}: {shipping_destination}",
            carrier_title=escape_html(carrier_title(guest.shipping_carrier)),
            shipping_destination=escape_html(guest.shipping_destination),
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=_("✅ Отправить сюда"), callback_data="rc:s:a:ok")],
                [InlineKeyboardButton(text=_("✏️ Другой получатель"), callback_data="rc:s:a:new")],
                _cancel_row(),
            ]
        ),
        edit=edit,
    )


async def _send_carrier_step(target, partner, terms=None, *, edit: bool = False) -> None:
    terms = terms or await sync_to_async(get_shipping_terms)(partner.id)
    if len(terms.carriers) == 1:
        carrier = terms.carriers[0]
        await sync_to_async(save_guest_shipping_carrier)(
            partner_id=partner.id, telegram_id=target.from_user.id, carrier=carrier
        )
        await _ask_destination(target, carrier)
        return
    rows = [
        [
            InlineKeyboardButton(
                text=carrier_title(carrier),
                callback_data=f"rc:s:c:{CARRIER_CODE_OF[carrier]}",
            )
        ]
        for carrier in terms.carriers
    ]
    rows.append(_cancel_row())
    await _reply(
        target,
        _("<b>Отправка почтой</b>\nКакой службой отправить?"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        edit=edit,
    )


async def _ask_destination(target, carrier: str) -> None:
    title = carrier_title(carrier)
    await message_of(target).answer(
        _(DESTINATION_PROMPT)
        + (f" ({escape_html(title)})" if title else "")
        + "\n"
        + _(
            "Напишите в ответ на это сообщение: ФИО получателя, город и номер отделения "
            "или почтомата. Например: Іваненко Іван, Київ, відділення 25."
        ),
        reply_markup=ForceReply(input_field_placeholder=_("ФИО, город, отделение")),
    )


def _is_destination_answer(message: Message) -> bool:
    replied = message.reply_to_message
    return bool(
        message.text
        and replied is not None
        and replied.from_user is not None
        and replied.from_user.is_bot
        and (replied.text or "").startswith(tuple(variants(DESTINATION_PROMPT)))
    )


async def _send_cost_step(target, partner, *, edit: bool = False) -> None:
    """Shipping: who pays the postage, when the owner offers both options."""

    terms = await sync_to_async(get_shipping_terms)(partner.id)
    codes = {cost: code for code, cost in SHIPPING_COST_CODES.items()}
    if len(terms.cost_options) == 1:
        await _after_option(
            target, partner, Order.Channel.SHIPPING, codes[terms.cost_options[0]], edit=edit
        )
        return
    await _reply(
        target,
        _("<b>Отправка почтой</b>\nКак оплатить пересылку?"),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=_("📮 На почте по тарифу перевозчика"),
                        callback_data="rc:s:t:c",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=_("📦 В заказе: {fixed_label}", fixed_label=terms.fixed_label()),
                        callback_data="rc:s:t:f",
                    )
                ],
                _cancel_row(),
            ]
        ),
        edit=edit,
    )


async def _after_option(target, partner, channel: str, option: str, *, edit: bool) -> None:
    """After the time slot or postage option: bonuses if any, then the summary."""

    code = CODE_OF[channel]
    quote = await sync_to_async(_quote)(partner, target.from_user.id, channel, option, False)
    if quote.bonus_available > 0:
        await _reply(
            target,
            _(
                "<b>{title}</b>\n"
                "Можно списать {bonus_available} бонусов: к оплате будет {to_pay} грн "
                "вместо {total} грн.",
                title=_title(channel),
                bonus_available=quote.bonus_available,
                to_pay=quote.total - quote.bonus_available,
                total=quote.total,
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=_(
                                "Списать {bonus_available} бонусов",
                                bonus_available=quote.bonus_available,
                            ),
                            callback_data=f"rc:{code}:b:{option}:1",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text=_("Без бонусов"),
                            callback_data=f"rc:{code}:b:{option}:0",
                        )
                    ],
                    _cancel_row(),
                ]
            ),
            edit=edit,
        )
    else:
        await _send_summary(target, partner, channel, option, False)


async def _ask_address(target) -> None:
    await message_of(target).answer(
        _(ADDRESS_PROMPT)
        + "\n"
        + _("Напишите в ответ на это сообщение улицу, дом, квартиру, подъезд и этаж."),
        reply_markup=ForceReply(input_field_placeholder=_("Улица, дом, квартира")),
    )


async def _send_address_step(target, partner, *, edit: bool = False) -> None:
    """Offer the remembered address, or ask for one."""

    guest = await sync_to_async(_guest)(partner.id, target.from_user.id)
    if guest is None or not guest.delivery_address:
        await _ask_address(target)
        return
    code = CODE_OF[Order.Channel.DELIVERY]
    point = _("\n📍 Точка на карте сохранена.") if guest.delivery_latitude is not None else ""
    await _reply(
        target,
        _(
            "<b>Доставка</b>\nДоставить сюда?\n{delivery_address}{point}",
            delivery_address=escape_html(guest.delivery_address),
            point=point,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=_("✅ Доставить сюда"), callback_data=f"rc:{code}:a:ok"
                    )
                ],
                [InlineKeyboardButton(text=_("✏️ Другой адрес"), callback_data=f"rc:{code}:a:new")],
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
        and (replied.text or "").startswith(tuple(variants(ADDRESS_PROMPT)))
    )


async def _send_time_step(target, partner, channel: str, *, edit: bool = False) -> None:
    slots = await sync_to_async(available_slots)(partner, channel=channel)
    if not slots:
        await _reply(target, await sync_to_async(closed_message)(partner, channel), edit=edit)
        return
    code = CODE_OF[channel]
    rows = [
        [InlineKeyboardButton(text=_(SLOT_LABELS[slot]), callback_data=f"rc:{code}:t:{slot}")]
        for slot in slots
    ]
    rows.append(_cancel_row())
    await _reply(
        target,
        f"<b>{_title(channel)}</b>\n{_(TIME_QUESTIONS[channel])}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        edit=edit,
    )


def _summary(
    quote, channel: str, slot: str, use_bonuses: bool, payment: str = PREPAID
) -> tuple[str, InlineKeyboardMarkup]:
    lines = [f"<b>{_title(channel)}</b>"]
    if channel == Order.Channel.SHIPPING:
        lines.append(
            _(
                "Служба: {carrier_title}",
                carrier_title=escape_html(carrier_title(quote.shipping_carrier)),
            )
        )
        lines.append(
            _(
                "Получатель: {shipping_destination}",
                shipping_destination=escape_html(quote.shipping_destination),
            )
        )
        lines.append(_("Позиции: {items_total} грн", items_total=quote.items_total))
        if quote.shipping_cost == Order.ShippingCost.CARRIER:
            lines.append(_("Пересылка: по тарифу перевозчика — оплатите на почте"))
        else:
            lines.append(
                _("Пересылка: {delivery_fee} грн", delivery_fee=quote.delivery_fee)
                if quote.delivery_fee
                else _("Пересылка: бесплатно")
            )
    else:
        lines.append(_("Время: {time}", time=_(SLOT_LABELS[slot]).lower()))
    if channel == Order.Channel.DELIVERY:
        lines.append(
            _("Адрес: {delivery_address}", delivery_address=escape_html(quote.delivery_address))
        )
        lines.append(_("Позиции: {items_total} грн", items_total=quote.items_total))
        lines.append(
            _("Доставка: {delivery_fee} грн", delivery_fee=quote.delivery_fee)
            if quote.delivery_fee
            else _("Доставка: бесплатно")
        )
    lines.append(_("Сумма заказа: {total} грн", total=quote.total))
    if quote.bonus_to_spend:
        lines.append(_("Бонусами: −{bonus_to_spend} грн", bonus_to_spend=quote.bonus_to_spend))
    if quote.to_pay > 0 and payment == ON_RECEIPT:
        how = (
            _("Оплатите наложенным платежом при получении на почте.")
            if channel == Order.Channel.SHIPPING
            else _("Оплатите {where} наличными или картой.", where=_(ON_RECEIPT_WHERE[channel]))
        )
        lines.extend([_("<b>К оплате при получении: {to_pay} грн</b>", to_pay=quote.to_pay), how])
        button_text = _("✅ Оформить заказ")
    elif quote.to_pay > 0:
        lines.extend(
            [
                _("<b>К оплате переводом: {to_pay} грн</b>", to_pay=quote.to_pay),
                "",
                _("Реквизиты:"),
                escape_html(quote.payment_details),
                "",
                _(
                    "Переведите сумму и нажмите «Я оплатил(а)». Заведение проверит "
                    "поступление и начнёт готовить."
                ),
            ]
        )
        button_text = _("✅ Я оплатил(а)")
    else:
        lines.append(_("Заказ полностью оплачивается бонусами."))
        button_text = _("✅ Оформить")
    callback_data = (
        f"rc:{CODE_OF[channel]}:p:{slot}:{int(use_bonuses)}:"
        f"{_cents(quote.total)}:{_cents(quote.to_pay)}:{PAYMENT_CODE_OF[payment]}"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=button_text, callback_data=callback_data)],
            _cancel_row(),
        ]
    )
    return "\n".join(lines), keyboard


async def _send_summary(
    callback: CallbackQuery,
    partner,
    channel: str,
    slot: str,
    use_bonuses: bool,
    payment: str | None = None,
) -> None:
    """The summary; with several payment options and none chosen, ask first."""

    quote = await sync_to_async(_quote)(
        partner, callback.from_user.id, channel, slot, use_bonuses
    )
    if payment is None:
        if len(quote.payment_modes) > 1 and quote.to_pay > 0:
            await _send_payment_step(callback, channel, slot, use_bonuses, quote.to_pay)
            return
        payment = quote.payment_modes[0]
    elif payment not in quote.payment_modes:
        raise RemoteOrderError(_("Этот способ оплаты сейчас недоступен — выберите другой."))
    text, keyboard = _summary(quote, channel, slot, use_bonuses, payment)
    await _reply(callback, text, reply_markup=keyboard, edit=True)


async def _send_payment_step(
    callback: CallbackQuery, channel: str, slot: str, use_bonuses: bool, to_pay
) -> None:
    prefix = f"rc:{CODE_OF[channel]}:m:{slot}:{int(use_bonuses)}"
    await _reply(
        callback,
        _(
            "<b>{title}</b>\nК оплате {to_pay} грн. Как оплатите?",
            title=_title(channel),
            to_pay=to_pay,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=_("💳 Переводом сейчас"), callback_data=f"{prefix}:t")],
                [
                    InlineKeyboardButton(
                        text=_("💵 При получении ({where})", where=_(ON_RECEIPT_WHERE[channel])),
                        callback_data=f"{prefix}:r",
                    )
                ],
                _cancel_row(),
            ]
        ),
        edit=True,
    )


@router.message(F.contact)
async def contact_handler(message: Message, bot: Bot) -> None:
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    contact = message.contact
    if contact.user_id != message.from_user.id:
        await message.answer(
            _("Отправьте, пожалуйста, свой номер — кнопкой ниже."),
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text=_(SHARE_PHONE_LABEL), request_contact=True)]],
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
        _("Спасибо, номер сохранён."),
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
        await message.answer(_("Доставка сейчас отключена."))
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
        _(
            "Адрес сохранён. Курьеру поможет точка на карте — отправьте геолокацию кнопкой "
            "ниже (по желанию)."
        ),
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=_(SHARE_LOCATION_LABEL), request_location=True)]],
            resize_keyboard=True,
            one_time_keyboard=True,
        ),
    )
    await _send_time_step(message, partner, Order.Channel.DELIVERY)


@router.message(_is_destination_answer)
async def destination_handler(message: Message, bot: Bot) -> None:
    """The guest answered «Куда отправить»: remember it, go on to the postage."""

    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    if not _supports(content, Order.Channel.SHIPPING):
        await message.answer(_("Отправка почтой сейчас отключена."))
        return
    try:
        await sync_to_async(save_guest_shipping_destination)(
            partner_id=partner.id,
            telegram_id=message.from_user.id,
            text=message.text,
        )
    except RemoteOrderError as exc:
        await message.answer(str(exc))
        guest = await sync_to_async(_guest)(partner.id, message.from_user.id)
        await _ask_destination(message, guest.shipping_carrier if guest is not None else "")
        return
    try:
        await _send_cost_step(message, partner)
    except RemoteOrderError as exc:
        await message.answer(str(exc))


@router.message(F.location)
async def location_handler(message: Message, bot: Bot) -> None:
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    channel = await sync_to_async(_cart_channel)(partner.id, message.from_user.id)
    if channel != Order.Channel.DELIVERY or not _supports(content, channel):
        await message.answer(
            _("Геолокация нужна только для доставки."),
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
        _("📍 Точка сохранена — курьер увидит её на карте."),
        reply_markup=await _main_keyboard(partner, content, message.from_user.id),
    )


@router.callback_query(F.data.startswith("rc:"))
async def checkout_callback_handler(callback: CallbackQuery, bot: Bot) -> None:
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    if callback.data == "rc:x":
        await _reply(callback, _("Оформление отменено. Корзина сохранена."), edit=True)
        await callback.answer()
        return
    # rc:<channel code>:<action>:… — drop the channel so the steps below
    # read as before: parts[1] is the action.
    raw = (callback.data or "").split(":")
    channel = CHANNEL_CODES.get(raw[1]) if len(raw) > 2 else None
    parts = [raw[0], *raw[2:]]
    action = parts[1] if len(parts) > 1 else ""
    if channel is None or not _supports(content, channel):
        await callback.answer(_("Этот способ заказа сейчас отключён."), show_alert=True)
        return

    try:
        if action == "a" and len(parts) == 3 and channel == Order.Channel.SHIPPING:
            if parts[2] == "ok":
                await _send_cost_step(callback, partner, edit=True)
            else:
                await _send_carrier_step(callback, partner, edit=True)
            await callback.answer()
            return
        if action == "c" and len(parts) == 3 and channel == Order.Channel.SHIPPING:
            carrier = CARRIER_CODES.get(parts[2])
            if carrier is None:
                raise RemoteOrderError(_("Выберите службу ещё раз."))
            await sync_to_async(save_guest_shipping_carrier)(
                partner_id=partner.id, telegram_id=callback.from_user.id, carrier=carrier
            )
            await _reply(
                callback,
                _(
                    "<b>Отправка почтой</b>\nСлужба: {carrier_title}",
                    carrier_title=escape_html(carrier_title(carrier)),
                ),
                edit=True,
            )
            await _ask_destination(callback, carrier)
            await callback.answer()
            return
        if action == "a" and len(parts) == 3 and channel == Order.Channel.DELIVERY:
            if parts[2] == "ok":
                await _send_time_step(callback, partner, channel, edit=True)
            else:
                await _reply(callback, _("<b>Доставка</b>\nУкажите новый адрес."), edit=True)
                await _ask_address(callback)
            await callback.answer()
            return
        if action == "t" and len(parts) == 3 and _option_ok(channel, parts[2]):
            await _after_option(callback, partner, channel, parts[2], edit=True)
            await callback.answer()
            return
        if action == "b" and len(parts) == 4 and _option_ok(channel, parts[2]):
            await _send_summary(callback, partner, channel, parts[2], parts[3] == "1")
            await callback.answer()
            return
        if (
            action == "m"
            and len(parts) == 5
            and _option_ok(channel, parts[2])
            and parts[4] in PAYMENT_CODES
        ):
            await _send_summary(
                callback, partner, channel, parts[2], parts[3] == "1", PAYMENT_CODES[parts[4]]
            )
            await callback.answer()
            return
        if (
            action == "p"
            and len(parts) in (6, 7)
            and _option_ok(channel, parts[2])
            and (len(parts) == 6 or parts[6] in PAYMENT_CODES)
        ):
            slot, use_bonuses = parts[2], parts[3] == "1"
            payment = PAYMENT_CODES[parts[6]] if len(parts) == 7 else PREPAID
            order = await sync_to_async(place_remote_order)(
                partner_id=partner.id,
                telegram_id=callback.from_user.id,
                slot=slot if channel != Order.Channel.SHIPPING else "now",
                shipping_cost=_shipping_cost(channel, slot),
                use_bonuses=use_bonuses,
                expected_total=_from_cents(parts[4]),
                expected_to_pay=_from_cents(parts[5]),
                channel=channel,
                payment=payment,
            )
            await _send_placed(
                callback, partner, content, order, to_pay=_from_cents(parts[5])
            )
            return
    except RemoteOrderError as exc:
        text = str(exc)
        if isinstance(exc, StaleQuoteError) and action == "p":
            payment = PAYMENT_CODES.get(parts[6]) if len(parts) == 7 else PREPAID
            try:
                await _send_summary(
                    callback, partner, channel, parts[2], parts[3] == "1", payment
                )
            except RemoteOrderError:
                pass
        await callback.answer(text, show_alert=True)
        return

    await callback.answer(_("Это действие устарело. Откройте корзину ещё раз."), show_alert=True)


async def _send_placed(callback: CallbackQuery, partner, content, order, *, to_pay) -> None:
    time_label = await sync_to_async(requested_time_label)(order)
    title = _(CHANNEL_TITLES[order.channel])
    if order.channel == Order.Channel.DELIVERY:
        time_label = f"{time_label}, {escape_html(order.delivery_address)}"
    if order.channel == Order.Channel.SHIPPING:
        time_label = (
            f"{escape_html(order.get_shipping_carrier_display())}, "
            f"{escape_html(order.shipping_destination)}"
        )
    work = _("собирать") if order.channel == Order.Channel.SHIPPING else _("готовить")
    if to_pay > 0 and order.payment_timing == ON_RECEIPT:
        how = (
            _("наложенным платежом на почте")
            if order.channel == Order.Channel.SHIPPING
            else _("при получении")
        )
        text = _(
            "Заказ #{public_id} ({title}) оформлен ({time_label}). Оплата {how}: "
            "{to_pay} грн. Пришлём сообщение, когда начнём {work}.",
            public_id=order.public_id,
            title=title,
            time_label=time_label,
            how=how,
            to_pay=to_pay,
            work=work,
        )
    elif to_pay <= 0:
        text = _(
            "Заказ #{public_id} ({title}) оформлен и оплачен бонусами ({time_label}). "
            "Заведение скоро возьмёт его в работу.",
            public_id=order.public_id,
            title=title,
            time_label=time_label,
        )
    else:
        text = _(
            "Заказ #{public_id} ({title}) оформлен ({time_label}). Как только "
            "заведение подтвердит оплату, начнём {work} — пришлём сообщение.",
            public_id=order.public_id,
            title=title,
            time_label=time_label,
            work=work,
        )
    await _reply(callback, text, edit=True)
    await callback.message.answer(
        "⁠",
        reply_markup=await _main_keyboard(partner, content, callback.from_user.id),
    )
    await callback.answer(_("Заказ оформлен."))
