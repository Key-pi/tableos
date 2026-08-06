import secrets

from django.db import models
from django.db.models import Q

from core.database.models import PartnerBoundModel


class Table(PartnerBoundModel):
    """Physical table in a venue with QR metadata for Telegram deep links."""

    number = models.PositiveIntegerField()
    name = models.CharField(max_length=255, blank=True)
    qr_token = models.CharField(max_length=32, unique=True, blank=True)
    seats = models.PositiveSmallIntegerField(default=4)
    is_active = models.BooleanField(default=True)

    class Meta:
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


class TableSession(PartnerBoundModel):
    """Active or historical guest session created after scanning a table QR code."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        CLOSED = "closed", "Closed"
        TIMED_OUT = "timed_out", "Timed out"

    guest = models.ForeignKey(
        "users.GuestProfile",
        on_delete=models.CASCADE,
        related_name="table_sessions",
    )
    table = models.ForeignKey(Table, on_delete=models.CASCADE, related_name="sessions")
    started_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
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
