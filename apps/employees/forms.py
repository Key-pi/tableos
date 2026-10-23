from django import forms

from apps.employees.models import EmployeeProfile
from apps.users.models import AdminAccessProfile, TelegramAccount


def _is_owner_or_superuser(user) -> bool:
    if user is None or not getattr(user, "is_active", False):
        return False
    if user.is_superuser:
        return True
    return AdminAccessProfile.objects.filter(
        user=user,
        is_partner_owner=True,
        can_access_admin=True,
        is_active=True,
    ).exists()


class EmployeeProfileAdminForm(forms.ModelForm):
    telegram_id = forms.IntegerField(
        required=False,
        help_text="Telegram ID сотрудника для bot-уведомлений.",
    )
    telegram_username = forms.CharField(
        required=False,
        help_text="Опционально: username сотрудника без @.",
    )

    class Meta:
        model = EmployeeProfile
        fields = (
            "partner",
            "user",
            "telegram_id",
            "telegram_username",
            "title",
            "hourly_rate",
            "commission_rate",
            "bot_notifications_enabled",
            "notify_on_order_created",
            "notify_on_order_status_changed",
            "notify_on_guest_calls",
            "notify_on_billing_requests",
            "can_adjust_bills",
            "is_active",
        )

    def __init__(self, *args, actor_user=None, **kwargs):
        self.actor_user = actor_user
        super().__init__(*args, **kwargs)
        account = self.instance.telegram_account if self.instance.pk else None
        if account is not None:
            self.fields["telegram_id"].initial = account.telegram_id
            self.fields["telegram_username"].initial = account.username
        if "can_adjust_bills" in self.fields and not _is_owner_or_superuser(self.actor_user):
            # PQ-028: only the venue owner grants the right to change bills.
            self.fields["can_adjust_bills"].disabled = True
            self.fields["can_adjust_bills"].help_text = (
                "Право выдаёт только владелец заведения."
            )
        if not getattr(self.actor_user, "is_superuser", False):
            self.fields["telegram_id"].disabled = True
            self.fields[
                "telegram_id"
            ].help_text = "Только superuser может менять глобальный Telegram ID."

    def clean_telegram_username(self) -> str:
        return self.cleaned_data.get("telegram_username", "").strip().removeprefix("@")

    def clean(self):
        cleaned_data = super().clean()
        telegram_id = cleaned_data.get("telegram_id")
        wants_notifications = any(
            cleaned_data.get(field_name, False)
            for field_name in (
                "bot_notifications_enabled",
                "notify_on_order_created",
                "notify_on_order_status_changed",
                "notify_on_guest_calls",
                "notify_on_billing_requests",
            )
        )
        if wants_notifications and not telegram_id:
            self.add_error(
                "telegram_id",
                "Telegram ID is required if bot notifications are enabled.",
            )
        return cleaned_data

    def save(self, commit: bool = True) -> EmployeeProfile:
        instance = super().save(commit=False)
        telegram_id = self.cleaned_data.get("telegram_id")
        telegram_username = self.cleaned_data.get("telegram_username", "")

        if telegram_id:
            account, _ = TelegramAccount.objects.get_or_create(
                telegram_id=telegram_id,
                defaults={"username": telegram_username},
            )
            if account.username != telegram_username:
                account.username = telegram_username
                account.save(update_fields=["username", "updated_at"])
            instance.telegram_account = account
        else:
            instance.telegram_account = None

        if commit:
            instance.save()
            self.save_m2m()
        return instance
