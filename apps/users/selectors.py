from apps.users.models import GuestProfile


def get_guest_profile(partner_id, telegram_id: int) -> GuestProfile:
    return GuestProfile.objects.select_related("telegram_account").get(
        partner_id=partner_id,
        telegram_account__telegram_id=telegram_id,
    )


def get_guest_profile_by_customer_code(partner_id, customer_code: str) -> GuestProfile:
    return GuestProfile.objects.select_related("telegram_account").get(
        partner_id=partner_id,
        customer_code=customer_code.strip().upper(),
    )
