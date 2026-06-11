from django.utils import timezone

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
    telegram_account, _ = TelegramAccount.objects.get_or_create(telegram_id=telegram_id)
    changed_fields = []
    if username and telegram_account.username != username:
        telegram_account.username = username
        changed_fields.append("username")
    if first_name and telegram_account.first_name != first_name:
        telegram_account.first_name = first_name
        changed_fields.append("first_name")
    if last_name and telegram_account.last_name != last_name:
        telegram_account.last_name = last_name
        changed_fields.append("last_name")
    if language_code and telegram_account.language_code != language_code:
        telegram_account.language_code = language_code
        changed_fields.append("language_code")
    if changed_fields:
        changed_fields.append("updated_at")
        telegram_account.save(update_fields=changed_fields)

    guest_profile, _ = GuestProfile.objects.get_or_create(
        partner_id=partner_id,
        telegram_account=telegram_account,
    )
    return GuestProfile.objects.select_related("telegram_account").get(id=guest_profile.id)


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
