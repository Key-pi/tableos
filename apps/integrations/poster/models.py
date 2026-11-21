"""Data of the Poster integration (ADR-0010, PQ-058; plan — docs/architecture/POSTER.md).

Step 1 keeps only the records; connecting, menu sync, order pushes and
webhook processing come in the next steps and write here through their own
services.
"""

from __future__ import annotations

import hashlib
import json
import re

from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q

from core.database.models import PartnerBoundModel, TimeStampedModel
from core.i18n import gettext_lazy as _lazy
from core.labels import venue_label
from core.security.crypto import decrypt_text, encrypt_text

# A Poster login is the subdomain of ``https://<login>.joinposter.com``; it
# arrives in the OAuth callback's query, so only a plain login is accepted.
POSTER_ACCOUNT_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62})$")
poster_account_validator = RegexValidator(
    POSTER_ACCOUNT_RE, _lazy("Логин Poster: латинские буквы, цифры и дефис."),
)


class PosterConnection(PartnerBoundModel):
    """A venue's link to one spot of a Poster account."""

    class Status(models.TextChoices):
        PENDING_SPOT = "pending_spot", _lazy("Ждёт выбора точки")
        ACTIVE = "active", _lazy("Работает")
        DISCONNECTED = "disconnected", _lazy("Отключено")

    account = models.CharField(
        max_length=63, validators=[poster_account_validator],
        verbose_name=_lazy("Аккаунт Poster"),
    )
    account_number = models.CharField(max_length=32, verbose_name=_lazy("Номер аккаунта Poster"))
    spot_id = models.PositiveIntegerField(null=True, blank=True, verbose_name=_lazy("Точка Poster"))
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING_SPOT,
        verbose_name=_lazy("Статус"),
    )
    # Fernet with BOT_TOKEN_ENCRYPTION_KEY, like bot tokens; never shown.
    access_token_encrypted = models.TextField(blank=True, verbose_name=_lazy("Токен (зашифрован)"))
    token_expires_at = models.DateTimeField(
        null=True, blank=True, verbose_name=_lazy("Токен действует до"),
    )
    tariff = models.JSONField(default=dict, blank=True, verbose_name=_lazy("Тариф Poster"))
    # Spots of the account at the last login: [{"spot_id", "name", "address"}].
    available_spots = models.JSONField(
        default=list, blank=True, verbose_name=_lazy("Точки аккаунта"),
    )
    connected_by = models.ForeignKey(
        "users.User", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+", verbose_name=_lazy("Подключил"),
    )
    last_synced_at = models.DateTimeField(
        null=True, blank=True, verbose_name=_lazy("Меню обновлено"),
    )
    last_error = models.TextField(blank=True, verbose_name=_lazy("Последняя ошибка"))
    last_error_at = models.DateTimeField(
        null=True, blank=True, verbose_name=_lazy("Когда была ошибка"),
    )

    class Meta:
        verbose_name = _lazy("подключение к Poster")
        verbose_name_plural = _lazy("Подключение к Poster")
        constraints = [
            models.UniqueConstraint(fields=["partner"], name="poster_connection_one_per_partner"),
            # One spot serves one venue at a time; a disconnected venue frees it.
            models.UniqueConstraint(
                fields=["account_number", "spot_id"],
                condition=Q(status="active"),
                name="poster_connection_active_spot_unique",
            ),
        ]

    def __str__(self) -> str:
        return venue_label(self, f"Poster {self.account}")

    def set_access_token(self, raw_token: str) -> None:
        self.access_token_encrypted = encrypt_text(raw_token)

    def get_access_token(self) -> str:
        if not self.access_token_encrypted:
            return ""
        return decrypt_text(self.access_token_encrypted)

    def clear_access_token(self) -> None:
        self.access_token_encrypted = ""
        self.token_expires_at = None


class PosterRef(PartnerBoundModel):
    """Which Botera object a Poster object became (menu sync, step 3)."""

    class Kind(models.TextChoices):
        CATEGORY = "category", _lazy("Категория")
        PRODUCT = "product", _lazy("Позиция")
        VARIANT = "variant", _lazy("Вариант")
        MODIFIER_GROUP = "modifier_group", _lazy("Группа добавок")
        MODIFIER = "modifier", _lazy("Добавка")

    kind = models.CharField(max_length=16, choices=Kind.choices, verbose_name=_lazy("Что это"))
    # A variant has no id of its own in Poster: «<product_id>:<modificator_id>».
    poster_id = models.CharField(max_length=64, verbose_name=_lazy("ID в Poster"))
    object_id = models.UUIDField(verbose_name=_lazy("ID в Botera"))
    removed_in_poster = models.BooleanField(default=False, verbose_name=_lazy("Удалено в Poster"))
    # What Poster said at the last sync (categories and items): only a change
    # there hides or shows the row, so Botera's stop list survives (step 3).
    hidden_in_poster = models.BooleanField(default=False, verbose_name=_lazy("Скрыто в Poster"))

    class Meta:
        verbose_name = _lazy("соответствие Poster")
        verbose_name_plural = _lazy("Соответствия Poster")
        constraints = [
            models.UniqueConstraint(
                fields=["partner", "kind", "poster_id"], name="poster_ref_unique_poster_object",
            ),
            models.UniqueConstraint(
                fields=["partner", "kind", "object_id"], name="poster_ref_unique_botera_object",
            ),
        ]

    def __str__(self) -> str:
        return venue_label(self, f"{self.get_kind_display()} {self.poster_id}")

    @staticmethod
    def variant_key(product_id, modificator_id) -> str:
        return f"{product_id}:{modificator_id}"


