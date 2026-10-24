"""Staff refunds of a paid order in the bot (PQ-037).

Stateless: the selected lines travel in the callback as a hex bit mask over
the order's lines in its paid bill (in bill order), then the reason and how
the money went back:

    strf:<order>:<mask>              choose lines (toggle one bit per press)
    strr:<order>:<mask>              choose the reason
    strm:<order>:<mask>:<r>          choose how the money went back
    strc:<order>:<mask>:<r>:<m>      confirm, with the amounts
    strk:<order>:<mask>:<r>:<m>      do it

The service (apps/billing/services.py:refund_bill_lines) rechecks the right,
the bill and the lines under its locks, so a stale screen cannot refund twice.
"""

from __future__ import annotations

from aiogram import Bot, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from asgiref.sync import sync_to_async

from apps.billing.models import Refund
from apps.billing.services import (
    BillingServiceError,
    get_order_refund_lines,
    preview_refund,
    refund_bill_lines,
    user_can_adjust_bills,
)
from apps.orders.models import Order
from apps.orders.repositories import OrderRepository
from apps.orders.services import OrderFlowError
from bot.handlers.staff import (
    _order_actions_keyboard,
    _render_order_card,
    _resolve_staff_employee_from_callback,
    _safe_edit_message_text,
)
from bot.utils.html import escape_html
from core.i18n import _

router = Router()

PREFIXES = ("strf", "strr", "strm", "strc", "strk")
MAX_LINES = 30
REASONS = {
    "os": Refund.ReasonCode.OUT_OF_STOCK,
    "q": Refund.ReasonCode.QUALITY,
    "g": Refund.ReasonCode.GUEST_REFUSED,
    "e": Refund.ReasonCode.INPUT_ERROR,
    "o": Refund.ReasonCode.OTHER,
}
METHODS = {
    "c": Refund.Method.CASH,
    "t": Refund.Method.TRANSFER,
    "p": Refund.Method.TERMINAL,
    "n": Refund.Method.NONE,
}


def _button(text: str, data: str) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text=text, callback_data=data)]


def _selected(lines, mask: int):
    return [line for index, line in enumerate(lines) if mask >> index & 1 and not line.refunded]


def _header(order: Order) -> str:
    return _("<b>Возврат по заказу #{public_id}</b>", public_id=escape_html(order.public_id))


def _lines_screen(order: Order, lines, mask: int):
    text = [_header(order), _("Отметьте, что вернуть:")]
    rows = []
    for index, line in enumerate(lines):
        label = _(
            "{item_name} × {quantity} — {line_total} грн",
            item_name=line.item_name,
            quantity=line.quantity,
            line_total=line.line_total,
        )
        if line.refunded:
            text.append(_("↩️ {label} — уже возвращено", label=escape_html(label)))
            continue
        mark = "☑️" if mask >> index & 1 else "⬜"
        toggled = mask ^ 1 << index
        rows.append(_button(f"{mark} {label}"[:60], f"strf:{order.public_id}:{toggled:x}"))
    open_indexes = [i for i, line in enumerate(lines) if not line.refunded]
    all_mask = sum(1 << i for i in open_indexes)
    if open_indexes and mask != all_mask:
        rows.append(_button(_("Отметить всё"), f"strf:{order.public_id}:{all_mask:x}"))
    if mask:
        rows.append(_button(_("Дальше →"), f"strr:{order.public_id}:{mask:x}"))
    rows.append(_button(_("← К заказу"), f"stafforderrefresh:{order.public_id}"))
    return "\n".join(text), InlineKeyboardMarkup(inline_keyboard=rows)


def _reason_screen(order: Order, mask: int):
    rows = [
        _button(str(Refund.ReasonCode(code).label), f"strm:{order.public_id}:{mask:x}:{short}")
        for short, code in REASONS.items()
    ]
    rows.append(_button(_("← Назад"), f"strf:{order.public_id}:{mask:x}"))
    return _(
        "{header}\n"
        "Причина возврата?",
        header=_header(order),
    ), InlineKeyboardMarkup(inline_keyboard=rows)


