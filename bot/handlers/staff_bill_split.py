"""Equal parts in the staff bot (PQ-009 in the bot, step 1; owner 2026-11-20).

«➗ Поровну» on the bill card asks on how many parts (2–8, «Не делить» before
a part is paid); each part is paid by cash or terminal. The rules live in
``apps/billing/bill_split.py``, as for the Mini App.

Callbacks: ``staffbillsplit:<bill>`` (the choice), ``staffbillsplit:<bill>:<n>``
(split; 0 — undo), ``staffbillpart:<bill>:<n>:<c|t>:<kopecks shown>``.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from aiogram import Bot, Router
from aiogram.types import CallbackQuery
from asgiref.sync import sync_to_async

from apps.billing.bill_split import BillSplitError, pay_split_part, split_bill_equally
from apps.billing.models import Bill, Payment
from apps.billing.repositories import BillRepository
from apps.orders.services import OrderFlowError
from bot.handlers.staff import (
    _build_bill_card_message,
    _can_manage_billing,
    _resolve_staff_employee_from_callback,
    _safe_edit_message_text,
)
from bot.keyboards.staff import build_staff_bill_split_keyboard
from core.i18n import _

router = Router(name="staff_bill_split")

METHODS = {"c": Payment.Method.CASH, "t": Payment.Method.TERMINAL}


async def _billing_staff(callback: CallbackQuery, bot: Bot):
    partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
    if not _can_manage_billing(employee):
        raise OrderFlowError(
            _("Работа со счетами доступна только владельцу, менеджеру или кассиру.")
        )
    return partner, employee


async def _show_card(callback: CallbackQuery, partner, employee, public_id: str) -> None:
    bill = await sync_to_async(BillRepository.by_public_id_for_partner)(partner.id, public_id)
    text, markup = await sync_to_async(_build_bill_card_message)(bill, employee.user)
    await _safe_edit_message_text(callback.message, text, reply_markup=markup)


@router.callback_query(lambda c: (c.data or "").startswith("staffbillsplit:"))
async def bill_split_handler(callback: CallbackQuery, bot: Bot) -> None:
    fields = (callback.data or "").split(":")
    try:
        partner, employee = await _billing_staff(callback, bot)
        public_id = fields[1]
        bill = await sync_to_async(BillRepository.by_public_id_for_partner)(partner.id, public_id)
        if len(fields) == 2:
            remaining = (bill.total_amount - bill.paid_amount).quantize(Decimal("0.01"))
            await _safe_edit_message_text(
                callback.message,
                _("<b>Счёт #{public_id}</b>\nНа сколько равных частей разделить {amount} грн?",
                  public_id=bill.public_id, amount=remaining),
                reply_markup=build_staff_bill_split_keyboard(bill_public_id=bill.public_id,
                                                             is_split=bool(bill.split_parts)),
            )
            await callback.answer()
            return
        parts = int(fields[2])
        await sync_to_async(split_bill_equally)(partner_id=partner.id, employee=employee,
                                                bill_id=bill.id, parts=parts)
        await _show_card(callback, partner, employee, public_id)
    except (Bill.DoesNotExist, IndexError, ValueError):
        await callback.answer(_("Счёт не найден."), show_alert=True)
        return
    except (OrderFlowError, BillSplitError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer(_("Счёт делят поровну на {parts}.", parts=parts) if parts
                          else _("Счёт больше не делят."))


@router.callback_query(lambda c: (c.data or "").startswith("staffbillpart:"))
async def bill_part_pay_handler(callback: CallbackQuery, bot: Bot) -> None:
    try:
        partner, employee = await _billing_staff(callback, bot)
        _prefix, public_id, number, method, cents = (callback.data or "").split(":")
        bill = await sync_to_async(BillRepository.by_public_id_for_partner)(partner.id, public_id)
        await sync_to_async(pay_split_part)(
            partner_id=partner.id, employee=employee, bill_id=bill.id, part=int(number),
            method=METHODS[method], expected_amount=Decimal(cents) / 100,
        )
    except (Bill.DoesNotExist, KeyError, ValueError, InvalidOperation):
        await callback.answer(_("Счёт не найден."), show_alert=True)
        return
    except (OrderFlowError, BillSplitError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await _show_card(callback, partner, employee, public_id)
    await callback.answer(_("Часть {number} оплачена", number=number))
