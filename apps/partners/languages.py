"""Which language the bot speaks to a person (PQ-040).

Order: the language the guest chose in the bot (if the venue still offers
it), the staff member's own language, the language of their Telegram (if
the venue offers it), the venue's main language.
"""

from __future__ import annotations

from django.core.cache import cache

from apps.partners.models import GUEST_LANGUAGE_FIELDS, BotText, PartnerBotSettings
from core.i18n import LANGUAGES, default_language, normalize_language, texts


def enabled_languages(settings: PartnerBotSettings | None) -> tuple[str, ...]:
    """Languages the venue offers guests, the main one first."""

    main = main_language(settings)
    others = [
        code
        for code in LANGUAGES
        if code != main
        and (settings is None or getattr(settings, GUEST_LANGUAGE_FIELDS[code]))
    ]
    return (main, *others)


def main_language(settings: PartnerBotSettings | None) -> str:
    if settings is None:
        return default_language()
    return normalize_language(settings.default_language) or default_language()


def choose_language(
    settings: PartnerBotSettings | None,
    *,
    guest_choice: str = "",
    staff_choice: str = "",
    telegram: str = "",
) -> str:
    enabled = enabled_languages(settings)
    guest_choice = normalize_language(guest_choice)
    if guest_choice in enabled:
        return guest_choice
    staff_choice = normalize_language(staff_choice)
    if staff_choice:
        return staff_choice
    telegram = normalize_language(telegram)
    if telegram in enabled:
        return telegram
    return enabled[0]


# The bot reads the owner's texts on every update (AA-047). Saving or deleting
# a text drops the entry (apps/partners/signals.py); the lifetime only bounds
# writes that skip signals, such as data migrations.
TEXT_OVERRIDES_CACHE_SECONDS = 300


def _text_overrides_key(partner_id) -> str:
    return f"partners:text_overrides:v1:{partner_id}"


def forget_text_overrides(partner_id) -> None:
    cache.delete(_text_overrides_key(partner_id))


def text_overrides(partner_id) -> dict[tuple[str, str], str]:
    """The owner's own texts, keyed by (language, catalog text)."""

    key = _text_overrides_key(partner_id)
    cached = cache.get(key)
    if cached is not None:
        return cached
    overrides = {
        (language, msgid): text
        for language, msgid, text in BotText.objects.filter(partner_id=partner_id)
        .exclude(text="")
        .values_list("language", "msgid", "text")
    }
    cache.set(key, overrides, TEXT_OVERRIDES_CACHE_SECONDS)
    return overrides


def _settings_for(partner_id) -> PartnerBotSettings | None:
    return PartnerBotSettings.objects.filter(partner_id=partner_id).first()


def _telegram_language(account) -> str:
    return getattr(account, "language_code", "") if account is not None else ""


def guest_language(guest) -> str:
    """The language the venue's bot speaks to this guest outside an update."""

    return choose_language(
        _settings_for(guest.partner_id),
        guest_choice=guest.language,
        telegram=_telegram_language(guest.telegram_account),
    )


def employee_language(employee) -> str:
    """The language of a staff member's bot messages (their own choice first)."""

    return choose_language(
        _settings_for(employee.partner_id),
        staff_choice=employee.user.language,
        telegram=_telegram_language(employee.telegram_account),
    )


def speaking_to_guest(guest):
    """Texts for a message to this guest from a task or another person's update."""

    return texts(guest_language(guest), text_overrides(guest.partner_id))


def speaking_to_employee(employee):
    """Texts for a message to this staff member (notifications, PQ-040)."""

    return texts(employee_language(employee), text_overrides(employee.partner_id))
