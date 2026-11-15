"""A sale at the counter: an order channel (PQ-054, owner 2026-11-15).

A shop with one seller, or a venue where a guest walks up to the bar for
tobacco and leaves: the seller rings the sale up. One action makes an order
«На кассе», its bill and the payment by the chosen method, so the sale is in
the orders, bills and payments like any other, is refunded the usual way and
counts in the day report by method (ADR-0009). The order is handed over and
paid at once and never goes to the kitchen and bar screens.

The guest is named by their customer code (bonuses: part of the price, and
cashback by the «Покупка на кассе» programmes,
``apps/bonuses/selectors.py:purchase_event_for``), or is the sale's own
anonymous guest (PQ-052, PQ-055).

Lock order, as a remote order (``remote_orders.py:place_remote_order``): the
named ``GuestProfile`` → the new order and its stock rows → (with bonuses, in
a network with shared bonuses) ``NetworkWallet`` → ``Bill`` → ``Order`` →
``GuestProfile`` (already held). A repeated ``request_key``
returns the sale already made; two at once — the second waits on the unique
key, its work is rolled back and it gets the first sale.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Sum

from apps.billing.models import Bill, Payment
from apps.billing.services import (
    BillingServiceError,
    create_bill_from_orders,
    issue_bill,
    pay_bill_with_bonuses,
    record_payment,
)
from apps.bonuses.models import BonusTransaction
from apps.bonuses.services import (
    BonusServiceError,
    assert_bonus_balance_reconciled,
    get_redeemable_bonus_amount,
    preview_bonus_amount,
)
from apps.employees.models import EmployeeProfile
from apps.orders.models import Order
from apps.orders.services import OrderFlowError, create_order, resolve_staff_order_lines
from apps.partners.models import PartnerBotSettings
from apps.users.models import GuestProfile
from core.i18n import _

# Who may sell at the counter: as the quick sale (bot/services/staff_rights.py).
COUNTER_ROLES = frozenset({"owner", "manager", "cashier"})
COUNTER_PAYMENT_METHODS = (Payment.Method.CASH, Payment.Method.TERMINAL)
ZERO = Decimal("0.00")


class CounterSaleError(BillingServiceError):
    pass


@dataclass(frozen=True)
class CounterPreview:
    guest: GuestProfile | None
    lines: list[dict]
    total: Decimal
    redeemable: Decimal
    to_pay: Decimal
    bonus_awarded: Decimal


@dataclass(frozen=True)
class CounterSale:
    order: Order
    bill: Bill
    repeated: bool
    bonus_awarded: Decimal


def counter_sales_on(partner_id) -> bool:
    """The «Быстрые продажи» module, with the menu to sell from."""

    return PartnerBotSettings.objects.filter(
        partner_id=partner_id, module_quick_sale_enabled=True, module_menu_enabled=True,
    ).exists()


def may_sell_at_counter(employee, partner_id) -> bool:
    return bool(
        employee is not None and employee.partner_id == partner_id
        and employee.is_active and employee.user.is_active
        and employee.user.role in COUNTER_ROLES
    )


def _money(value) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"))


def _lines(partner_id, items: list[dict]) -> list[dict]:
    try:
        return resolve_staff_order_lines(
            partner_id=partner_id, items=items, channel=Order.Channel.COUNTER
        )
    except OrderFlowError as exc:
        raise CounterSaleError(str(exc)) from exc


def _guest(partner_id, customer_code: str) -> GuestProfile | None:
    from apps.users.services import find_guest_by_customer_code

    code = (customer_code or "").strip()
    if not code:
        return None
    try:
        return find_guest_by_customer_code(partner_id, code)
    except GuestProfile.DoesNotExist as exc:
        raise CounterSaleError(_("Гость с кодом {code} не найден.", code=code.upper())) from exc


def preview_counter_sale(
    *, partner_id, customer_code: str, items: list[dict], use_bonuses: bool = False
) -> CounterPreview:
    """What the seller sees before «Провести»: the guest, the lines, the
    total, the bonuses that can pay part of it and what the guest earns."""

    guest = _guest(partner_id, customer_code)
    lines = _lines(partner_id, items) if items else []
    total = _money(sum((line["unit_price"] * line["quantity"] for line in lines), ZERO))
    redeemable = ZERO
    awarded = ZERO
    if guest is not None and total > 0:
        redeemable = get_redeemable_bonus_amount(
            partner_id=partner_id, guest=guest, purchase_total=total
        )
        awarded = preview_bonus_amount(
            partner_id=partner_id, guest=guest, purchase_total=total,
            bonus_paid_amount=redeemable if use_bonuses else ZERO,
        )
    spend = redeemable if use_bonuses else ZERO
    return CounterPreview(
        guest=guest, lines=lines, total=total, redeemable=redeemable,
        to_pay=_money(total - spend), bonus_awarded=awarded,
    )


def _seller(partner_id, seller) -> EmployeeProfile:
    # The current row, not the one the screen loaded: rights are checked now.
    employee = (
        EmployeeProfile.objects.select_related("user")
        .filter(id=getattr(seller, "id", None), partner_id=partner_id).first()
    )
    if not may_sell_at_counter(employee, partner_id):
        raise CounterSaleError(_(
            "Продажу на кассе проводит владелец, менеджер или кассир этого заведения."
        ))
    return employee


def _sale(order: Order, *, repeated: bool) -> CounterSale:
    bill = Bill.objects.filter(partner_id=order.partner_id, bill_orders__order=order).get()
    awarded = BonusTransaction.objects.filter(
        partner_id=order.partner_id, order=order,
        transaction_type=BonusTransaction.TransactionType.ACCRUAL,
    ).aggregate(total=Sum("amount"))["total"]
    return CounterSale(order=order, bill=bill, repeated=repeated,
                       bonus_awarded=_money(awarded or ZERO))


def _saved(partner_id, request_key: str) -> Order | None:
    if not request_key:
        return None
    return Order.objects.filter(
        partner_id=partner_id, request_key=request_key, channel=Order.Channel.COUNTER
    ).first()


def sell_at_counter(
    *,
    partner_id,
    seller,
    customer_code: str,
    items: list[dict],
    payment: str,
    use_bonuses: bool = False,
    expected_to_pay: Decimal | str | None = None,
    comment: str = "",
    request_key: str = "",
) -> CounterSale:
    """Ring a sale up: an order «На кассе», its bill and the payment.

    ``payment`` — cash or card (terminal), for what is left after bonuses;
    not needed when bonuses pay it all. ``expected_to_pay`` — the amount the
    seller saw; if it changed (a price, the guest's balance), nothing is
    saved and the seller checks again.
    """

    saved = _saved(partner_id, request_key)
    if saved is not None:
        return _sale(saved, repeated=True)
    seller = _seller(partner_id, seller)
    if not counter_sales_on(partner_id):
        raise CounterSaleError(_("Быстрые продажи в этом заведении выключены."))
    if payment and payment not in COUNTER_PAYMENT_METHODS:
        raise CounterSaleError(_("Выберите, чем платит гость: наличными или картой."))
    lines = _lines(partner_id, items)
    named = _guest(partner_id, customer_code)
    try:
        with transaction.atomic():
            order = _ring_up(
                partner_id=partner_id, seller=seller, named=named, lines=lines,
                payment=payment, use_bonuses=use_bonuses, expected_to_pay=expected_to_pay,
                comment=comment, request_key=request_key,
            )
    except IntegrityError:
        saved = _saved(partner_id, request_key)
        if saved is None:
            raise
        return _sale(saved, repeated=True)
    return _sale(order, repeated=False)


def _ring_up(
    *, partner_id, seller, named, lines, payment, use_bonuses, expected_to_pay, comment,
    request_key,
) -> Order:
    from apps.users.services import create_anonymous_guest

    if named is not None:
        guest = GuestProfile.objects.select_for_update().get(id=named.id, partner_id=partner_id)
    else:
        guest = create_anonymous_guest(partner_id=partner_id)
    try:
        order = create_order(
            partner_id=partner_id, guest=guest, items=lines,
            comment=(comment or "").strip()[:500], channel=Order.Channel.COUNTER,
            notify_staff=False, served_by=seller,
        )
    except OrderFlowError as exc:
        raise CounterSaleError(str(exc)) from exc
    if request_key:
        order.request_key = request_key
        order.save(update_fields=["request_key", "updated_at"])
    bill = create_bill_from_orders(
        partner_id=partner_id, order_ids=[str(order.id)], kind=Bill.Kind.PERSONAL,
        label=f"{_('На кассе')} #{order.public_id}",
    )
    bill = issue_bill(bill)
    if use_bonuses and guest.telegram_account_id is not None:
        # The guest's row is held; in a network with shared bonuses this locks
        # the shared balance too (GuestProfile → NetworkWallet), so what can
        # be spent is read under the lock. Another venue spent it a moment
        # ago — nothing left: the sale goes on without bonuses.
        try:
            assert_bonus_balance_reconciled(guest)
        except BonusServiceError as exc:
            raise CounterSaleError(str(exc)) from exc
        if get_redeemable_bonus_amount(
            partner_id=partner_id, guest=guest, purchase_total=bill.total_amount
        ) > 0:
            pay_bill_with_bonuses(bill=bill, created_by=seller.user)
    bill.refresh_from_db()
    if bill.bonus_spent_amount:
        Order.objects.filter(id=order.id).update(bonus_spent=bill.bonus_spent_amount)
    to_pay = ZERO if bill.status == Bill.Status.PAID else _money(
        bill.total_amount - bill.paid_amount
    )
    if expected_to_pay is not None and to_pay != _money(expected_to_pay):
        raise CounterSaleError(_("Сумма к оплате изменилась — проверьте её ещё раз."))
    if to_pay > 0:
        if payment not in COUNTER_PAYMENT_METHODS:
            raise CounterSaleError(_("Выберите, чем платит гость: наличными или картой."))
        record_payment(bill=bill, amount=to_pay, method=payment, created_by=seller.user)
    return Order.objects.get(id=order.id)
