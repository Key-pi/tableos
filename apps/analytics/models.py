import secrets

from django.core.validators import RegexValidator
from django.db import models

from core.database.models import PartnerBoundModel


class PartnerDailyMetric(PartnerBoundModel):
    """Daily aggregated KPI snapshot for partner dashboards and reporting."""

    bucket_date = models.DateField()
    revenue = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    orders_count = models.PositiveIntegerField(default=0)
    average_check = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    active_guests = models.PositiveIntegerField(default=0)
    repeat_visits = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-bucket_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["partner", "bucket_date"],
                name="unique_partner_metric_per_day",
            )
        ]

    def __str__(self) -> str:
        return f"{self.partner.name} / {self.bucket_date}"


class TrafficSource(PartnerBoundModel):
    """A named link that brings guests to the bot: `t.me/<bot>?start=src_<code>`.

    The venue owner creates one per channel (Instagram stories, a flyer at the
    door) and compares how many clicks and new guests each brings.
    """

    PAYLOAD_PREFIX = "src_"
    CODE_PATTERN = r"^[a-z0-9_-]{2,40}$"

    name = models.CharField(max_length=120, verbose_name="Название")
    code = models.CharField(
        max_length=40,
        blank=True,
        verbose_name="Код в ссылке",
        help_text=(
            "Латинские буквы в нижнем регистре, цифры, «-» и «_», от 2 до 40 символов. "
            "Оставьте пустым — код придумается сам. После создания не меняется: "
            "иначе перестанут работать уже напечатанные ссылки."
        ),
        validators=[
            RegexValidator(
                CODE_PATTERN,
                "Только латинские буквы в нижнем регистре, цифры, «-» и «_», 2–40 символов.",
            )
        ],
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Активен",
        help_text="Выключенный источник не считает переходы; гость всё равно попадает в бота.",
    )
    note = models.TextField(blank=True, verbose_name="Заметка")

    class Meta:
        ordering = ["name"]
        verbose_name = "Источник перехода"
        verbose_name_plural = "Источники переходов"
        constraints = [
            models.UniqueConstraint(
                fields=["partner", "code"],
                name="unique_traffic_source_code_per_partner",
            )
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = self._generate_code()
        super().save(*args, **kwargs)

    def _generate_code(self) -> str:
        while True:
            code = secrets.token_hex(4)
            if not TrafficSource.objects.filter(partner_id=self.partner_id, code=code).exists():
                return code

    @property
    def deep_link_payload(self) -> str:
        return f"{self.PAYLOAD_PREFIX}{self.code}"

    def telegram_start_url(self, bot_username: str) -> str:
        return (
            f"https://t.me/{bot_username.strip().removeprefix('@')}?start={self.deep_link_payload}"
        )


class SourceVisit(PartnerBoundModel):
    """One /start through a traffic source link.

    ``is_new_guest`` marks the visit that brought the guest to this venue for the
    first time (first-touch attribution); at most one such visit per guest.
    """

    source = models.ForeignKey(
        TrafficSource,
        on_delete=models.CASCADE,
        related_name="visits",
        verbose_name="Источник",
    )
    guest = models.ForeignKey(
        "users.GuestProfile",
        on_delete=models.CASCADE,
        related_name="source_visits",
        verbose_name="Гость",
    )
    is_new_guest = models.BooleanField(default=False, verbose_name="Новый гость")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Переход по источнику"
        verbose_name_plural = "Переходы по источникам"
        indexes = [models.Index(fields=["source", "created_at"])]
        constraints = [
            models.UniqueConstraint(
                fields=["guest"],
                condition=models.Q(is_new_guest=True),
                name="unique_acquiring_source_visit_per_guest",
            )
        ]

    def __str__(self) -> str:
        return f"{self.source} / {self.created_at:%Y-%m-%d %H:%M}"
