import secrets

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Max, Q

from core.database.models import PartnerBoundModel
from core.i18n import _
from core.i18n import gettext_lazy as _lazy
from core.labels import within_one_venue

# The floor plan (PQ-052): a hall is a grid of cells, a table takes a
# rectangle of cells in it (apps/tables/floor_plan.py).
HALL_MAX_WIDTH = 60
HALL_MAX_HEIGHT = 40
TABLE_MAX_CELLS = 8


class Hall(PartnerBoundModel):
    """A room or zone of the venue on the floor plan (PQ-052)."""

    name = models.CharField(max_length=60, verbose_name=_lazy("Название"))
    sort_order = models.PositiveSmallIntegerField(default=0, verbose_name=_lazy("Порядок"))
    width = models.PositiveSmallIntegerField(
        default=16, validators=[MinValueValidator(4), MaxValueValidator(HALL_MAX_WIDTH)],
        verbose_name=_lazy("Ширина, клеток"),
    )
    height = models.PositiveSmallIntegerField(
        default=10, validators=[MinValueValidator(4), MaxValueValidator(HALL_MAX_HEIGHT)],
        verbose_name=_lazy("Глубина, клеток"),
    )

    class Meta:
        verbose_name = _lazy("зал")
        verbose_name_plural = _lazy("Схема зала")
        ordering = ["sort_order", "name"]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()
        if not self.pk:
            return
        reach = self.tables.exclude(plan_x=None).aggregate(
            right=Max(models.F("plan_x") + models.F("plan_w")),
            bottom=Max(models.F("plan_y") + models.F("plan_h")),
        )
        errors = {}
        if reach["right"] and self.width < reach["right"]:
            errors["width"] = _("Столы стоят до клетки {cells}: сначала подвиньте их.",
                                cells=reach["right"])
        if reach["bottom"] and self.height < reach["bottom"]:
            errors["height"] = _("Столы стоят до клетки {cells}: сначала подвиньте их.",
                                 cells=reach["bottom"])
        if errors:
            raise ValidationError(errors)


class Table(PartnerBoundModel):
    """Physical table in a venue with QR metadata for Telegram deep links."""

    class Shape(models.TextChoices):
        SQUARE = "square", _lazy("Квадратный")
        ROUND = "round", _lazy("Круглый")

    number = models.PositiveIntegerField(verbose_name=_lazy("Номер"))
    name = models.CharField(max_length=255, blank=True, verbose_name=_lazy("Название"))
    qr_token = models.CharField(max_length=32, unique=True, blank=True, verbose_name=_lazy(
        "Код QR",
    ))
    seats = models.PositiveSmallIntegerField(default=4, verbose_name=_lazy("Мест"))
    is_active = models.BooleanField(default=True, verbose_name=_lazy("Работает"))
    # The floor plan (PQ-052): written only by floor_plan.save_hall_layout.
    hall = models.ForeignKey(
        Hall, on_delete=models.SET_NULL, null=True, blank=True, related_name="tables",
        verbose_name=_lazy("Зал"),
    )
    plan_x = models.PositiveSmallIntegerField(null=True, blank=True,
                                              verbose_name=_lazy("Клетка слева"))
    plan_y = models.PositiveSmallIntegerField(null=True, blank=True,
                                              verbose_name=_lazy("Клетка сверху"))
    plan_w = models.PositiveSmallIntegerField(default=2, verbose_name=_lazy("Ширина, клеток"))
    plan_h = models.PositiveSmallIntegerField(default=2, verbose_name=_lazy("Глубина, клеток"))
    shape = models.CharField(max_length=8, choices=Shape.choices, default=Shape.SQUARE,
                             verbose_name=_lazy("Форма"))

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
        if within_one_venue():
            return _("Стол {number}", number=self.number)
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
        if within_one_venue():
            return f"{self.table} · {self.get_status_display()}"
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
        if within_one_venue():
            return f"{self.table} · {self.get_status_display()}"
        return f"{self.partner.name} / table {self.table.number} / {self.status}"


class Reservation(PartnerBoundModel):
    """A table booked by staff or in Admin (PQ-052, step 3). Written only by
    ``apps/tables/reservations.py``: one table is never booked twice for
    overlapping time; «Пришли» opens the visit."""

    class Status(models.TextChoices):
        BOOKED = "booked", _lazy("Ждём")
        SEATED = "seated", _lazy("Пришли")
        NO_SHOW = "no_show", _lazy("Не пришли")
        CANCELED = "canceled", _lazy("Отменена")

    table = models.ForeignKey(Table, on_delete=models.PROTECT, related_name="reservations",
                              verbose_name=_lazy("Стол"))
    starts_at = models.DateTimeField(verbose_name=_lazy("Время"))
    minutes = models.PositiveSmallIntegerField(default=120,
                                               verbose_name=_lazy("Длительность, минут"))
    # Derived from the two above on every save — for the overlap check and
    # the plan in the database.
    ends_at = models.DateTimeField(editable=False, verbose_name=_lazy("До"))
    guests = models.PositiveSmallIntegerField(default=2, verbose_name=_lazy("Гостей"))
    name = models.CharField(max_length=80, verbose_name=_lazy("Имя"))
    phone = models.CharField(max_length=32, verbose_name=_lazy("Телефон"))
    comment = models.CharField(max_length=300, blank=True, verbose_name=_lazy("Комментарий"))
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.BOOKED,
                              verbose_name=_lazy("Статус"))
    visit = models.ForeignKey(TableVisit, on_delete=models.SET_NULL, null=True, blank=True,
                              related_name="reservations", verbose_name=_lazy("Визит"))
    created_by = models.ForeignKey(
        "users.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name=_lazy("Забронировал"),
    )
    closed_by = models.ForeignKey(
        "users.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name=_lazy("Отметил"),
    )
    closed_at = models.DateTimeField(null=True, blank=True, verbose_name=_lazy("Отмечена"))

    class Meta:
        verbose_name = _lazy("бронь")
        verbose_name_plural = _lazy("Бронь")
        ordering = ["starts_at"]
        indexes = [
            models.Index(fields=["partner", "table", "starts_at"]),
            models.Index(fields=["partner", "status", "starts_at"]),
        ]

    def __str__(self) -> str:
        from django.utils import timezone

        local = timezone.localtime(self.starts_at)
        return f"{local:%d.%m %H:%M} · {self.name}"

    def save(self, *args, **kwargs):
        from datetime import timedelta

        self.ends_at = self.starts_at + timedelta(minutes=self.minutes)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and {"starts_at", "minutes"} & set(update_fields):
            kwargs["update_fields"] = {*update_fields, "ends_at"}
        super().save(*args, **kwargs)
