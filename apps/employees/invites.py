"""Staff invite links (PQ-046, owner's decision 2026-11-04).

The owner or a manager issues a one-time link ``t.me/<bot>?start=inv_<code>``
for a role (never an owner, PQ-035). It works for 24 hours. The person who
presses Start waits; the issuer (or the owner) confirms or rejects. Only
then is the person staff in the bot; Admin access stays the owner's call.

Locks: ``StaffInvite`` (the only row two people can race for), then the
person's former ``EmployeeProfile`` when it comes back. Adapters (bot,
Admin) only call these functions.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.employees.models import EmployeeProfile, StaffInvite
from apps.users.models import AdminAccessProfile, TelegramAccount, User
from core.i18n import _

INVITE_PREFIX = "inv_"
INVITE_LIFETIME = timedelta(hours=24)
# Every staff role; an owner is never invited (PQ-035).
INVITABLE_ROLES = [
    User.Role.MANAGER,
    User.Role.CASHIER,
    User.Role.WAITER,
    User.Role.HOOKAH_MASTER,
]


class StaffInviteError(Exception):
    """A refusal the person can read."""


@dataclass(frozen=True)
class ClaimResult:
    invite: StaffInvite
    # Where to send "confirm / reject"; None when the issuer has no Telegram.
    issuer_telegram_id: int | None
    already_claimed: bool


def _is_owner(user: User, partner_id) -> bool:
    return user.role == User.Role.OWNER or AdminAccessProfile.objects.filter(
        user=user, partner_id=partner_id, is_partner_owner=True
    ).exists()


def _active_in_partner(user: User | None, partner_id) -> bool:
    if user is None or not user.is_active or user.partner_id != partner_id:
        return False
    # Staff without a profile (Admin only) may still be the owner.
    profile = EmployeeProfile.objects.filter(user=user, partner_id=partner_id).first()
    return profile is None or profile.is_active


def roles_invitable_by(user: User | None, partner_id) -> list[str]:
    """The roles this person may invite now; empty means "may not invite"."""

    if not _active_in_partner(user, partner_id):
        return []
    if _is_owner(user, partner_id) or user.role == User.Role.MANAGER:
        return list(INVITABLE_ROLES)
    return []


def create_staff_invite(*, partner_id, actor: User, role: str) -> StaffInvite:
    if role not in roles_invitable_by(actor, partner_id):
        raise StaffInviteError(_("Нет права приглашать сотрудника с этой ролью."))
    return StaffInvite.objects.create(
        partner_id=partner_id,
        code=secrets.token_urlsafe(16),
        role=role,
        created_by=actor,
        expires_at=timezone.now() + INVITE_LIFETIME,
    )


def invite_link(code: str, bot_username: str) -> str:
    return f"https://t.me/{bot_username}?start={INVITE_PREFIX}{code}"


def parse_invite_payload(payload: str) -> str | None:
    if not payload.startswith(INVITE_PREFIX):
        return None
    return payload[len(INVITE_PREFIX):] or None


def _locked_invite(**lookup) -> StaffInvite:
    try:
        return StaffInvite.objects.select_for_update().get(**lookup)
    except StaffInvite.DoesNotExist as exc:
        raise StaffInviteError(_("Ссылка-приглашение не найдена.")) from exc


def _issuer_telegram_id(invite: StaffInvite) -> int | None:
    profile = (
        EmployeeProfile.objects.filter(user_id=invite.created_by_id, partner_id=invite.partner_id)
        .select_related("telegram_account")
        .first()
    )
    if profile is None or profile.telegram_account is None:
        return None
    return profile.telegram_account.telegram_id


def claim_staff_invite(*, partner_id, code: str, telegram_id: int) -> ClaimResult:
    """The person pressed the link: they now wait for the issuer."""

    with transaction.atomic():
        invite = _locked_invite(partner_id=partner_id, code=code)
        account, _created = TelegramAccount.objects.get_or_create(telegram_id=telegram_id)
        if invite.status in (StaffInvite.Status.REVOKED, StaffInvite.Status.REJECTED):
            raise StaffInviteError(_("Это приглашение отменено. Попросите новую ссылку."))
        if invite.status in (StaffInvite.Status.CLAIMED, StaffInvite.Status.ACCEPTED):
            if invite.claimed_by_id != account.id:
                raise StaffInviteError(_("Эта ссылка уже использована. Попросите новую."))
            if invite.status == StaffInvite.Status.ACCEPTED:
                raise StaffInviteError(_("Вы уже сотрудник этого заведения."))
            return ClaimResult(invite, _issuer_telegram_id(invite), already_claimed=True)
        if invite.expires_at <= timezone.now():
            raise StaffInviteError(_("Ссылка устарела. Попросите новую."))
        if EmployeeProfile.objects.filter(
            partner_id=partner_id, telegram_account=account, is_active=True
        ).exists():
            # The link stays for the person it was meant for.
            raise StaffInviteError(_("Вы уже сотрудник этого заведения."))
        invite.status = StaffInvite.Status.CLAIMED
        invite.claimed_by = account
        invite.claimed_at = timezone.now()
        invite.save(update_fields=["status", "claimed_by", "claimed_at", "updated_at"])
    return ClaimResult(invite, _issuer_telegram_id(invite), already_claimed=False)


def _may_decide(actor: User, invite: StaffInvite) -> bool:
    if invite.role not in roles_invitable_by(actor, invite.partner_id):
        return False
    return actor.id == invite.created_by_id or _is_owner(actor, invite.partner_id)


def decide_staff_invite(*, partner_id, invite_id, actor: User, accept: bool) -> StaffInvite:
    """The issuer (or the owner) confirms or rejects the person who pressed
    the link; the right is checked now, not when the link was issued."""

    with transaction.atomic():
        invite = _locked_invite(partner_id=partner_id, id=invite_id)
        if invite.status in (StaffInvite.Status.ACCEPTED, StaffInvite.Status.REJECTED):
            raise StaffInviteError(_("По этому приглашению уже решено."))
        if invite.status != StaffInvite.Status.CLAIMED:
            raise StaffInviteError(_("По этой ссылке ещё никто не пришёл."))
        if not _may_decide(actor, invite):
            raise StaffInviteError(_("Подтвердить может тот, кто выдал ссылку, или владелец."))
        if accept:
            invite.employee = _add_employee(invite)
            invite.status = StaffInvite.Status.ACCEPTED
        else:
            invite.status = StaffInvite.Status.REJECTED
        invite.decided_by = actor
        invite.decided_at = timezone.now()
        invite.save(
            update_fields=["status", "employee", "decided_by", "decided_at", "updated_at"]
        )
    return invite


def _add_employee(invite: StaffInvite) -> EmployeeProfile:
    account = invite.claimed_by
    former = (
        EmployeeProfile.objects.select_for_update()
        .filter(partner_id=invite.partner_id, telegram_account=account)
        .first()
    )
    if former is not None:
        user = former.user
        if former.is_active and user.is_active:
            raise StaffInviteError(_("Вы уже сотрудник этого заведения."))
        if user.role == User.Role.OWNER:
            raise StaffInviteError(_("Это бывший владелец: восстановите его в Admin."))
        user.role = invite.role
        user.is_active = True
        user.save(update_fields=["role", "is_active"])
        former.is_active = True
        former.save(update_fields=["is_active", "updated_at"])
        return former
    user = User(
        username=f"tg{account.telegram_id}-{invite.partner_id.hex[:8]}",
        partner_id=invite.partner_id,
        role=invite.role,
        first_name=account.first_name,
        last_name=account.last_name,
    )
    # The bot is enough to work; a password and Admin come from the owner.
    user.set_unusable_password()
    user.save()
    return EmployeeProfile.objects.create(
        partner_id=invite.partner_id, user=user, telegram_account=account
    )


def revoke_staff_invite(*, partner_id, invite_id, actor: User) -> StaffInvite:
    with transaction.atomic():
        invite = _locked_invite(partner_id=partner_id, id=invite_id)
        if not _may_decide(actor, invite):
            raise StaffInviteError(_("Отменить может тот, кто выдал ссылку, или владелец."))
        if invite.status in (StaffInvite.Status.PENDING, StaffInvite.Status.CLAIMED):
            invite.status = StaffInvite.Status.REVOKED
            invite.decided_by = actor
            invite.decided_at = timezone.now()
            invite.save(update_fields=["status", "decided_by", "decided_at", "updated_at"])
    return invite
