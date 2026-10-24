import secrets

from django.core.validators import RegexValidator
from django.db import models

from core.database.models import PartnerBoundModel
from core.i18n import gettext_lazy as _lazy


class PartnerDailyMetric(PartnerBoundModel):
    """Daily aggregated KPI snapshot for partner dashboards and reporting."""

    bucket_date = models.DateField(verbose_name=_lazy("День"))
    revenue = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        verbose_name=_lazy("Выручка"),
    )
    orders_count = models.PositiveIntegerField(default=0, verbose_name=_lazy("Заказов"))
    average_check = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name=_lazy("Средний чек"),
    )
    active_guests = models.PositiveIntegerField(default=0, verbose_name=_lazy("Активных гостей"))
    repeat_visits = models.PositiveIntegerField(default=0, verbose_name=_lazy("Повторных визитов"))

    class Meta:
        verbose_name = _lazy("метрика за день")
        verbose_name_plural = _lazy("Метрики по дням")
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

    name = models.CharField(max_length=120, verbose_name=_lazy("Название"))
    code = models.CharField(
        max_length=40,
        blank=True,
        verbose_name=_lazy("Код в ссылке"),
        help_text=(
            _lazy(
                "Латинские буквы в нижнем регистре, цифры, «-» и «_», от 2 до 40 символов. "
                "Оставьте пустым — код придумается сам. После создания не меняется: иначе "
                "перестанут работать уже напечатанные ссылки.",
            )
        ),
        validators=[
            RegexValidator(
                CODE_PATTERN,
                _lazy("Только латинские буквы в нижнем регистре, цифры, «-» и «_», 2–40 символов."),
            )
        ],
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name=_lazy("Активен"),
        help_text=_lazy(
            "Выключенный источник не считает переходы; гость всё равно попадает в бота.",
        ),
    )
    note = models.TextField(blank=True, verbose_name=_lazy("Заметка"))

    class Meta:
        ordering = ["name"]
        verbose_name = _lazy("Источник перехода")
        verbose_name_plural = _lazy("Источники переходов")
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
        verbose_name=_lazy("Источник"),
    )
    guest = models.ForeignKey(
        "users.GuestProfile",
        on_delete=models.CASCADE,
        related_name="source_visits",
        verbose_name=_lazy("Гость"),
    )
    is_new_guest = models.BooleanField(default=False, verbose_name=_lazy("Новый гость"))

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _lazy("Переход по источнику")
        verbose_name_plural = _lazy("Переходы по источникам")
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
