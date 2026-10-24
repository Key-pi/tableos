from django.db import models

from core.database.models import PartnerBoundModel


class NotificationPreference(PartnerBoundModel):
    """Per-guest notification opt-in settings within a specific partner."""

    guest = models.OneToOneField(
        "users.GuestProfile",
        on_delete=models.CASCADE,
        related_name="notification_preferences",
        verbose_name="Гость",
    )
    order_updates_enabled = models.BooleanField(default=True, verbose_name="Сообщения о заказах")
    bonus_updates_enabled = models.BooleanField(default=True, verbose_name="Сообщения о бонусах")
    marketing_enabled = models.BooleanField(default=True, verbose_name="Рассылки")

    class Meta:
        verbose_name = "настройки сообщений гостя"
        verbose_name_plural = "Настройки сообщений гостей"
        ordering = ["partner__name", "guest_id"]

    def __str__(self) -> str:
        return f"{self.partner.name} / preferences / {self.guest_id}"


class StaffNotification(PartnerBoundModel):
    """Internal notification delivered to partner staff about operational events."""

    class Category(models.TextChoices):
        ORDER_CREATED = "order_created", "Новый заказ"
        ORDER_STATUS_CHANGED = "order_status_changed", "Статус заказа"
        GUEST_CALL = "guest_call", "Вызов персонала"
        BILLING_REQUEST = "billing_request", "Запрос счёта"
        TABLE_VISIT = "table_visit", "Визит стола"
        # Changes that move money or rights, sent to the owner (AA-035).
        SECURITY = "security", "Безопасность"

    class DeliveryStatus(models.TextChoices):
        PENDING = "pending", "Ожидает"
        SENT = "sent", "Отправлено"
        FAILED = "failed", "Не доставлено"

    employee = models.ForeignKey(
        "employees.EmployeeProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="staff_notifications",
        verbose_name="Сотрудник",
    )
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="staff_notifications",
        verbose_name="Заказ",
    )
    category = models.CharField(max_length=32, choices=Category.choices, verbose_name="Тема")
    title = models.CharField(max_length=255, verbose_name="Заголовок")
    message = models.TextField(verbose_name="Текст")
    is_read = models.BooleanField(default=False, verbose_name="Прочитано")
    read_at = models.DateTimeField(null=True, blank=True, verbose_name="Прочитано в")
    delivery_status = models.CharField(
        max_length=16,
        choices=DeliveryStatus.choices,
        default=DeliveryStatus.PENDING,
        verbose_name="Доставка",
    )
    delivered_at = models.DateTimeField(null=True, blank=True, verbose_name="Доставлено")
    delivery_error = models.TextField(blank=True, verbose_name="Ошибка доставки")

    class Meta:
        verbose_name = "уведомление персоналу"
        verbose_name_plural = "Уведомления персоналу"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.partner.name} / {self.category} / {self.title}"


class BroadcastCampaign(PartnerBoundModel):
    """Partner marketing or announcement message scheduled for mass delivery."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        SCHEDULED = "scheduled", "Запланирована"
        SENDING = "sending", "Отправляется"
        SENT = "sent", "Отправлена"
        FAILED = "failed", "Ошибка"

    name = models.CharField(max_length=255, verbose_name="Название рассылки")
    message = models.TextField(verbose_name="Текст рассылки")
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
        verbose_name="Статус",
    )
    delivered_count = models.PositiveIntegerField(default=0, verbose_name="Доставлено")
    failed_count = models.PositiveIntegerField(default=0, verbose_name="Не доставлено")
    unknown_count = models.PositiveIntegerField(
        default=0,
        help_text=(
            "Сообщения, судьба которых неизвестна после сбоя воркера. Повторно не "
            "отправляются: гость получает рассылку не больше одного раза (PQ-014)."
        ),
        verbose_name="Неизвестно",
    )
    last_error = models.TextField(blank=True, verbose_name="Последняя ошибка")
    delivery_started_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Отправка начата",
    )
    scheduled_at = models.DateTimeField(null=True, blank=True, verbose_name="Отправить в")
    sent_at = models.DateTimeField(null=True, blank=True, verbose_name="Отправлена")

    class Meta:
        verbose_name = "рассылка"
        verbose_name_plural = "Рассылки"
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
        PENDING = "pending", "Ожидает"
        SENDING = "sending", "Отправляется"
        SENT = "sent", "Отправлено"
        FAILED = "failed", "Не доставлено"
        SKIPPED = "skipped", "Пропущено (гость больше не подходит)"
        UNKNOWN = "unknown", "Неизвестно (воркер остановился)"

    campaign = models.ForeignKey(
        BroadcastCampaign,
        on_delete=models.CASCADE,
        related_name="deliveries",
        verbose_name="Рассылка",
    )
    guest = models.ForeignKey(
        "users.GuestProfile",
        on_delete=models.CASCADE,
        related_name="broadcast_deliveries",
        verbose_name="Гость",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        verbose_name="Статус",
    )
    claim_token = models.UUIDField(null=True, blank=True, verbose_name="Метка захвата")
    claimed_at = models.DateTimeField(null=True, blank=True, verbose_name="Захвачено")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="Завершено")
    error = models.TextField(blank=True, verbose_name="Ошибка")

    class Meta:
        verbose_name = "доставка рассылки"
        verbose_name_plural = "Доставки рассылок"
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
