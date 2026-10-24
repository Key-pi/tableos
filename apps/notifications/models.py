from django.db import models

from core.database.models import PartnerBoundModel
from core.i18n import gettext_lazy as _lazy


class NotificationPreference(PartnerBoundModel):
    """Per-guest notification opt-in settings within a specific partner."""

    guest = models.OneToOneField(
        "users.GuestProfile",
        on_delete=models.CASCADE,
        related_name="notification_preferences",
        verbose_name=_lazy("Гость"),
    )
    order_updates_enabled = models.BooleanField(default=True, verbose_name=_lazy(
        "Сообщения о заказах",
    ))
    bonus_updates_enabled = models.BooleanField(default=True, verbose_name=_lazy(
        "Сообщения о бонусах",
    ))
    marketing_enabled = models.BooleanField(default=True, verbose_name=_lazy("Рассылки"))

    class Meta:
        verbose_name = _lazy("настройки сообщений гостя")
        verbose_name_plural = _lazy("Настройки сообщений гостей")
        ordering = ["partner__name", "guest_id"]

    def __str__(self) -> str:
        return f"{self.partner.name} / preferences / {self.guest_id}"


class StaffNotification(PartnerBoundModel):
    """Internal notification delivered to partner staff about operational events."""

    class Category(models.TextChoices):
        ORDER_CREATED = "order_created", _lazy("Новый заказ")
        ORDER_STATUS_CHANGED = "order_status_changed", _lazy("Статус заказа")
        GUEST_CALL = "guest_call", _lazy("Вызов персонала")
        BILLING_REQUEST = "billing_request", _lazy("Запрос счёта")
        TABLE_VISIT = "table_visit", _lazy("Визит стола")
        # Changes that move money or rights, sent to the owner (AA-035).
        SECURITY = "security", _lazy("Безопасность")

    class DeliveryStatus(models.TextChoices):
        PENDING = "pending", _lazy("Ожидает")
        SENT = "sent", _lazy("Отправлено")
        FAILED = "failed", _lazy("Не доставлено")

    employee = models.ForeignKey(
        "employees.EmployeeProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="staff_notifications",
        verbose_name=_lazy("Сотрудник"),
    )
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="staff_notifications",
        verbose_name=_lazy("Заказ"),
    )
    category = models.CharField(max_length=32, choices=Category.choices, verbose_name=_lazy("Тема"))
    title = models.CharField(max_length=255, verbose_name=_lazy("Заголовок"))
    message = models.TextField(verbose_name=_lazy("Текст"))
    # The same event in any language (e.g. a repeated staff call), PQ-040.
    dedupe_key = models.CharField(
        max_length=120, blank=True, db_index=True, verbose_name=_lazy("Ключ повтора")
    )
    is_read = models.BooleanField(default=False, verbose_name=_lazy("Прочитано"))
    read_at = models.DateTimeField(null=True, blank=True, verbose_name=_lazy("Прочитано в"))
    delivery_status = models.CharField(
        max_length=16,
        choices=DeliveryStatus.choices,
        default=DeliveryStatus.PENDING,
        verbose_name=_lazy("Доставка"),
    )
    delivered_at = models.DateTimeField(null=True, blank=True, verbose_name=_lazy("Доставлено"))
    delivery_error = models.TextField(blank=True, verbose_name=_lazy("Ошибка доставки"))

    class Meta:
        verbose_name = _lazy("уведомление персоналу")
        verbose_name_plural = _lazy("Уведомления персоналу")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.partner.name} / {self.category} / {self.title}"


class BroadcastCampaign(PartnerBoundModel):
    """Partner marketing or announcement message scheduled for mass delivery."""

    class Status(models.TextChoices):
        DRAFT = "draft", _lazy("Черновик")
        SCHEDULED = "scheduled", _lazy("Запланирована")
        SENDING = "sending", _lazy("Отправляется")
        SENT = "sent", _lazy("Отправлена")
        FAILED = "failed", _lazy("Ошибка")

    name = models.CharField(max_length=255, verbose_name=_lazy("Название рассылки"))
    message = models.TextField(verbose_name=_lazy("Текст рассылки"))
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
        verbose_name=_lazy("Статус"),
    )
    delivered_count = models.PositiveIntegerField(default=0, verbose_name=_lazy("Доставлено"))
    failed_count = models.PositiveIntegerField(default=0, verbose_name=_lazy("Не доставлено"))
    unknown_count = models.PositiveIntegerField(
        default=0,
        help_text=(
            _lazy(
                "Сообщения, судьба которых неизвестна после сбоя воркера. Повторно не "
                "отправляются: гость получает рассылку не больше одного раза (PQ-014).",
            )
        ),
        verbose_name=_lazy("Неизвестно"),
    )
    last_error = models.TextField(blank=True, verbose_name=_lazy("Последняя ошибка"))
    delivery_started_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_lazy("Отправка начата"),
    )
    scheduled_at = models.DateTimeField(null=True, blank=True, verbose_name=_lazy("Отправить в"))
    sent_at = models.DateTimeField(null=True, blank=True, verbose_name=_lazy("Отправлена"))

    class Meta:
        verbose_name = _lazy("рассылка")
        verbose_name_plural = _lazy("Рассылки")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.partner.name} / {self.name}"


class BroadcastDelivery(PartnerBoundModel):
    """One guest's delivery of one campaign: the at-most-once ledger (PQ-014).

    A row is claimed (``sending``) in a short transaction before the Telegram
    call and finished in another one afterwards. A ``sending`` row is never
    claimed again; if its worker dies, recovery marks it ``unknown``.
    """

    class Status(models.TextChoices):
        PENDING = "pending", _lazy("Ожидает")
        SENDING = "sending", _lazy("Отправляется")
        SENT = "sent", _lazy("Отправлено")
        FAILED = "failed", _lazy("Не доставлено")
        SKIPPED = "skipped", _lazy("Пропущено (гость больше не подходит)")
        UNKNOWN = "unknown", _lazy("Неизвестно (воркер остановился)")

    campaign = models.ForeignKey(
        BroadcastCampaign,
        on_delete=models.CASCADE,
        related_name="deliveries",
        verbose_name=_lazy("Рассылка"),
    )
    guest = models.ForeignKey(
        "users.GuestProfile",
        on_delete=models.CASCADE,
        related_name="broadcast_deliveries",
        verbose_name=_lazy("Гость"),
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        verbose_name=_lazy("Статус"),
    )
    claim_token = models.UUIDField(null=True, blank=True, verbose_name=_lazy("Метка захвата"))
    claimed_at = models.DateTimeField(null=True, blank=True, verbose_name=_lazy("Захвачено"))
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name=_lazy("Завершено"))
    error = models.TextField(blank=True, verbose_name=_lazy("Ошибка"))

    class Meta:
        verbose_name = _lazy("доставка рассылки")
        verbose_name_plural = _lazy("Доставки рассылок")
        ordering = ["campaign_id", "guest_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["campaign", "guest"],
                name="unique_broadcast_delivery_per_guest",
            ),
        ]
        indexes = [
            models.Index(fields=["campaign", "status"], name="broadcast_delivery_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.campaign_id} / {self.guest_id} / {self.status}"
