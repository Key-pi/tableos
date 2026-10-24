from django.conf import settings
from django.db import models

from core.database.models import PartnerBoundModel


class EmployeeProfile(PartnerBoundModel):
    """Compensation and staffing profile for a partner employee account."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="employee_profile",
        verbose_name="Пользователь",
    )
    telegram_account = models.ForeignKey(
        "users.TelegramAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="employee_profiles",
        verbose_name="Telegram",
    )
    title = models.CharField(max_length=255, blank=True, verbose_name="Должность")
    hourly_rate = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name="Ставка в час",
    )
    commission_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        verbose_name="Процент с продаж",
    )
    bot_notifications_enabled = models.BooleanField(default=True, verbose_name="Уведомления в боте")
    notify_on_order_created = models.BooleanField(default=True, verbose_name="О новых заказах")
    notify_on_order_status_changed = models.BooleanField(
        default=True,
        verbose_name="О смене статуса заказа",
    )
    notify_on_guest_calls = models.BooleanField(default=True, verbose_name="О вызовах персонала")
    notify_on_billing_requests = models.BooleanField(default=True, verbose_name="О запросах счёта")
    # Orders of which ordering modules this employee hears about (e.g. a
    # courier — only delivery). Applies to new orders and status changes.
    notify_channel_table = models.BooleanField(default=True, verbose_name="Заказы за столом")
    notify_channel_pickup = models.BooleanField(default=True, verbose_name="Самовывоз")
    notify_channel_delivery = models.BooleanField(default=True, verbose_name="Доставка")
    notify_channel_shipping = models.BooleanField(default=True, verbose_name="Отправка почтой")

    CHANNEL_NOTIFY_FIELDS = {
        "table": "notify_channel_table",
        "pickup": "notify_channel_pickup",
        "delivery": "notify_channel_delivery",
        "shipping": "notify_channel_shipping",
    }
    # Granted only by the venue owner: change or void bills (PQ-028).
    can_adjust_bills = models.BooleanField(
        default=False,
        verbose_name="Может изменять счета",
        help_text="Убирать позиции, отменять заказы в счёте и аннулировать счета. "
        "Выдаёт только владелец заведения.",
    )
    is_active = models.BooleanField(default=True, verbose_name="Активен")

    class Meta:
        verbose_name = "сотрудник"
        verbose_name_plural = "Сотрудники"
        ordering = ["partner__name", "user__username"]
        constraints = [
            models.UniqueConstraint(
                fields=["partner", "telegram_account"],
                name="unique_employee_telegram_account_per_partner",
            )
        ]

    def __str__(self) -> str:
        return f"{self.partner.name} / {self.user}"