class PosterOrderPush(PartnerBoundModel):
    """Sending one Botera order to the Poster register (outbox, step 5)."""

    class Status(models.TextChoices):
        PENDING = "pending", _lazy("Ждёт отправки")
        SENDING = "sending", _lazy("Отправляется")
        SENT = "sent", _lazy("В Poster")
        FAILED = "failed", _lazy("Не дошёл")
        UNKNOWN = "unknown", _lazy("Неизвестно, проверяется")

    order = models.OneToOneField(
        "orders.Order", on_delete=models.CASCADE, related_name="poster_push",
        verbose_name=_lazy("Заказ"),
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING,
        verbose_name=_lazy("Статус"),
    )
    attempts = models.PositiveSmallIntegerField(default=0, verbose_name=_lazy("Попыток"))
    claimed_at = models.DateTimeField(null=True, blank=True, verbose_name=_lazy("Взят в работу"))
    sent_at = models.DateTimeField(null=True, blank=True, verbose_name=_lazy("Отправлен"))
    incoming_order_id = models.PositiveBigIntegerField(
        null=True, blank=True, verbose_name=_lazy("Онлайн-заказ Poster"),
    )
    transaction_id = models.PositiveBigIntegerField(
        null=True, blank=True, verbose_name=_lazy("Чек Poster"),
    )
    poster_sum = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True, verbose_name=_lazy("Сумма чека"),
    )
    poster_payed_sum = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        verbose_name=_lazy("Оплачено по чеку"),
    )
    last_error = models.TextField(blank=True, verbose_name=_lazy("Последняя ошибка"))

    class Meta:
        verbose_name = _lazy("передача заказа в Poster")
        verbose_name_plural = _lazy("Передача заказов в Poster")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return venue_label(self, f"#{self.order.public_id}")


class PosterWebhookEvent(TimeStampedModel):
    """Inbox of Poster webhooks: one row per event, however often Poster
    repeats it (step 4). The venue is known only after the signature is
    checked and the account matched to a connection."""

    class Status(models.TextChoices):
        RECEIVED = "received", _lazy("Принят")
        PROCESSED = "processed", _lazy("Обработан")
        IGNORED = "ignored", _lazy("Не наш")
        FAILED = "failed", _lazy("Ошибка")

    partner = models.ForeignKey(
        "partners.Partner", null=True, blank=True, on_delete=models.CASCADE,
        related_name="poster_webhook_events", verbose_name=_lazy("Заведение"),
    )
    dedupe_key = models.CharField(max_length=64, unique=True, verbose_name=_lazy("Ключ события"))
    account = models.CharField(max_length=63, verbose_name=_lazy("Аккаунт Poster"))
    entity = models.CharField(max_length=32, verbose_name=_lazy("Объект"))
    entity_id = models.CharField(max_length=64, verbose_name=_lazy("ID объекта"))
    action = models.CharField(max_length=32, verbose_name=_lazy("Действие"))
    event_time = models.PositiveBigIntegerField(verbose_name=_lazy("Время события"))
    payload = models.JSONField(default=dict, blank=True, verbose_name=_lazy("Данные"))
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.RECEIVED,
        verbose_name=_lazy("Статус"),
    )
    processed_at = models.DateTimeField(null=True, blank=True, verbose_name=_lazy("Обработан"))
    error = models.TextField(blank=True, verbose_name=_lazy("Ошибка"))

    class Meta:
        verbose_name = _lazy("вебхук Poster")
        verbose_name_plural = _lazy("Вебхуки Poster")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"], name="poster_webhook_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.account} {self.entity} {self.action} {self.entity_id}"

    @staticmethod
    def dedupe_key_for(body: dict) -> str:
        """The same event gives the same key; ``data`` is part of it: Poster
        sends several events of one object in the same second (step 0).
        The signature is not: it does not tell events apart."""

        data = body.get("data")
        if data is not None and not isinstance(data, str):
            data = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        parts = [
            str(body.get(name, "")) for name in ("account", "object", "object_id", "action", "time")
        ]
        parts.append(data or "")
        return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
