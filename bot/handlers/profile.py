import re
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message
from asgiref.sync import sync_to_async
from django.utils import translation

from apps.billing.remote_orders import tracking_link
from apps.billing.services import get_order_refunded_money
from apps.bonuses.selectors import (
    active_bonus_programs_for_partner,
    recent_bonus_transactions_for_guest,
)
from apps.orders.models import Order
from apps.orders.selectors import get_guest_orders
from apps.orders.services import requested_time_label
from apps.partners.languages import enabled_languages
from apps.tables.services import get_active_table_session
from apps.users.models import GuestProfile
from apps.users.services import (
    GuestLanguageError,
    get_or_create_guest_profile,
    set_guest_language,
)
from bot.filters.content import PartnerButtonFilter
from bot.keyboards.main import build_main_keyboard
from bot.keyboards.profile import (
    build_language_keyboard,
    build_my_order_keyboard,
    build_my_orders_keyboard,
    build_profile_bonuses_keyboard,
    build_profile_summary_keyboard,
)
from bot.services.content import BotContent
from bot.services.context import resolve_partner_for_bot_token
from bot.services.language import guest_language_choice, language_context, partner_bot_settings
from bot.services.navigation import resolve_guest_navigation_state
from bot.utils.html import escape_html
from core.i18n import LANGUAGE_NAMES, _

router = Router()

_BONUS_TRANSACTIONS_PAGE_SIZE = 5
_BONUS_PROGRAMS_PREVIEW_LIMIT = 4


def _format_dt(value) -> str:
    if not value:
        return "—"
    return value.strftime("%d.%m.%Y %H:%M")


def _render_profile_summary(
    *,
    partner_name: str,
    guest_profile,
    active_session,
) -> str:
    telegram_username = guest_profile.telegram_account.username
    identity_line = f"@{telegram_username}" if telegram_username else str(
        guest_profile.telegram_account.telegram_id
    )
    lines = [
        _("<b>Мой профиль</b>"),
        _("Заведение: {partner_name}", partner_name=escape_html(partner_name)),
        f"Telegram: {identity_line}",
        _("Код клиента: <code>{customer_code}</code>", customer_code=guest_profile.customer_code),
        _("Баланс: {loyalty_balance} бонусов", loyalty_balance=guest_profile.loyalty_balance),
        "",
        _("<b>Статус</b>"),
        (
            _("Активный стол: #{number}", number=active_session.table.number)
            if active_session is not None
            else _("Активного стола сейчас нет.")
        ),
        _("Первый визит: {date}", date=_format_dt(guest_profile.first_visit_at)),
        _("Последний визит: {date}", date=_format_dt(guest_profile.last_visit_at)),
        "",
        _("Код клиента можно назвать на баре или кассе для бонусов и быстрых продаж."),
    ]
    return "\n".join(lines)


