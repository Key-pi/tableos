from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from apps.billing.models import Bill, BillingRequest, BillOrder
from apps.bonuses.models import BonusProgram
from apps.bonuses.services import apply_bonus_programs
from apps.orders.models import Cart, Order
from apps.tables.models import Table, TableSession, TableVisit
from apps.users.models import GuestProfile
from apps.users.services import get_or_create_guest_profile, mark_guest_visit
from core.i18n import _


class TableSessionError(Exception):
    """Raised when a table session cannot be activated or used."""


# How a scan chooses the table visit (ADR-0007, PQ-026).
VISIT_CHOICE_AUTO = "auto"  # join the party sitting at the table, else start one
VISIT_CHOICE_ASK = "ask"  # the bot asks the guest when another party sits there
VISIT_CHOICE_NEW = "new"  # start a separate visit
VISIT_CHOICE_JOIN_PREFIX = "join:"  # join:<visit id>


@dataclass(frozen=True, slots=True)
class VisitOption:
    visit_id: str
    started_at: datetime
    guest_count: int


class VisitChoiceRequired(TableSessionError):
    """Raised in ``ask`` mode when the guest must choose join or new visit."""

    def __init__(self, *, table: Table, options: list[VisitOption]):
        super().__init__(_("Выберите: подсесть к компании за столом или начать новый визит."))
        self.table = table
        self.options = options


@dataclass(slots=True)
class _VisitPlan:
    visit: TableVisit | None = None
    create_new: bool = False
    explicit_join: bool = False
    other_active_visit_ids: list = field(default_factory=list)


@dataclass(slots=True)
class ParsedTablePayload:
    table_number: int
    qr_token: str


