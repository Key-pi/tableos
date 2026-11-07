"""The network's owner and managers in the bot of every venue they run
(PQ-047, owner's decision 2026-11-05).

Admin already lets the owner of a network open every venue and a manager the
venues given to them (``apps/partners/networks.py:admin_partner_ids``). The
bot finds staff per venue (``EmployeeProfile`` of that venue), so here each
such person gets a copy of their own staff profile in every other venue they
run: work mode, notifications (with «📍 venue»), the same role and settings.
A copy whose access is gone is switched off, never deleted (history stays).

``sync_network_staff`` is idempotent; ``apps/employees/signals.py`` calls it
when a venue joins or leaves a network, access to Admin or the given venues
change, or the person's own profile or user changes.
"""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.db.models import Q

from apps.employees.models import EmployeeProfile
from apps.partners.models import Partner
from apps.partners.networks import admin_partner_ids
from apps.users.models import AdminAccessProfile, User

# Settings a copy takes from the person's own profile.
COPIED_FIELDS = (
    "title",
    "bot_notifications_enabled",
    "notify_on_order_created",
    "notify_on_order_status_changed",
    "notify_on_guest_calls",
    "notify_on_billing_requests",
    "notify_channel_table",
    "notify_channel_pickup",
    "notify_channel_delivery",
    "notify_channel_shipping",
    "can_adjust_bills",
)


def _wanted(network_id) -> dict:
    """{(own profile id, venue id): own profile} for everyone who runs more
    than their own venue of this network."""

    wanted = {}
    profiles = AdminAccessProfile.objects.filter(
        partner__network_id=network_id, is_active=True, can_access_admin=True,
        user__is_active=True,
    ).select_related("user")
    for access in profiles:
        own = EmployeeProfile.objects.filter(
            user=access.user, partner_id=access.partner_id, is_active=True,
            mirrored_from__isnull=True, telegram_account__isnull=False,
        ).first()
        if own is None:
            continue  # no Telegram: nothing to work with in the bot
        for venue_id in admin_partner_ids(access.user):
            if venue_id != access.partner_id:
                wanted[(own.id, venue_id)] = own
    return wanted


def _switch_off(copy: EmployeeProfile) -> None:
    if copy.is_active:
        copy.is_active = False
        copy.save(update_fields=["is_active", "updated_at"])
    if copy.user.is_active:
        copy.user.is_active = False
        copy.user.save(update_fields=["is_active"])


def _bring_in_step(copy: EmployeeProfile, own: EmployeeProfile) -> None:
    changed = [field for field in COPIED_FIELDS if getattr(copy, field) != getattr(own, field)]
    for field in changed:
        setattr(copy, field, getattr(own, field))
    if not copy.is_active or copy.mirrored_from_id != own.id:
        copy.is_active = True
        copy.mirrored_from = own
        changed += ["is_active", "mirrored_from"]
    if changed:
        copy.save(update_fields=[*changed, "updated_at"])
    user = copy.user
    user_changed = []
    if user.role != own.user.role:
        user.role = own.user.role
        user_changed.append("role")
    if not user.is_active:
        user.is_active = True
        user_changed.append("is_active")
    if user_changed:
        user.save(update_fields=user_changed)


def _follow_telegram(copy: EmployeeProfile, own: EmployeeProfile) -> bool:
    """The copy takes the person's current Telegram (they may have moved to
    another account in their own profile). False — a real staff profile in
    that venue already has it: theirs is not touched, the copy is not needed."""

    if copy.telegram_account_id == own.telegram_account_id:
        return True
    taken = EmployeeProfile.objects.filter(
        partner_id=copy.partner_id, telegram_account_id=own.telegram_account_id
    ).exclude(pk=copy.pk)
    if taken.exists():
        return False
    copy.telegram_account_id = own.telegram_account_id
    copy.save(update_fields=["telegram_account", "updated_at"])
    return True


def _make_copy(own: EmployeeProfile, venue_id) -> None:
    account = own.telegram_account
    with transaction.atomic():
        user = User(
            username=f"tg{account.telegram_id}-{venue_id.hex[:8]}-net",
            partner_id=venue_id,
            role=own.user.role,
            first_name=own.user.first_name,
            last_name=own.user.last_name,
            language=own.user.language,
        )
        user.set_unusable_password()  # Admin stays with the person's own login
        user.save()
        EmployeeProfile.objects.create(
            partner_id=venue_id,
            user=user,
            telegram_account=account,
            mirrored_from=own,
            **{field: getattr(own, field) for field in COPIED_FIELDS},
        )


def sync_network_staff(network_id) -> None:
    if network_id is None:
        return
    wanted = _wanted(network_id)
    # Copies in the network's venues, and copies of its people elsewhere
    # (a venue that left the network).
    copies = EmployeeProfile.objects.filter(mirrored_from__isnull=False).filter(
        Q(partner__network_id=network_id) | Q(mirrored_from__partner__network_id=network_id)
    )
    for copy in copies.select_related("user", "mirrored_from"):
        own = wanted.pop((copy.mirrored_from_id, copy.partner_id), None)
        if own is None or not _follow_telegram(copy, own):
            _switch_off(copy)
        else:
            _bring_in_step(copy, own)
    for (_own_id, venue_id), own in wanted.items():
        existing = (
            EmployeeProfile.objects.filter(
                partner_id=venue_id, telegram_account=own.telegram_account
            )
            .select_related("user")
            .first()
        )
        if existing is not None:
            if existing.mirrored_from_id is not None:
                _bring_in_step(existing, own)
            continue  # a real staff profile there: theirs, not touched
        try:
            _make_copy(own, venue_id)
        except IntegrityError:
            pass  # made by a parallel sync


def networks_of_user(user) -> set:
    return set(
        Partner.objects.filter(
            id__in=AdminAccessProfile.objects.filter(user=user).values("partner_id"),
            network__isnull=False,
        ).values_list("network_id", flat=True)
    )
