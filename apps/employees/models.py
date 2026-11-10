from django.conf import settings
from django.db import models

from core.database.models import PartnerBoundModel
from core.i18n import gettext_lazy as _lazy
from core.labels import venue_label, within_one_venue


class EmployeeProfile(PartnerBoundModel):
    """Compensation and staffing profile for a partner employee account."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="employee_profile",
        verbose_name=_lazy("Пользователь"),
    )
    telegram_account = models.ForeignKey(
        "users.TelegramAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="employee_profiles",
        verbose_name="Telegram",
    )
    title = models.CharField(max_length=255, blank=True, verbose_name=_lazy("Должность"))
    hourly_rate = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name=_lazy("Ставка в час"),
    )
    commission_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        verbose_name=_lazy("Процент с продаж"),
    )
    bot_notifications_enabled = models.BooleanField(default=True, verbose_name=_lazy(
        "Уведомления в боте",
    ))
    notify_on_order_created = models.BooleanField(default=True, verbose_name=_lazy(
        "О новых заказах",
    ))
    notify_on_order_status_changed = models.BooleanField(
        default=True,
        verbose_name=_lazy("О смене статуса заказа"),
    )
    notify_on_guest_calls = models.BooleanField(default=True, verbose_name=_lazy(
        "О вызовах персонала",
    ))
    notify_on_billing_requests = models.BooleanField(default=True, verbose_name=_lazy(
        "О запросах счёта",
    ))
    # Orders of which ordering modules this employee hears about (e.g. a
    # courier — only delivery). Applies to new orders and status changes.
    notify_channel_table = models.BooleanField(default=True, verbose_name=_lazy("Заказы за столом"))
    notify_channel_pickup = models.BooleanField(default=True, verbose_name=_lazy("Самовывоз"))
    notify_channel_delivery = models.BooleanField(default=True, verbose_name=_lazy("Доставка"))
    notify_channel_shipping = models.BooleanField(default=True, verbose_name=_lazy(
        "Отправка почтой",
    ))

    CHANNEL_NOTIFY_FIELDS = {
        "table": "notify_channel_table",
        "pickup": "notify_channel_pickup",
        "delivery": "notify_channel_delivery",
        "shipping": "notify_channel_shipping",
    }
    # Granted only by the venue owner: change or void bills (PQ-028).
    can_adjust_bills = models.BooleanField(
        default=False,
        verbose_name=_lazy("Может изменять счета"),
        help_text=_lazy(
            "Убирать позиции, отменять заказы в счёте и аннулировать счета. Выдаёт только "
            "владелец заведения.",
        ),
    )
    is_active = models.BooleanField(default=True, verbose_name=_lazy("Активен"))
    # PQ-047: the network's owner works in every venue of it, a manager in
    # the venues given to them — through a copy of their own staff profile
    # kept by apps/employees/network_staff.py.
    mirrored_from = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="mirrors",
        editable=False,
        verbose_name=_lazy("Копия сотрудника из точки"),
    )

    class Meta:
        verbose_name = _lazy("сотрудник")
        verbose_name_plural = _lazy("Сотрудники")
        ordering = ["partner__name", "user__username"]
        constraints = [
            models.UniqueConstraint(
                fields=["partner", "telegram_account"],
                name="unique_employee_telegram_account_per_partner",
            )
        ]

    def __str__(self) -> str:
        return venue_label(self, f"{self.user}")


class StaffInvite(PartnerBoundModel):
    """A one-time link that adds a staff member (PQ-046): the owner or a
    manager issues it for a role, the person presses Start, the issuer
    confirms. ``apps/employees/invites.py`` owns every change."""

    class Status(models.TextChoices):
        PENDING = "pending", _lazy("Ждёт нажатия ссылки")
        CLAIMED = "claimed", _lazy("Ждёт подтверждения")
        ACCEPTED = "accepted", _lazy("Принято")
        REJECTED = "rejected", _lazy("Отклонено")
        REVOKED = "revoked", _lazy("Отменено")

    code = models.CharField(max_length=32, unique=True, editable=False, verbose_name=_lazy("Код"))
    role = models.CharField(max_length=32, verbose_name=_lazy("Роль"))
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        verbose_name=_lazy("Статус"),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="staff_invites_issued",
        verbose_name=_lazy("Выдал"),
    )
    expires_at = models.DateTimeField(verbose_name=_lazy("Действует до"))
    claimed_by = models.ForeignKey(
        "users.TelegramAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="staff_invites",
        verbose_name=_lazy("Кто нажал"),
    )
    claimed_at = models.DateTimeField(null=True, blank=True, verbose_name=_lazy("Нажал"))
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="staff_invites_decided",
        verbose_name=_lazy("Решил"),
    )
    decided_at = models.DateTimeField(null=True, blank=True, verbose_name=_lazy("Решено"))
    employee = models.ForeignKey(
        EmployeeProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invites",
        verbose_name=_lazy("Сотрудник"),
    )

    class Meta:
        verbose_name = _lazy("приглашение сотрудника")
        verbose_name_plural = _lazy("Приглашения сотрудников")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        if within_one_venue():
            role = dict(self._meta.apps.get_model("users", "User").Role.choices)
            return f"{role.get(self.role, self.role)} · {self.get_status_display()}"
        return f"{self.partner} / {self.role} / {self.status}"
