"""The language and the owner's texts for one bot update (PQ-040)."""

from __future__ import annotations

from apps.employees.models import EmployeeProfile
from apps.partners.languages import choose_language, text_overrides
from apps.partners.models import Partner, PartnerBotSettings
from apps.users.models import GuestProfile


def partner_bot_settings(partner: Partner) -> PartnerBotSettings | None:
    try:
        return partner.bot_settings
    except PartnerBotSettings.DoesNotExist:
        return None


def guest_language_choice(partner_id, telegram_id: int) -> str:
    return (
        GuestProfile.objects.filter(
            partner_id=partner_id, telegram_account__telegram_id=telegram_id
        )
        .values_list("language", flat=True)
        .first()
        or ""
    )


def staff_language_choice(partner_id, telegram_id: int) -> str:
    return (
        EmployeeProfile.objects.filter(
            partner_id=partner_id,
            telegram_account__telegram_id=telegram_id,
            is_active=True,
            user__is_active=True,
            user__partner_id=partner_id,
        )
        .values_list("user__language", flat=True)
        .first()
        or ""
    )


def language_context(
    partner: Partner,
    telegram_id: int | None,
    telegram_language: str | None,
    *,
    staff_choice: str | None = None,
) -> tuple[str, dict[tuple[str, str], str]]:
    """The language to answer this person in and the venue's own texts.

    ``staff_choice``: the language of this venue's staff profile when the
    caller has already loaded it (``staff_language_choice`` otherwise)."""

    guest_choice = ""
    if telegram_id is not None:
        guest_choice = guest_language_choice(partner.id, telegram_id)
        if staff_choice is None:
            staff_choice = staff_language_choice(partner.id, telegram_id)
    staff_choice = staff_choice or ""
    language = choose_language(
        partner_bot_settings(partner),
        guest_choice=guest_choice,
        staff_choice=staff_choice,
        telegram=telegram_language or "",
    )
    return language, text_overrides(partner.id)
