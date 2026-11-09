"""What the start page of Admin shows one venue (admin step 2, PQ-050).

Read-only. First what may lose money — transfers nobody confirmed, guests
waiting for their bill, money on receipt not recorded, new orders not taken,
parcels not sent — then stock and the bot; and what the venue has not set up
yet. Who sees which part, the texts and the links are Admin's
(``apps/users/admin_dashboard.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.db.models import Count, Min

from apps.billing.models import BillingRequest
from apps.billing.remote_orders import (
    ON_RECEIPT,
    PAYMENT_OPTION_FIELDS,
    PREPAID,
    REMOTE_CHANNELS,
)
from apps.employees.models import EmployeeProfile
from apps.menu import stock
from apps.menu.models import MenuItem
from apps.menu.options import options_on
from apps.orders.models import Order
from apps.partners.models import BotInstance, OpeningHours, Partner, PartnerBotSettings
from apps.partners.networks import bot_for_partner
from apps.tables.models import Table


@dataclass(frozen=True, slots=True)
class Waiting:
    """How many wait, and since when the oldest one does."""

    count: int = 0
    since: datetime | None = None

    def __bool__(self) -> bool:
        return self.count > 0


def _waiting(queryset) -> Waiting:
    row = queryset.aggregate(count=Count("id"), since=Min("created_at"))
    return Waiting(row["count"], row["since"])


def _orders(partner_id):
    return Order.objects.filter(partner_id=partner_id)


def transfers_to_confirm(partner_id) -> Waiting:
    """Remote orders paid in advance by transfer that nobody confirmed yet
    (``remote_orders.awaits_transfer_check``)."""

    return _waiting(_orders(partner_id).filter(
        channel__in=Order.REMOTE_CHANNELS,
        payment_timing=PREPAID,
        paid_at__isnull=True,
        status=Order.Status.NEW,
    ))


def transfer_wait_minutes(partner_id) -> int:
    """After how many minutes an unconfirmed transfer is canceled (0: never,
    PQ-036)."""

    return (
        PartnerBotSettings.objects.filter(partner_id=partner_id)
        .values_list("transfer_wait_minutes", flat=True)
        .first()
    ) or 0


BILL_REQUEST_OPEN = (BillingRequest.Status.OPEN, BillingRequest.Status.AUTO_PREPARED)


def guests_waiting_for_bill(partner_id) -> Waiting:
    """Guests at tables who asked for their bill."""

    return _waiting(BillingRequest.objects.filter(
        partner_id=partner_id, status__in=BILL_REQUEST_OPEN
    ))


RECEIPT_DUE = (Order.Status.DELIVERING, Order.Status.COMPLETED)


def receipts_to_record(partner_id) -> Waiting:
    """Orders paid on receipt that the guest has or is getting (with the
    courier, at the post, received) while nobody recorded the money."""

    return _waiting(_orders(partner_id).filter(
        payment_timing=ON_RECEIPT, paid_at__isnull=True, status__in=RECEIPT_DUE
    ))


def new_orders(partner_id) -> Waiting:
    """New orders nobody took into work (those waiting for a transfer check
    are counted by ``transfers_to_confirm``)."""

    return _waiting(
        _orders(partner_id)
        .filter(status=Order.Status.NEW)
        .exclude(payment_timing=PREPAID, paid_at__isnull=True)
    )


PARCEL_NOT_SENT = (Order.Status.ACCEPTED, Order.Status.PREPARING, Order.Status.READY)


def parcels_to_send(partner_id) -> Waiting:
    """Orders by post taken into work and not sent yet (no ТТН)."""

    return _waiting(_orders(partner_id).filter(
        channel=Order.Channel.SHIPPING, status__in=PARCEL_NOT_SENT
    ))


@dataclass(frozen=True, slots=True)
class StockProblems:
    out: int = 0
    below_zero: int = 0
    low: int = 0

    def __bool__(self) -> bool:
        return bool(self.out or self.below_zero or self.low)


def stock_problems(partner_id) -> StockProblems:
    """Counted items that are out, below zero or low — while stock is on."""

    if not stock.stock_on(partner_id):
        return StockProblems()
    items = MenuItem.objects.filter(partner_id=partner_id, is_available=True)
    variants = options_on(partner_id)
    return StockProblems(**{
        name: stock.items_in_stock_state(items, state, variants=variants).count()
        for name, state in (
            ("out", stock.OUT), ("below_zero", stock.BELOW_ZERO), ("low", stock.LOW)
        )
    })


WORKING_BOT = (BotInstance.RuntimeState.RUNNING, BotInstance.RuntimeState.RUNNING_SUSPENDED)


def broken_bot(partner_id) -> BotInstance | None:
    """The venue's bot (its own or its network's) when it is not answering
    guests: switched off or not started by the bot service (PQ-041)."""

    bot = bot_for_partner(partner_id, active_only=False)
    if bot is None or (bot.is_active and bot.runtime_state in WORKING_BOT):
        return None
    return bot


# What a new venue still has to set up, in the order to do it.
SETUP_BOT = "bot"
SETUP_MENU = "menu"
SETUP_PAYMENT_DETAILS = "payment_details"
SETUP_PAYMENT_OPTIONS = "payment_options"
SETUP_HOURS = "hours"
SETUP_TABLES = "tables"
SETUP_STAFF = "staff"
SETUP_CARD = "card"


def setup_steps(partner_id) -> list[str]:
    """Steps the venue has not done yet; empty when it is set up."""

    partner = Partner.objects.get(id=partner_id)
    settings = PartnerBotSettings.objects.filter(partner_id=partner_id).first()
    steps = []
    if bot_for_partner(partner_id, active_only=False) is None:
        steps.append(SETUP_BOT)
    if settings is not None:
        if settings.module_menu_enabled and not MenuItem.objects.filter(
            partner_id=partner_id
        ).exists():
            steps.append(SETUP_MENU)
        remote = [channel for channel, field in REMOTE_CHANNELS.items()
                  if getattr(settings, field)]
        options = [PAYMENT_OPTION_FIELDS[channel] for channel in remote]
        if any(getattr(settings, fields[PREPAID]) for fields in options) and not (
            settings.transfer_payment_details.strip()
        ):
            steps.append(SETUP_PAYMENT_DETAILS)
        if any(
            not getattr(settings, fields[PREPAID]) and not getattr(settings, fields[ON_RECEIPT])
            for fields in options
        ):
            steps.append(SETUP_PAYMENT_OPTIONS)
        hours_matter = settings.module_pickup_enabled or settings.module_delivery_enabled
        if hours_matter and not OpeningHours.objects.filter(partner_id=partner_id).exists():
            steps.append(SETUP_HOURS)
        if settings.module_tables_enabled and not Table.objects.filter(
            partner_id=partner_id
        ).exists():
            steps.append(SETUP_TABLES)
    # The network's owner and managers in a point's bot (PQ-047) are staff too.
    if not EmployeeProfile.objects.filter(partner_id=partner_id, is_active=True).exists():
        steps.append(SETUP_STAFF)
    if not partner.address.strip() or not partner.contact_phone.strip():
        steps.append(SETUP_CARD)
    return steps
