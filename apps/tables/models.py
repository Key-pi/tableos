import secrets

from django.db import models
from django.db.models import Q

from core.database.models import PartnerBoundModel
from core.i18n import gettext_lazy as _lazy


class Table(PartnerBoundModel):
    """Physical table in a venue with QR metadata for Telegram deep links."""

    number = models.PositiveIntegerField(verbose_name=_lazy("Номер"))
    name = models.CharField(max_length=255, blank=True, verbose_name=_lazy("Название"))
    qr_token = models.CharField(max_length=32, unique=True, blank=True, verbose_name=_lazy(
        "Код QR",
    ))
    seats = models.PositiveSmallIntegerField(default=4, verbose_name=_lazy("Мест"))
    is_active = models.BooleanField(default=True, verbose_name=_lazy("Работает"))

    class Meta:
        verbose_name = _lazy("стол")
        verbose_name_plural = _lazy("Столы")
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
        ACTIVE = "active", _lazy("Идёт")
        CLOSED = "closed", _lazy("Закрыт")

    table = models.ForeignKey(
        Table,
        on_delete=models.CASCADE,
        related_name="visits",
        verbose_name=_lazy("Стол"),
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
        verbose_name=_lazy("Статус"),
    )
    started_at = models.DateTimeField(auto_now_add=True, verbose_name=_lazy("Начат"))
    closed_at = models.DateTimeField(null=True, blank=True, verbose_name=_lazy("Закрыт"))
    closed_by = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="closed_table_visits",
        verbose_name=_lazy("Кто закрыл"),
    )

    class Meta:
        verbose_name = _lazy("визит стола")
        verbose_name_plural = _lazy("Визиты столов")
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["partner", "table", "status"], name="table_visit_lookup_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.partner.name} / table {self.table.number} / visit {self.status}"


class TableSession(PartnerBoundModel):
    """Active or historical guest session created after scanning a table QR code."""

    class Status(models.TextChoices):
        ACTIVE = "active", _lazy("Активна")
        CLOSED = "closed", _lazy("Закрыта")
        TIMED_OUT = "timed_out", _lazy("Истекла")

    guest = models.ForeignKey(
        "users.GuestProfile",
        on_delete=models.CASCADE,
        related_name="table_sessions",
        verbose_name=_lazy("Гость"),
    )
    table = models.ForeignKey(
        Table,
        on_delete=models.CASCADE,
        related_name="sessions",
        verbose_name=_lazy("Стол"),
    )
    # Historical sessions closed before table visits existed have no visit.
    visit = models.ForeignKey(
        TableVisit,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="sessions",
        verbose_name=_lazy("Визит"),
    )
    started_at = models.DateTimeField(auto_now_add=True, verbose_name=_lazy("Начат"))
    expires_at = models.DateTimeField(null=True, blank=True, verbose_name=_lazy("Истекает"))
    closed_at = models.DateTimeField(null=True, blank=True, verbose_name=_lazy("Закрыт"))
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
        verbose_name=_lazy("Статус"),
    )

    class Meta:
        verbose_name = _lazy("сессия стола")
        verbose_name_plural = _lazy("Сессии столов")
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