def _render_profile_bonuses(
    *,
    guest_profile,
    active_programs,
    recent_transactions,
    page: int,
) -> tuple[str, int]:
    page = max(page, 0)
    total_transactions = len(recent_transactions)
    total_pages = max(1, (total_transactions + _BONUS_TRANSACTIONS_PAGE_SIZE - 1) // _BONUS_TRANSACTIONS_PAGE_SIZE)
    page = min(page, total_pages - 1)
    start = page * _BONUS_TRANSACTIONS_PAGE_SIZE
    end = start + _BONUS_TRANSACTIONS_PAGE_SIZE
    page_transactions = recent_transactions[start:end]

    lines = [
        _("<b>Бонусы</b>"),
        _(
            "Текущий баланс: {loyalty_balance} бонусов",
            loyalty_balance=guest_profile.loyalty_balance,
        ),
        "",
        _("<b>Активные программы</b>"),
    ]
    if active_programs:
        preview_programs = active_programs[:_BONUS_PROGRAMS_PREVIEW_LIMIT]
        for program in preview_programs:
            lines.append(
                f"• {escape_html(program.name)} — {program.get_program_type_display()} / "
                f"{program.get_trigger_event_display()}"
            )
        hidden_programs = len(active_programs) - len(preview_programs)
        if hidden_programs > 0:
            lines.append(_("• И ещё программ: {hidden_programs}", hidden_programs=hidden_programs))
    else:
        lines.append(_("• Сейчас активных бонусных программ нет."))

    lines.extend(["", _("<b>Последние движения</b>")])
    if page_transactions:
        for transaction in page_transactions:
            program_name = (
                transaction.program.name if transaction.program_id else _("Ручная операция")
            )
            sign = "+" if transaction.amount >= 0 else ""
            lines.append(
                _(
                    "• {sign}{amount} через {program_name} ({created})",
                    sign=sign,
                    amount=transaction.amount,
                    program_name=escape_html(program_name),
                    created=f"{transaction.created_at:%d.%m %H:%M}",
                )
            )
    else:
        lines.append(_("• История бонусов пока пуста."))
    if total_pages > 1:
        lines.append("")
        lines.append(
            _("Страница истории: {page}/{total_pages}", page=page + 1, total_pages=total_pages)
        )
    return "\n".join(lines), total_pages


async def _safe_profile_message_edit(message: Message, text: str, *, reply_markup) -> bool:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
        return True
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return False
        raise


def _offers_language_choice(partner) -> bool:
    return len(enabled_languages(partner_bot_settings(partner))) > 1


@router.message(PartnerButtonFilter("button_my_profile"))
async def profile_handler(message: Message, bot: Bot) -> None:
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    guest_profile = await sync_to_async(get_or_create_guest_profile)(
        partner_id=partner.id,
        telegram_id=message.from_user.id,
        username=message.from_user.username or "",
        first_name=message.from_user.first_name or "",
        last_name=message.from_user.last_name or "",
        language_code=message.from_user.language_code or "",
    )
    session = await sync_to_async(get_active_table_session)(
        partner_id=partner.id,
        telegram_id=message.from_user.id,
    )
    await message.answer(
        _render_profile_summary(
            partner_name=partner.name,
            guest_profile=guest_profile,
            active_session=session,
        ),
        reply_markup=build_profile_summary_keyboard(
            bonuses_label=content.button_profile_bonuses_label,
            show_language=await sync_to_async(_offers_language_choice)(partner),
        ),
    )


@router.callback_query(F.data == "guestprofile:summary")
async def profile_summary_callback(callback: CallbackQuery, bot: Bot) -> None:
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    guest_profile = await sync_to_async(get_or_create_guest_profile)(
        partner_id=partner.id,
        telegram_id=callback.from_user.id,
        username=callback.from_user.username or "",
        first_name=callback.from_user.first_name or "",
        last_name=callback.from_user.last_name or "",
        language_code=callback.from_user.language_code or "",
    )
    session = await sync_to_async(get_active_table_session)(
        partner_id=partner.id,
        telegram_id=callback.from_user.id,
    )
    updated = await _safe_profile_message_edit(
        callback.message,
        _render_profile_summary(
            partner_name=partner.name,
            guest_profile=guest_profile,
            active_session=session,
        ),
        reply_markup=build_profile_summary_keyboard(
            bonuses_label=content.button_profile_bonuses_label,
            show_language=await sync_to_async(_offers_language_choice)(partner),
        ),
    )
    if updated:
        await callback.answer()
    else:
        await callback.answer(_("Без изменений"))


@router.callback_query(F.data.startswith("guestprofile:bonuses:"))
async def profile_bonuses_callback(callback: CallbackQuery, bot: Bot) -> None:
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    guest_profile = await sync_to_async(get_or_create_guest_profile)(
        partner_id=partner.id,
        telegram_id=callback.from_user.id,
        username=callback.from_user.username or "",
        first_name=callback.from_user.first_name or "",
        last_name=callback.from_user.last_name or "",
        language_code=callback.from_user.language_code or "",
    )
    try:
        page = int((callback.data or "").rsplit(":", 1)[1])
    except (IndexError, ValueError):
        page = 0
    active_programs = await sync_to_async(list)(active_bonus_programs_for_partner(partner.id))
    recent_transactions = await sync_to_async(list)(
        recent_bonus_transactions_for_guest(guest_profile.id, limit=25)
    )
    text, total_pages = _render_profile_bonuses(
        guest_profile=guest_profile,
        active_programs=active_programs,
        recent_transactions=recent_transactions,
        page=page,
    )
    normalized_page = min(max(page, 0), total_pages - 1)
    updated = await _safe_profile_message_edit(
        callback.message,
        text,
        reply_markup=build_profile_bonuses_keyboard(
            page=normalized_page,
            total_pages=total_pages,
        ),
    )
    if updated:
        await callback.answer()
    else:
        await callback.answer(_("Без изменений"))


# «Мои заказы»: the guest's own orders with status, payment and ТТН.
_MY_ORDERS_PAGE_SIZE = 5
_MY_ORDERS_CALLBACK = re.compile(r"^guestorders:(\d{1,4})$|^guestorder:([0-9a-f]{1,32})$")


def _payment_line(order: Order) -> str:
    if order.status == Order.Status.CANCELED:
        return ""
    if order.paid_at is not None:
        return _("оплачен")
    if order.payment_timing == Order.PaymentTiming.ON_RECEIPT:
        if order.channel == Order.Channel.SHIPPING:
            return _("наложенным платежом при получении")
        return _("оплата при получении")
    if order.payment_timing == Order.PaymentTiming.PREPAID:
        return _("ждёт подтверждения перевода")
    return _("не оплачен")


def _local(order: Order, value):
    return value.astimezone(ZoneInfo(order.partner.timezone))


def _render_my_orders(orders: list[Order], *, page: int, total_pages: int) -> str:
    if not orders:
        return _("<b>Мои заказы</b>\nЗаказов пока нет.")
    lines = [_("<b>Мои заказы</b>")]
    for order in orders:
        payment = _payment_line(order)
        details = _(
            "{status} · {total_amount} грн",
            status=order.get_status_display(),
            total_amount=order.total_amount,
        )
        if payment:
            details += f" · {payment}"
        lines += [
            "",
            f"<b>#{escape_html(order.public_id)}</b> · "
            f"{_local(order, order.created_at):%d.%m %H:%M} · "
            f"{escape_html(order.get_channel_display())}",
            details,
        ]
        if order.tracking_number:
            lines.append(
                _(
                    "ТТН <code>{tracking_number}</code>",
                    tracking_number=escape_html(order.tracking_number),
                )
            )
    if total_pages > 1:
        lines += ["", _("Страница {page}/{total_pages}", page=page + 1, total_pages=total_pages)]
    return "\n".join(lines)


def _render_my_order(order: Order) -> str:
    lines = [
        _("<b>Заказ #{public_id}</b>", public_id=escape_html(order.public_id)),
        f"{escape_html(order.get_channel_display())} · "
        f"{_local(order, order.created_at):%d.%m.%Y %H:%M}",
        _("Статус: {status}", status=order.get_status_display()),
    ]
    if order.channel == Order.Channel.TABLE and order.table is not None:
        lines.append(_("Стол #{number}", number=order.table.number))
    elif order.channel in (Order.Channel.PICKUP, Order.Channel.DELIVERY):
        lines.append(
            _(
                "Время: {requested_time_label}",
                requested_time_label=escape_html(requested_time_label(order)),
            )
        )
    if order.channel == Order.Channel.DELIVERY:
        lines.append(
            _("Адрес: {delivery_address}", delivery_address=escape_html(order.delivery_address))
        )
    if order.channel == Order.Channel.SHIPPING:
        lines.append(
            f"{escape_html(order.get_shipping_carrier_display())}: "
            f"{escape_html(order.shipping_destination)}"
        )
        if order.tracking_number:
            lines.append(
                _(
                    "ТТН <code>{tracking_number}</code>",
                    tracking_number=escape_html(order.tracking_number),
                )
            )
            link = tracking_link(order.shipping_carrier, order.tracking_number)
            if link:
                lines.append(_('<a href="{link}">Отследить посылку</a>', link=escape_html(link)))
    lines.append("")
    for item in order.items.all():
        lines.append(
            _(
                "• {item_name} × {quantity} — {line_total} грн",
                item_name=escape_html(item.item_name),
                quantity=item.quantity,
                line_total=item.unit_price * item.quantity,
            )
        )
    lines.append(_("<b>Итого: {total_amount} грн</b>", total_amount=order.total_amount))
    if order.bonus_spent:
        lines.append(_("Оплачено бонусами: {bonus_spent}", bonus_spent=order.bonus_spent))
    payment = _payment_line(order)
    if payment:
        lines.append(_("Оплата: {payment}", payment=payment))
    refunded = getattr(order, "refunded_money", 0)
    if refunded:
        lines.append(_("Возвращено: {refunded} грн", refunded=refunded))
    return "\n".join(lines)


def _can_confirm_from_list(order: Order) -> bool:
    # Without a table only a parcel on its way is confirmed by the guest
    # (orders/services.py:confirm_order_received_by_guest, PQ-034).
    return order.channel == Order.Channel.SHIPPING and order.status == Order.Status.DELIVERING


def _load_my_orders(partner_id, telegram_id: int, page: int):
    queryset = get_guest_orders(partner_id=partner_id, telegram_id=telegram_id)
    total = queryset.count()
    total_pages = max(1, (total + _MY_ORDERS_PAGE_SIZE - 1) // _MY_ORDERS_PAGE_SIZE)
    page = min(max(page, 0), total_pages - 1)
    start = page * _MY_ORDERS_PAGE_SIZE
    return list(queryset[start:start + _MY_ORDERS_PAGE_SIZE]), page, total_pages


def _load_my_order(partner_id, telegram_id: int, public_id: str):
    order = (
        get_guest_orders(partner_id=partner_id, telegram_id=telegram_id)
        .prefetch_related("items")
        .filter(public_id=public_id)
        .first()
    )
    if order is not None:
        order.refunded_money = get_order_refunded_money(order)
    return order


@router.callback_query(lambda c: bool(c.data and _MY_ORDERS_CALLBACK.match(c.data)))
async def my_orders_callback(callback: CallbackQuery, bot: Bot) -> None:
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    match = _MY_ORDERS_CALLBACK.match(callback.data)
    if match.group(1) is not None:
        orders, page, total_pages = await sync_to_async(_load_my_orders)(
            partner.id, callback.from_user.id, int(match.group(1))
        )
        text = _render_my_orders(orders, page=page, total_pages=total_pages)
        markup = build_my_orders_keyboard(
            orders=[
                (order.public_id, f"#{order.public_id} · {order.get_status_display()}")
                for order in orders
            ],
            page=page,
            total_pages=total_pages,
        )
    else:
        order = await sync_to_async(_load_my_order)(
            partner.id, callback.from_user.id, match.group(2)
        )
        if order is None:
            await callback.answer(_("Заказ не найден."), show_alert=True)
            return
        text = _render_my_order(order)
        markup = build_my_order_keyboard(
            public_id=order.public_id, can_confirm=_can_confirm_from_list(order)
        )
    updated = await _safe_profile_message_edit(callback.message, text, reply_markup=markup)
    await callback.answer() if updated else await callback.answer(_("Без изменений"))


# Bot language (PQ-040): the guest picks one of the venue's languages or
# «automatic» (the language of their Telegram when the venue offers it).
_LANGUAGE_CALLBACK = re.compile(r"^guestlang:(menu|auto|uk|ru|en)$")


@router.callback_query(F.data.regexp(_LANGUAGE_CALLBACK))
async def guest_language_callback(callback: CallbackQuery, bot: Bot) -> None:
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    choice = _LANGUAGE_CALLBACK.match(callback.data).group(1)
    telegram_id = callback.from_user.id
    languages = enabled_languages(await sync_to_async(partner_bot_settings)(partner))
    if choice == "menu":
        chosen = await sync_to_async(guest_language_choice)(partner.id, telegram_id)
        await _safe_profile_message_edit(
            callback.message,
            _("<b>Язык бота</b>\nНа каком языке боту с вами говорить?"),
            reply_markup=build_language_keyboard(languages=languages, chosen=chosen),
        )
        await callback.answer()
        return
    try:
        await sync_to_async(set_guest_language)(
            partner_id=partner.id,
            telegram_id=telegram_id,
            language="" if choice == "auto" else choice,
        )
    except GuestLanguageError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except GuestProfile.DoesNotExist:
        await callback.answer(_("Сначала откройте бота командой /start."), show_alert=True)
        return
    language, _overrides = await sync_to_async(language_context)(
        partner, telegram_id, callback.from_user.language_code
    )
    with translation.override(language):
        content = await sync_to_async(BotContent.for_partner)(partner)
        state = await sync_to_async(resolve_guest_navigation_state)(
            partner_id=partner.id, telegram_id=telegram_id, content=content
        )
        chosen = "" if choice == "auto" else choice
        await _safe_profile_message_edit(
            callback.message,
            _("<b>Язык бота</b>\nНа каком языке боту с вами говорить?"),
            reply_markup=build_language_keyboard(languages=languages, chosen=chosen),
        )
        await callback.message.answer(
            _("Готово, язык бота: {language}.", language=LANGUAGE_NAMES[language]),
            reply_markup=build_main_keyboard(content, navigation_state=state),
        )
        await callback.answer()
