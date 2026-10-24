import secrets

from django.db import models
from django.db.models import Q

from core.database.models import PartnerBoundModel


class Table(PartnerBoundModel):
    """Physical table in a venue with QR metadata for Telegram deep links."""

    number = models.PositiveIntegerField(verbose_name="Номер")
    name = models.CharField(max_length=255, blank=True, verbose_name="Название")
    qr_token = models.CharField(max_length=32, unique=True, blank=True, verbose_name="Код QR")
    seats = models.PositiveSmallIntegerField(default=4, verbose_name="Мест")
    is_active = models.BooleanField(default=True, verbose_name="Работает")

    class Meta:
        verbose_name = "стол"
        verbose_name_plural = "Столы"
        ordering = ["partner__name", "number"]
        constraints = [
            models.UniqueConstraint(
                fields=["partner", "number"],
                name="unique_table_number_per_partner",
            )
        ]

    def __str__(self) -> str:
        return f"{self.partner.name} / table {self.number}"

    @property
    def deep_link_payload(self) -> str:
        return f"table_{self.number}_{self.qr_token}"

    def telegram_start_url(self, bot_username: str) -> str:
        normalized_username = bot_username.strip().removeprefix("@")
        return f"https://t.me/{normalized_username}?start={self.deep_link_payload}"

    def save(self, *args, **kwargs):
        if not self.qr_token:
            self.qr_token = secrets.token_urlsafe(9)
        return super().save(*args, **kwargs)


class TableVisit(PartnerBoundModel):
    """One party sitting at a table: groups guest sessions and scopes billing (ADR-0007)."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Идёт"
        CLOSED = "closed", "Закрыт"

    table = models.ForeignKey(
        Table,
        on_delete=models.CASCADE,
        related_name="visits",
        verbose_name="Стол",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
        verbose_name="Статус",
    )
    started_at = models.DateTimeField(auto_now_add=True, verbose_name="Начат")
    closed_at = models.DateTimeField(null=True, blank=True, verbose_name="Закрыт")
    closed_by = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="closed_table_visits",
        verbose_name="Кто закрыл",
    )

    class Meta:
        verbose_name = "визит стола"
        verbose_name_plural = "Визиты столов"
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["partner", "table", "status"], name="table_visit_lookup_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.partner.name} / table {self.table.number} / visit {self.status}"


class TableSession(PartnerBoundModel):
    """Active or historical guest session created after scanning a table QR code."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Активна"
        CLOSED = "closed", "Закрыта"
        TIMED_OUT = "timed_out", "Истекла"

    guest = models.ForeignKey(
        "users.GuestProfile",
        on_delete=models.CASCADE,
        related_name="table_sessions",
        verbose_name="Гость",
    )
    table = models.ForeignKey(
        Table,
        on_delete=models.CASCADE,
        related_name="sessions",
        verbose_name="Стол",
    )
    # Historical sessions closed before table visits existed have no visit.
    visit = models.ForeignKey(
        TableVisit,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="sessions",
        verbose_name="Визит",
    )
    started_at = models.DateTimeField(auto_now_add=True, verbose_name="Начат")
    expires_at = models.DateTimeField(null=True, blank=True, verbose_name="Истекает")
    closed_at = models.DateTimeField(null=True, blank=True, verbose_name="Закрыт")
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
        verbose_name="Статус",
    )

    class Meta:
        verbose_name = "сессия стола"
        verbose_name_plural = "Сессии столов"
        ordering = ["-started_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["partner", "guest"],
                condition=Q(status="active"),
                name="unique_active_table_session_per_partner_guest",
            )
        ]

    def __str__(self) -> str:
        return f"{self.partner.name} / table {self.table.number} / {self.status}"