def _method_screen(order: Order, mask: int, reason: str):
    rows = [
        _button(str(Refund.Method(code).label), f"strc:{order.public_id}:{mask:x}:{reason}:{short}")
        for short, code in METHODS.items()
    ]
    rows.append(_button(_("← Назад"), f"strr:{order.public_id}:{mask:x}"))
    return (
        _("{header}\nКак вернёте деньги гостю?", header=_header(order)),
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


def _confirm_screen(order: Order, lines, mask: int, reason: str, method: str, amounts):
    money, bonus = amounts
    chosen = _selected(lines, mask)
    text = [_header(order)]
    text += [f"• {escape_html(line.item_name)} × {line.quantity}" for line in chosen]
    how = str(Refund.Method(METHODS[method]).label).lower()
    text.append(_("Деньги гостю: <b>{money} грн</b> — {how}", money=money, how=how))
    if bonus:
        text.append(_("Бонусы вернутся: {bonus}", bonus=bonus))
    text.append(_("Причина: {label}", label=str(Refund.ReasonCode(REASONS[reason]).label)))
    text.append(_("Кешбэк за возвращённое снимется. Гость получит сообщение."))
    rows = [
        _button(_("✅ Вернуть"), f"strk:{order.public_id}:{mask:x}:{reason}:{method}"),
        _button(_("← Назад"), f"strm:{order.public_id}:{mask:x}:{reason}"),
    ]
    return "\n".join(text), InlineKeyboardMarkup(inline_keyboard=rows)


def _parse(data: str):
    parts = (data or "").split(":")
    if len(parts) < 3 or parts[0] not in PREFIXES:
        return None
    try:
        mask = int(parts[2], 16)
    except ValueError:
        return None
    reason = parts[3] if len(parts) > 3 else ""
    method = parts[4] if len(parts) > 4 else ""
    if mask < 0 or mask >= 1 << MAX_LINES:
        return None
    if reason and reason not in REASONS or method and method not in METHODS:
        return None
    return parts[0], parts[1], mask, reason, method


def _load(partner_id, public_id: str):
    order = OrderRepository.by_public_id_for_partner(partner_id, public_id)
    bill, lines = get_order_refund_lines(order)
    return order, bill, lines


@router.callback_query(lambda c: bool(c.data) and c.data.split(":", 1)[0] in PREFIXES)
async def staff_refund_callback(callback: CallbackQuery, bot: Bot) -> None:
    parsed = _parse(callback.data)
    if parsed is None:
        await callback.answer(_("Неизвестное действие."), show_alert=True)
        return
    step, public_id, mask, reason, method = parsed
    try:
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
    except OrderFlowError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    allowed = await sync_to_async(user_can_adjust_bills)(employee.user, partner.id)
    if not allowed:
        await callback.answer(
            _("Нет права на возвраты. Его выдаёт владелец заведения."), show_alert=True
        )
        return
    try:
        order, bill, lines = await sync_to_async(_load)(partner.id, public_id)
    except Order.DoesNotExist:
        await callback.answer(_("Заказ не найден."), show_alert=True)
        return
    if bill is None or all(line.refunded for line in lines):
        await callback.answer(_("По этому заказу нечего возвращать."), show_alert=True)
        return
    if len(lines) > MAX_LINES:
        await callback.answer(
            _("Строк слишком много для бота — сделайте возврат в Admin."), show_alert=True
        )
        return
    if step != "strf" and not _selected(lines, mask):
        step, mask = "strf", 0

    if step == "strf":
        text, markup = _lines_screen(order, lines, mask)
    elif step == "strr":
        text, markup = _reason_screen(order, mask)
    elif step == "strm" or not method:
        text, markup = _method_screen(order, mask, reason or "o")
    elif step == "strc":
        ids = [line.id for line in _selected(lines, mask)]
        amounts = await sync_to_async(preview_refund)(bill, ids)
        text, markup = _confirm_screen(order, lines, mask, reason, method, amounts)
    else:
        ids = [line.id for line in _selected(lines, mask)]
        try:
            refund = await sync_to_async(refund_bill_lines)(
                bill=bill,
                bill_item_ids=ids,
                actor=employee.user,
                reason_code=REASONS[reason],
                method=METHODS[method],
            )
        except (BillingServiceError, OrderFlowError) as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        order = await sync_to_async(OrderRepository.by_public_id_for_partner)(
            partner.id, public_id
        )
        summary = [
            _(
                "\n"
                "\n"
                "<b>Возврат оформлен:</b> {money_amount} грн ({method}).",
                money_amount=refund.money_amount,
                method=refund.get_method_display().lower(),
            )
        ]
        if refund.bonus_returned:
            summary.append(_(
                "Бонусов вернулось: {bonus_returned}.",
                bonus_returned=refund.bonus_returned,
            ))
        if refund.cashback_revoked:
            summary.append(_(
                "Кешбэка снято: {cashback_revoked}.",
                cashback_revoked=refund.cashback_revoked,
            ))
        if refund.cashback_not_revoked:
            summary.append(
                _(
                    "Не снято {cashback_not_revoked} кешбэка — гость уже потратил бонусы.",
                    cashback_not_revoked=refund.cashback_not_revoked,
                )
            )
        if refund.money_amount and refund.method != Refund.Method.NONE:
            summary.append(_("Передайте деньги гостю — он получил сообщение с суммой."))
        await _safe_edit_message_text(
            callback.message,
            _render_order_card(order) + "\n".join(summary),
            reply_markup=_order_actions_keyboard(order),
        )
        await callback.answer(_("Возврат оформлен."))
        return
    await _safe_edit_message_text(callback.message, text, reply_markup=markup)
    await callback.answer()
