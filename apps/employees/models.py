from django.conf import settings
from django.db import models

from core.database.models import PartnerBoundModel


class EmployeeProfile(PartnerBoundModel):
    """Compensation and staffing profile for a partner employee account."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="employee_profile",
    )
    telegram_account = models.ForeignKey(
        "users.TelegramAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="employee_profiles",
    )
    title = models.CharField(max_length=255, blank=True)
    hourly_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    commission_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    bot_notifications_enabled = models.BooleanField(default=True)
    notify_on_order_created = models.BooleanField(default=True)
    notify_on_order_status_changed = models.BooleanField(default=True)
    notify_on_guest_calls = models.BooleanField(default=True)
    notify_on_billing_requests = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["partner__name", "user__username"]
        constraints = [
            models.UniqueConstraint(
                fields=["partner", "telegram_account"],
                name="unique_employee_telegram_account_per_partner",
            )
        ]

    def __str__(self) -> str:
        return f"{self.partner.name} / {self.user}"
