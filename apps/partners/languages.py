"""Which language the bot speaks to a person (PQ-040).

Order: the language the guest chose in the bot (if the venue still offers
it), the staff member's own language, the language of their Telegram (if
the venue offers it), the venue's main language.
"""

from __future__ import annotations

from apps.partners.models import GUEST_LANGUAGE_FIELDS, BotText, PartnerBotSettings
from core.i18n import LANGUAGES, default_language, normalize_language


def enabled_languages(settings: PartnerBotSettings | None) -> tuple[str, ...]:
    """Languages the venue offers guests, the main one first."""

    if settings is None:
        return LANGUAGES
    main = main_language(settings)
    others = [
        code
        for code in LANGUAGES
        if code != main and getattr(settings, GUEST_LANGUAGE_FIELDS[code])
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


def text_overrides(partner_id) -> dict[tuple[str, str], str]:
    """The owner's own texts, keyed by (language, catalog text)."""

    return {
        (language, msgid): text
        for language, msgid, text in BotText.objects.filter(partner_id=partner_id)
        .exclude(text="")
        .values_list("language", "msgid", "text")
    }
