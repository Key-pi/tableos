from apps.users.models import GuestProfile, TelegramAccount, User


class UserRepository:
    @staticmethod
    def tenant_staff():
        return User.objects.exclude(partner__isnull=True).select_related("partner")


class GuestRepository:
    @staticmethod
    def for_partner(partner_id):
        return GuestProfile.objects.filter(partner_id=partner_id).select_related("telegram_account")


class TelegramAccountRepository:
    @staticmethod
    def get_or_create_account(telegram_id: int) -> tuple[TelegramAccount, bool]:
        return TelegramAccount.objects.get_or_create(telegram_id=telegram_id)
