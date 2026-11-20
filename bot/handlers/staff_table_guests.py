"""The company at a table in the staff bot (PQ-009 in the bot, step 2;
owner 2026-11-20).

On the table card: «Гостей за столом» with ➖ / ➕ and a guest's money in
one tap by cash or terminal — their own bill and their parts of «Общее».
The rules live in ``apps/tables/services.py:set_visit_guests_count`` and
``apps/billing/visit_settlement.py:pay_for_guest``, as for the Mini App.

Callbacks: ``stafftvcount:<visit>:<n>``,
``stafftvpay:<visit>:<guest's code>:<c|t>:<kopecks shown>``.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from aiogram import Bot, Router
from aiogram.types import CallbackQuery
from asgiref.sync import sync_to_async

from apps.billing.models import Payment
from apps.billing.visit_settlement import SettlementError, pay_for_guest
from apps.orders.services import OrderFlowError
from apps.tables.models import TableVisit
from apps.tables.services import TableSessionError, set_visit_guests_count
from apps.users.models import GuestProfile
from bot.handlers.staff import (
    _can_manage_billing,
    _can_view_tables,
    _load_staff_visit,
    _resolve_staff_employee_from_callback,
    _safe_edit_message_text,
    _table_view,
)
from core.i18n import _

router = Router(name="staff_table_guests")

METHODS = {"c": Payment.Method.CASH, "t": Payment.Method.TERMINAL}


@router.callback_query(lambda c: (c.data or "").startswith("stafftvcount:"))
async def visit_count_handler(callback: CallbackQuery, bot: Bot) -> None:
    try:
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        if not _can_view_tables(employee):
            raise OrderFlowError(_(
                "Работа со столами доступна только владельцу, менеджеру или кассиру при "
                "включённом модуле столов."
            ))
        _prefix, raw_visit, count = (callback.data or "").split(":")
        visit = await sync_to_async(_load_staff_visit)(partner_id=partner.id, raw_id=raw_visit)
        await sync_to_async(set_visit_guests_count)(partner_id=partner.id, actor=employee,
                                                    visit_id=visit.id, count=int(count))
        text, keyboard = await _table_view(bot, partner, employee, visit.table_id)
    except (TableVisit.DoesNotExist, ValueError):
        await callback.answer(_("Визит не найден."), show_alert=True)
        return
    except (OrderFlowError, TableSessionError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await _safe_edit_message_text(callback.message, text, reply_markup=keyboard)
    await callback.answer(_("Гостей за столом: {count}", count=count))


@router.callback_query(lambda c: (c.data or "").startswith("stafftvpay:"))
async def visit_pay_handler(callback: CallbackQuery, bot: Bot) -> None:
    try:
        partner, employee = await _resolve_staff_employee_from_callback(callback, bot)
        if not _can_manage_billing(employee):
            raise OrderFlowError(
                _("Работа со счетами доступна только владельцу, менеджеру или кассиру.")
            )
        _prefix, raw_visit, code, method, cents = (callback.data or "").split(":")
        visit = await sync_to_async(_load_staff_visit)(partner_id=partner.id, raw_id=raw_visit)
        guest = await sync_to_async(
            GuestProfile.objects.filter(partner_id=partner.id, customer_code=code).first
        )()
        if guest is None:
            raise SettlementError(_("Этого гостя нет в компании — обновите экран."))
        amount = Decimal(cents) / 100
        await sync_to_async(pay_for_guest)(
            partner_id=partner.id, employee=employee, visit_id=visit.id, guest_id=guest.id,
            method=METHODS[method], expected_amount=amount,
        )
        text, keyboard = await _table_view(bot, partner, employee, visit.table_id)
    except (TableVisit.DoesNotExist, KeyError, ValueError, InvalidOperation):
        await callback.answer(_("Визит не найден."), show_alert=True)
        return
    except (OrderFlowError, SettlementError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await _safe_edit_message_text(callback.message, text, reply_markup=keyboard)
    await callback.answer(_("Оплачено: {amount} грн", amount=amount.quantize(Decimal("0.01"))))
