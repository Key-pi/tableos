from django.utils import timezone

from apps.partners.networks import shared_bonus_scope
from apps.users.models import GuestProfile, TelegramAccount


def get_or_create_guest_profile(
    *,
    partner_id,
    telegram_id: int,
    username: str = "",
    first_name: str = "",
    last_name: str = "",
    language_code: str = "",
) -> GuestProfile:
    guest_profile, _ = get_or_create_guest_profile_with_created(
        partner_id=partner_id,
        telegram_id=telegram_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        language_code=language_code,
    )
    return guest_profile


def get_or_create_guest_profile_with_created(
    *,
    partner_id,
    telegram_id: int,
    username: str = "",
    first_name: str = "",
    last_name: str = "",
    language_code: str = "",
) -> tuple[GuestProfile, bool]:
    """Like ``get_or_create_guest_profile``; also says whether the guest is new here.

    ``created`` is true for exactly one caller even when two /start updates race:
    the profile is unique per partner and Telegram account.
    """

    telegram_account, _ = TelegramAccount.objects.get_or_create(telegram_id=telegram_id)
    changed_fields = []
    if telegram_account.username != username:
        telegram_account.username = username
        changed_fields.append("username")
    if telegram_account.first_name != first_name:
        telegram_account.first_name = first_name
        changed_fields.append("first_name")
    if telegram_account.last_name != last_name:
        telegram_account.last_name = last_name
        changed_fields.append("last_name")
    if telegram_account.language_code != language_code:
        telegram_account.language_code = language_code
        changed_fields.append("language_code")
    if changed_fields:
        changed_fields.append("updated_at")
        telegram_account.save(update_fields=changed_fields)

    guest_profile, created = GuestProfile.objects.get_or_create(
        partner_id=partner_id,
        telegram_account=telegram_account,
        # One guest code for a network with shared bonuses (PQ-047).
        defaults={"customer_code": _network_customer_code(partner_id, telegram_account.id)},
    )
    return (
        GuestProfile.objects.select_related("telegram_account").get(id=guest_profile.id),
        created,
    )


def mark_guest_visit(guest_profile: GuestProfile) -> GuestProfile:
    now = timezone.now()
    guest_profile.last_visit_at = now
    if guest_profile.first_visit_at is None:
        guest_profile.first_visit_at = now
        update_fields = ["first_visit_at", "last_visit_at", "updated_at"]
    else:
        update_fields = ["last_visit_at", "updated_at"]
    guest_profile.save(update_fields=update_fields)
    return guest_profile


class GuestLanguageError(ValueError):
    pass


def set_guest_language(*, partner_id, telegram_id: int, language: str) -> GuestProfile:
    """The guest picks the bot language among the venue's (PQ-040); "" = automatic."""

    from apps.partners.languages import enabled_languages
    from apps.partners.models import PartnerBotSettings
    from core.i18n import _

    settings = PartnerBotSettings.objects.filter(partner_id=partner_id).first()
    if language and language not in enabled_languages(settings):
        raise GuestLanguageError(_("Этот язык заведение не включило."))
    guest = GuestProfile.objects.select_related("telegram_account").get(
        partner_id=partner_id, telegram_account__telegram_id=telegram_id
    )
    if guest.language != language:
        guest.language = language
        guest.save(update_fields=["language", "updated_at"])
    return guest


def _network_customer_code(partner_id, telegram_account_id) -> str:
    """The code this person already has in a network with shared bonuses,
    if it is free at this venue; "" lets the profile make its own."""

    scope = shared_bonus_scope(partner_id)
    if scope is None:
        return ""
    code = (
        GuestProfile.objects.filter(
            partner_id__in=scope.partner_ids, telegram_account_id=telegram_account_id
        )
        .order_by("created_at")
        .values_list("customer_code", flat=True)
        .first()
    )
    if not code or GuestProfile.objects.filter(partner_id=partner_id, customer_code=code).exists():
        return ""
    return code


def find_guest_by_customer_code(partner_id, customer_code: str) -> GuestProfile:
    """The guest a cashier names by code. In a network with shared bonuses
    (PQ-047) a code from any venue works: the guest gets a profile here if
    they had none. A code of this venue's own guest wins. Raises
    ``GuestProfile.DoesNotExist``."""

    code = customer_code.strip().upper()
    try:
        # The anonymous guest of a table visit (PQ-052) is nobody to name.
        return GuestProfile.objects.select_related("telegram_account").get(
            partner_id=partner_id, customer_code=code, telegram_account__isnull=False
        )
    except GuestProfile.DoesNotExist:
        scope = shared_bonus_scope(partner_id)
        if scope is None:
            raise
    elsewhere = (
        GuestProfile.objects.filter(partner_id__in=scope.partner_ids, customer_code=code,
                                    telegram_account__isnull=False)
        .order_by("created_at")
        .first()
    )
    if elsewhere is None:
        raise GuestProfile.DoesNotExist(f"No guest with code {code} in the network.")
    profile, _created = GuestProfile.objects.get_or_create(
        partner_id=partner_id,
        telegram_account_id=elsewhere.telegram_account_id,
        defaults={
            "customer_code": _network_customer_code(partner_id, elsewhere.telegram_account_id)
        },
    )
    return GuestProfile.objects.select_related("telegram_account").get(id=profile.id)


def create_anonymous_guest(*, partner_id) -> GuestProfile:
    """The guest of a table visit who ordered through staff, without the bot
    (PQ-052, step 2): no Telegram, so no messages or broadcasts; earns no
    bonuses; never found by its code. One per company at a table — a guest
    sits at one table at a time, so one per venue would hop between tables."""

    return GuestProfile.objects.create(partner_id=partner_id, telegram_account=None,
                                       is_subscribed=False)