def parse_table_deep_link_payload(payload: str) -> ParsedTablePayload:
    parts = payload.split("_", 2)
    if len(parts) != 3 or parts[0] != "table":
        raise TableSessionError(_("Некорректная ссылка стола."))

    try:
        table_number = int(parts[1])
    except ValueError as exc:
        raise TableSessionError(_("Некорректный номер стола в QR-ссылке.")) from exc

    qr_token = parts[2].strip()
    if not qr_token:
        raise TableSessionError(_("В QR-ссылке отсутствует токен стола."))

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
    visit_choice: str = VISIT_CHOICE_AUTO,
) -> TableSession:
    """Open the guest's session at a table inside a table visit.

    ``visit_choice`` is ``auto`` (service callers), ``ask`` (the bot: raises
    ``VisitChoiceRequired`` when another party sits at the table), ``new`` or
    ``join:<visit id>``. A guest who already sits in an active visit of this
    table stays in it.
    """

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
        raise TableSessionError(_("Стол по этому QR-коду не найден или уже недоступен.")) from exc
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
        # FOR NO KEY UPDATE (AA-040): serialises activations of this table but,
        # unlike FOR UPDATE, does not block another transaction's foreign-key
        # check on the table at COMMIT (FOR KEY SHARE). A payment that holds
        # the visit and references the table would otherwise deadlock with a
        # guest joining that visit.
        table = (
            Table.objects.select_for_update(of=("self",), no_key=True)
            .select_related("partner")
            .get(
                id=table.id,
                partner_id=partner_id,
                is_active=True,
            )
        )
    except Table.DoesNotExist as exc:
        raise TableSessionError(_("Стол по этому QR-коду уже недоступен.")) from exc
    # Lock order: GuestProfile -> Table -> guest's own TableSessions -> TableVisit.
    # The Table lock serialises visit creation and joining for this table.
    plan = _plan_visit(
        partner_id=partner_id,
        table=table,
        guest_profile=guest_profile,
        visit_choice=visit_choice,
    )
    mark_guest_visit(guest_profile)
    _close_guest_active_sessions(
        partner_id=partner_id,
        guest_profile=guest_profile,
        keep_visit_id=plan.visit.id if plan.visit is not None else None,
    )
    visit = _lock_target_visit(partner_id=partner_id, table=table, plan=plan)
    session = TableSession.objects.create(
        partner_id=partner_id,
        guest=guest_profile,
        table=table,
        visit=visit,
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


def _joinable_visit_options(*, partner_id, table: Table, exclude_guest_id) -> list[VisitOption]:
    """Active visits of the table with at least one other guest sitting there."""

    options = []
    visits = TableVisit.objects.filter(
        partner_id=partner_id,
        table_id=table.id,
        status=TableVisit.Status.ACTIVE,
    ).order_by("started_at")
    for visit in visits:
        guest_count = (
            TableSession.objects.filter(
                partner_id=partner_id,
                visit_id=visit.id,
                status=TableSession.Status.ACTIVE,
            )
            .exclude(guest_id=exclude_guest_id)
            .count()
        )
        if guest_count:
            options.append(
                VisitOption(
                    visit_id=str(visit.id),
                    started_at=visit.started_at,
                    guest_count=guest_count,
                )
            )
    return options


def _plan_visit(*, partner_id, table: Table, guest_profile: GuestProfile, visit_choice: str):
    other_active_visit_ids = list(
        TableVisit.objects.filter(
            partner_id=partner_id,
            table_id=table.id,
            status=TableVisit.Status.ACTIVE,
        ).values_list("id", flat=True)
    )
    plan = _VisitPlan(other_active_visit_ids=other_active_visit_ids)

    if visit_choice.startswith(VISIT_CHOICE_JOIN_PREFIX):
        visit_id = visit_choice.removeprefix(VISIT_CHOICE_JOIN_PREFIX)
        visit = TableVisit.objects.filter(
            partner_id=partner_id,
            table_id=table.id,
            id=visit_id,
        ).first()
        if visit is None:
            raise TableSessionError(_(
                "Эта компания за столом не найдена. Отсканируйте QR ещё раз.",
            ))
        plan.visit = visit
        plan.explicit_join = True
        return plan
    if visit_choice == VISIT_CHOICE_NEW:
        plan.create_new = True
        return plan
    if visit_choice not in {VISIT_CHOICE_AUTO, VISIT_CHOICE_ASK}:
        raise TableSessionError(_("Неизвестный выбор визита."))

    member_session = (
        TableSession.objects.filter(
            partner_id=partner_id,
            guest_id=guest_profile.id,
            table_id=table.id,
            status=TableSession.Status.ACTIVE,
            visit__status=TableVisit.Status.ACTIVE,
        )
        .select_related("visit")
        .first()
    )
    if member_session is not None:
        plan.visit = member_session.visit
        return plan

    options = _joinable_visit_options(
        partner_id=partner_id,
        table=table,
        exclude_guest_id=guest_profile.id,
    )
    if not options:
        plan.create_new = True
        return plan
    if visit_choice == VISIT_CHOICE_ASK:
        raise VisitChoiceRequired(table=table, options=options)
    # auto: join the most recently started party at the table.
    plan.visit = TableVisit.objects.get(id=options[-1].visit_id)
    return plan


def _lock_target_visit(*, partner_id, table: Table, plan: _VisitPlan) -> TableVisit:
    if plan.visit is not None:
        visit = TableVisit.objects.select_for_update().get(id=plan.visit.id)
        if visit.status == TableVisit.Status.ACTIVE:
            return visit
        if plan.explicit_join:
            raise TableSessionError(
                _("Эта компания уже рассчиталась и ушла. Отсканируйте QR ещё раз.")
            )
        # auto/member target closed meanwhile: start a fresh visit instead.
    visit = TableVisit.objects.create(partner_id=partner_id, table=table)
    still_open = [
        visit_id for visit_id in plan.other_active_visit_ids
        if TableVisit.objects.filter(id=visit_id, status=TableVisit.Status.ACTIVE).exists()
    ]
    if still_open:
        # Function-local import: notifications.services imports this module.
        from apps.notifications.services import notify_staff_about_parallel_visit

        notify_staff_about_parallel_visit(visit)
    return visit


def _close_guest_active_sessions(
    *,
    partner_id: UUID | str,
    guest_profile: GuestProfile,
    keep_visit_id=None,
) -> None:
    active_sessions = TableSession.objects.select_for_update().filter(
        partner_id=partner_id,
        guest=guest_profile,
        status=TableSession.Status.ACTIVE,
    )
    for session in active_sessions:
        _close_locked_table_session(
            session,
            close_visit=session.visit_id != keep_visit_id,
        )


def visit_unsettled_reason(visit: TableVisit) -> str | None:
    """Return why the visit is not settled, or ``None`` when it is."""

    visit_orders = Order.objects.filter(
        partner_id=visit.partner_id,
        table_session__visit_id=visit.id,
    ).exclude(status=Order.Status.CANCELED)
    if visit_orders.filter(paid_at__isnull=True).exists():
        return _("В визите есть неоплаченные заказы.")
    has_open_bills = Bill.objects.filter(
        partner_id=visit.partner_id,
        visit_id=visit.id,
        status__in=[Bill.Status.DRAFT, Bill.Status.ISSUED, Bill.Status.PARTIALLY_PAID],
    ).exists()
    if has_open_bills:
        return _("По визиту есть открытый счёт.")
    return None


def _close_visit_if_finished(visit_id) -> bool:
    """Close the visit when nobody sits in it any more and it is settled."""

    visit = TableVisit.objects.select_for_update().filter(id=visit_id).first()
    if visit is None or visit.status != TableVisit.Status.ACTIVE:
        return False
    has_active_sessions = TableSession.objects.filter(
        visit_id=visit.id,
        status=TableSession.Status.ACTIVE,
    ).exists()
    if has_active_sessions or visit_unsettled_reason(visit) is not None:
        return False
    visit.status = TableVisit.Status.CLOSED
    visit.closed_at = timezone.now()
    visit.save(update_fields=["status", "closed_at", "updated_at"])
    return True


def _close_locked_table_session(session: TableSession, *, close_visit: bool = True) -> TableSession:
    if session.status != TableSession.Status.ACTIVE:
        return session
    session.status = TableSession.Status.CLOSED
    session.closed_at = timezone.now()
    session.save(update_fields=["status", "closed_at", "updated_at"])
    if close_visit and session.visit_id is not None:
        _close_visit_if_finished(session.visit_id)
    return session


@transaction.atomic
def close_table_visit(visit: TableVisit, *, actor=None) -> TableVisit:
    """Staff command: close a visit and its sessions once everything is settled."""

    try:
        locked_sessions = list(
            TableSession.objects.select_for_update()
            .filter(
                partner_id=visit.partner_id,
                visit_id=visit.id,
                status=TableSession.Status.ACTIVE,
            )
            .order_by("id")
        )
        visit = TableVisit.objects.select_for_update().get(
            id=visit.id,
            partner_id=visit.partner_id,
        )
    except TableVisit.DoesNotExist as exc:
        raise TableSessionError(_("Визит не найден в этом заведении.")) from exc
    if visit.status != TableVisit.Status.ACTIVE:
        return visit
    reason = visit_unsettled_reason(visit)
    if reason is not None:
        raise TableSessionError(reason)
    for session in locked_sessions:
        can_close, session_reason = can_close_table_session(session)
        if not can_close:
            raise TableSessionError(session_reason)
    for session in locked_sessions:
        _close_locked_table_session(session, close_visit=False)
    visit.status = TableVisit.Status.CLOSED
    visit.closed_at = timezone.now()
    visit.closed_by = actor
    visit.save(update_fields=["status", "closed_at", "closed_by", "updated_at"])
    return visit


@transaction.atomic
def close_table_session(session: TableSession) -> TableSession:
    try:
        locked_session = TableSession.objects.select_for_update().get(
            id=session.id,
            partner_id=session.partner_id,
        )
    except TableSession.DoesNotExist as exc:
        raise TableSessionError(_("Сессия стола не найдена в этом заведении.")) from exc
    return _close_locked_table_session(locked_session)


def can_close_table_session(
    session: TableSession,
    *,
    include_billing_requests: bool = True,
) -> tuple[bool, str]:
    """Return whether the session is settled and may be closed.

    ``include_billing_requests=False`` lets the billing settlement path decide
    about pending requests itself: once every order is paid they are moot.
    """
    has_active_cart = Cart.objects.filter(
        partner_id=session.partner_id,
        guest_id=session.guest_id,
        table_session_id=session.id,
        status=Cart.Status.ACTIVE,
    ).exists()
    if has_active_cart:
        return False, _("У гостя ещё есть активная корзина.")

    has_unpaid_orders = Order.objects.filter(
        partner_id=session.partner_id,
        table_session_id=session.id,
        paid_at__isnull=True,
    ).exclude(
        status=Order.Status.CANCELED,
    ).exists()
    if has_unpaid_orders:
        return False, _("У гостя есть заказы без оплаты.")

    has_unreceived_orders = Order.objects.filter(
        partner_id=session.partner_id,
        table_session_id=session.id,
        received_at__isnull=True,
    ).exclude(
        status=Order.Status.CANCELED,
    ).exists()
    if has_unreceived_orders:
        return False, _("У гостя есть заказы, получение которых ещё не подтверждено.")

    has_open_billing_requests = include_billing_requests and BillingRequest.objects.filter(
        partner_id=session.partner_id,
        table_session_id=session.id,
        status__in=[
            BillingRequest.Status.OPEN,
            BillingRequest.Status.AUTO_PREPARED,
        ],
    ).exists()
    if has_open_billing_requests:
        return False, _("У гостя есть необработанный запрос счёта.")

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
        return False, _("По гостю есть открытый счёт или неоплаченная сумма.")

    return True, ""


@transaction.atomic
def close_table_session_if_settled(session: TableSession) -> TableSession:
    try:
        locked_session = TableSession.objects.select_for_update().get(
            id=session.id,
            partner_id=session.partner_id,
        )
    except TableSession.DoesNotExist as exc:
        raise TableSessionError(_("Сессия стола не найдена в этом заведении.")) from exc
    if locked_session.status != TableSession.Status.ACTIVE:
        return locked_session
    can_close, reason = can_close_table_session(locked_session)
    if not can_close:
        raise TableSessionError(reason)
    return _close_locked_table_session(locked_session)
