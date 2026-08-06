from dataclasses import dataclass
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from apps.billing.models import Bill, BillingRequest, BillOrder
from apps.bonuses.models import BonusProgram
from apps.bonuses.services import apply_bonus_programs
from apps.orders.models import Cart, Order
from apps.tables.models import Table, TableSession
from apps.users.models import GuestProfile
from apps.users.services import get_or_create_guest_profile, mark_guest_visit


class TableSessionError(Exception):
    """Raised when a table session cannot be activated or used."""


@dataclass(slots=True)
class ParsedTablePayload:
    table_number: int
    qr_token: str


def parse_table_deep_link_payload(payload: str) -> ParsedTablePayload:
    parts = payload.split("_", 2)
    if len(parts) != 3 or parts[0] != "table":
        raise TableSessionError("Некорректная ссылка стола.")

    try:
        table_number = int(parts[1])
    except ValueError as exc:
        raise TableSessionError("Некорректный номер стола в QR-ссылке.") from exc

    qr_token = parts[2].strip()
    if not qr_token:
        raise TableSessionError("В QR-ссылке отсутствует токен стола.")

    return ParsedTablePayload(table_number=table_number, qr_token=qr_token)


@transaction.atomic
def activate_table_session(
    *,
    partner_id: UUID | str,
    telegram_id: int,
    payload: str,
    username: str = "",
    first_name: str = "",
    last_name: str = "",
    language_code: str = "",
) -> TableSession:
    parsed = parse_table_deep_link_payload(payload)
    try:
        table = (
            Table.objects.select_related("partner")
            .get(
                partner_id=partner_id,
                number=parsed.table_number,
                qr_token=parsed.qr_token,
                is_active=True,
            )
        )
    except Table.DoesNotExist as exc:
        raise TableSessionError("Стол по этому QR-коду не найден или уже недоступен.") from exc
    guest_profile = get_or_create_guest_profile(
        partner_id=partner_id,
        telegram_id=telegram_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        language_code=language_code,
    )
    guest_profile = GuestProfile.objects.select_for_update().get(
        id=guest_profile.id,
        partner_id=partner_id,
    )
    try:
        table = (
            Table.objects.select_for_update()
            .select_related("partner")
            .get(
                id=table.id,
                partner_id=partner_id,
                is_active=True,
            )
        )
    except Table.DoesNotExist as exc:
        raise TableSessionError("Стол по этому QR-коду уже недоступен.") from exc
    mark_guest_visit(guest_profile)
    _close_guest_active_sessions(partner_id=partner_id, guest_profile=guest_profile)
    session = TableSession.objects.create(
        partner_id=partner_id,
        guest=guest_profile,
        table=table,
    )
    # Visit rewards are tied to QR activation so partners can reward a real
    # venue visit even before the guest makes the first order.
    apply_bonus_programs(
        partner_id=partner_id,
        event=BonusProgram.TriggerEvent.VISIT,
        guest=guest_profile,
        source_type="table_session",
        source_id=str(session.id),
        comment=f"Visit bonus for table #{table.number}.",
    )
    return session


def get_active_table_session(*, partner_id: UUID | str, telegram_id: int) -> TableSession | None:
    return (
        TableSession.objects.select_related("guest__telegram_account", "table", "partner")
        .filter(
            partner_id=partner_id,
            guest__telegram_account__telegram_id=telegram_id,
            status=TableSession.Status.ACTIVE,
        )
        .order_by("-started_at")
        .first()
    )


def _close_guest_active_sessions(*, partner_id: UUID | str, guest_profile: GuestProfile) -> None:
    active_sessions = TableSession.objects.select_for_update().filter(
        partner_id=partner_id,
        guest=guest_profile,
        status=TableSession.Status.ACTIVE,
    )
    for session in active_sessions:
        _close_locked_table_session(session)


def _close_locked_table_session(session: TableSession) -> TableSession:
    if session.status != TableSession.Status.ACTIVE:
        return session
    session.status = TableSession.Status.CLOSED
    session.closed_at = timezone.now()
    session.save(update_fields=["status", "closed_at", "updated_at"])
    return session


@transaction.atomic
def close_table_session(session: TableSession) -> TableSession:
    try:
        locked_session = TableSession.objects.select_for_update().get(
            id=session.id,
            partner_id=session.partner_id,
        )
    except TableSession.DoesNotExist as exc:
        raise TableSessionError("Сессия стола не найдена в этом заведении.") from exc
    return _close_locked_table_session(locked_session)


def can_close_table_session(session: TableSession) -> tuple[bool, str]:
    has_active_cart = Cart.objects.filter(
        partner_id=session.partner_id,
        guest_id=session.guest_id,
        table_session_id=session.id,
        status=Cart.Status.ACTIVE,
    ).exists()
    if has_active_cart:
        return False, "У гостя ещё есть активная корзина."

    has_unpaid_orders = Order.objects.filter(
        partner_id=session.partner_id,
        table_session_id=session.id,
        paid_at__isnull=True,
    ).exclude(
        status=Order.Status.CANCELED,
    ).exists()
    if has_unpaid_orders:
        return False, "У гостя есть заказы без оплаты."

    has_unreceived_orders = Order.objects.filter(
        partner_id=session.partner_id,
        table_session_id=session.id,
        received_at__isnull=True,
    ).exclude(
        status=Order.Status.CANCELED,
    ).exists()
    if has_unreceived_orders:
        return False, "У гостя есть заказы, получение которых ещё не подтверждено."

    has_open_billing_requests = BillingRequest.objects.filter(
        partner_id=session.partner_id,
        table_session_id=session.id,
        status__in=[
            BillingRequest.Status.OPEN,
            BillingRequest.Status.AUTO_PREPARED,
        ],
    ).exists()
    if has_open_billing_requests:
        return False, "У гостя есть необработанный запрос счёта."

    has_open_bills = Bill.objects.filter(
        partner_id=session.partner_id,
        status__in=[
            Bill.Status.DRAFT,
            Bill.Status.ISSUED,
            Bill.Status.PARTIALLY_PAID,
        ],
        bill_orders__order_id__in=BillOrder.objects.filter(
            partner_id=session.partner_id,
            order__table_session_id=session.id,
        ).values("order_id"),
    ).exists()
    if has_open_bills:
        return False, "По гостю есть открытый счёт или неоплаченная сумма."

    return True, ""


@transaction.atomic
def close_table_session_if_settled(session: TableSession) -> TableSession:
    try:
        locked_session = TableSession.objects.select_for_update().get(
            id=session.id,
            partner_id=session.partner_id,
        )
    except TableSession.DoesNotExist as exc:
        raise TableSessionError("Сессия стола не найдена в этом заведении.") from exc
    if locked_session.status != TableSession.Status.ACTIVE:
        return locked_session
    can_close, reason = can_close_table_session(locked_session)
    if not can_close:
        raise TableSessionError(reason)
    return _close_locked_table_session(locked_session)
