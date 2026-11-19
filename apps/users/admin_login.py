"""Into Admin by a one-time link from the bot (PQ-045, step 2).

Owner's decisions (2026-11-19): the link comes from «🔐 Войти в Admin» in the
work mode of the venue's bot, lives 15 minutes, needs no password; the owner
and the staff the owner gave Admin (``AdminAccessProfile.can_access_admin``)
may use it. Only the SHA-256 of the link is kept. The page asks «Войти»
first (``apps/users/views.py:admin_enter``): a link preview opening the
address does not spend it. Access is checked again when the link is used.

Locks: only the ``AdminLoginLink`` row (``redeem_admin_login_link``).
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.employees.models import EmployeeProfile
from apps.partners.networks import admin_partner_ids
from apps.users.models import AdminLoginLink, User
from core.i18n import _
from core.site import site_address

LINK_LIFETIME = timedelta(minutes=15)


class AdminLoginError(Exception):
    """No way into Admin by this link or for this person; the message says why."""


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _may_open(user: User, partner_id) -> bool:
    """Admin is open to this login at this venue now: active, with Admin
    access, and the venue is theirs (or the network's, PQ-047)."""

    return bool(user.is_active) and partner_id in admin_partner_ids(user)


def admin_login_user(employee: EmployeeProfile) -> User | None:
    """The login that enters Admin for this staff profile: their own; for a
    copy of a network's owner or manager in another venue's bot — the
    person's own login, which opens that venue too (PQ-047)."""

    if not employee.is_active:
        return None
    own = employee.mirrored_from if employee.mirrored_from_id else employee
    if not own.is_active:
        return None
    user = own.user
    return user if _may_open(user, employee.partner_id) else None


def issue_admin_login_link(*, partner_id, employee: EmployeeProfile) -> str:
    """A new link for this staff member of the bot's venue; returns the raw
    token (never stored)."""

    if str(employee.partner_id) != str(partner_id):
        raise AdminLoginError(_("Вход в Admin — из бота своего заведения."))
    user = admin_login_user(employee)
    if user is None:
        raise AdminLoginError(_("Вход в Admin вам не открыт: его даёт владелец."))
    now = timezone.now()
    raw = secrets.token_urlsafe(32)
    # Old links of this person are of no use: keep the table small.
    AdminLoginLink.objects.filter(user=user, expires_at__lt=now - timedelta(days=1)).delete()
    AdminLoginLink.objects.create(user=user, partner_id=partner_id, token_hash=_hash(raw),
                                  expires_at=now + LINK_LIFETIME)
    return raw


def admin_login_url(raw: str) -> str:
    return f"{site_address()}{reverse('admin-enter', args=[raw])}"


def usable_link(raw: str) -> AdminLoginLink | None:
    """The link if it may still be used — for the page before «Войти»."""

    link = (
        AdminLoginLink.objects.select_related("user", "partner")
        .filter(token_hash=_hash(raw), used_at__isnull=True, expires_at__gt=timezone.now())
        .first()
    )
    if link is None or not _may_open(link.user, link.partner_id):
        return None
    return link


def redeem_admin_login_link(raw: str) -> AdminLoginLink:
    """Spend the link: once, within its time, while Admin is still open to
    the person. Returns the link (its user enters, at its venue)."""

    refused = AdminLoginError(_(
        "Ссылка уже использована или устарела. Нажмите «🔐 Войти в Admin» в боте ещё раз."
    ))
    with transaction.atomic():
        link = (
            AdminLoginLink.objects.select_for_update()
            .filter(token_hash=_hash(raw))
            .first()
        )
        now = timezone.now()
        if link is None or link.used_at is not None or link.expires_at <= now:
            raise refused
        if not _may_open(link.user, link.partner_id):
            raise AdminLoginError(_("Вход в Admin вам больше не открыт."))
        link.used_at = now
        link.save(update_fields=["used_at", "updated_at"])
    return link
