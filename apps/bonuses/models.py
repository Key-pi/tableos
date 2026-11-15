
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from core.database.models import PartnerBoundModel
from core.i18n import gettext_lazy as _lazy
from core.labels import venue_label, within_one_venue


class BonusProgram(PartnerBoundModel):
    """Partner-defined loyalty ruleset used to accrue and spend bonuses."""

    class TriggerEvent(models.TextChoices):
        VISIT = "visit", _lazy("Визит")
        ORDER_COMPLETED = "order_completed", _lazy("Оплаченный заказ")
        # A sale at the counter (PQ-054; before it, the quick sale).
        MANUAL_PURCHASE = "manual_purchase", _lazy("Покупка на кассе")

    class ProgramType(models.TextChoices):
        CASHBACK = "cashback", _lazy("Кешбэк")
        VISIT = "visit", _lazy("За визит")
        MILESTONE = "milestone", _lazy("За N-й заказ")

    class AccrualBase(models.TextChoices):
        FULL_TOTAL = "full_total", _lazy("Вся сумма покупки")
        MONEY_PAID = "money_paid", _lazy("Только оплаченное деньгами (без бонусов)")

    name = models.CharField(max_length=255, verbose_name=_lazy("Название"))
    trigger_event = models.CharField(
        max_length=32,
        choices=TriggerEvent.choices,
        default=TriggerEvent.ORDER_COMPLETED,
        verbose_name=_lazy("Событие"),
    )
    program_type = models.CharField(
        max_length=16,
        choices=ProgramType.choices,
        verbose_name=_lazy("Тип программы"),
    )
    strategy_code = models.CharField(
        max_length=64,
        blank=True,
        help_text=_lazy("Необязательный ключ особой стратегии начисления."),
        verbose_name=_lazy("Код стратегии"),
    )
    fixed_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name=_lazy("Фиксированная сумма"),
    )
    percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, verbose_name=_lazy(
        "Процент",
    ))
    min_order_total = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name=_lazy("Минимальная сумма"),
    )
    accrual_base = models.CharField(
        max_length=16,
        choices=AccrualBase.choices,
        default=AccrualBase.FULL_TOTAL,
        help_text=(
            _lazy(
                "Только для кешбэка: начислять со всей суммы покупки или только с оплаченного "
                "деньгами (PQ-030). Минимальная сумма всегда сравнивается со всей суммой.",
            )
        ),
        verbose_name=_lazy("База начисления"),
    )
    milestone_order_count = models.PositiveIntegerField(default=0, verbose_name=_lazy(
        "Каждый N-й заказ",
    ))
    max_redeem_share = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=30,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text=_lazy("Какую долю счёта можно оплатить бонусами, % (0–100)."),
        verbose_name=_lazy("Можно оплатить бонусами, % счёта"),
    )
    expires_in_days = models.PositiveIntegerField(default=90, verbose_name=_lazy(
        "Сгорают через, дней",
    ))
    config = models.JSONField(
        default=dict,
        blank=True,
        help_text=_lazy("Резерв для настроек особой стратегии."),
        verbose_name=_lazy("Настройки (JSON)"),
    )
    is_active = models.BooleanField(default=True, verbose_name=_lazy("Работает"))

    class Meta:
        verbose_name = _lazy("бонусная программа")
        verbose_name_plural = _lazy("Бонусные программы")
        ordering = ["partner__name", "name"]

    def __str__(self) -> str:
        return venue_label(self, f"{self.name}")


class BonusTransaction(PartnerBoundModel):
    """Ledger record for bonus accrual, redemption, expiration, or manual change."""

    class TransactionType(models.TextChoices):
        ACCRUAL = "accrual", _lazy("Начисление")
        REDEMPTION = "redemption", _lazy("Списание")
        EXPIRATION = "expiration", _lazy("Сгорание")
        MANUAL = "manual", _lazy("Вручную")
        # Compensating credit, e.g. bonuses returned when a bill is voided.
        REVERSAL = "reversal", _lazy("Возврат")

    guest = models.ForeignKey(
        "users.GuestProfile",
        on_delete=models.CASCADE,
        related_name="bonus_transactions",
        verbose_name=_lazy("Гость"),
    )
    program = models.ForeignKey(
        BonusProgram,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
        verbose_name=_lazy("Программа"),
    )
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bonus_transactions",
        verbose_name=_lazy("Заказ"),
    )
    bill = models.ForeignKey(
        "billing.Bill",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bonus_transactions",
        verbose_name=_lazy("Счёт"),
    )
    transaction_type = models.CharField(
        max_length=16,
        choices=TransactionType.choices,
        verbose_name=_lazy("Операция"),
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name=_lazy("Сумма"))
    expires_at = models.DateTimeField(null=True, blank=True, verbose_name=_lazy("Сгорают"))
    comment = models.CharField(max_length=255, blank=True, verbose_name=_lazy("Комментарий"))
    source_type = models.CharField(max_length=32, blank=True, verbose_name=_lazy("Тип источника"))
    source_id = models.CharField(max_length=64, blank=True, verbose_name=_lazy("ID источника"))
    idempotency_key = models.CharField(
        max_length=128,
        blank=True,
        verbose_name=_lazy("Ключ идемпотентности"),
    )

    class Meta:
        verbose_name = _lazy("бонусная операция")
        verbose_name_plural = _lazy("Бонусные операции")
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["partner", "idempotency_key"],
                condition=~Q(idempotency_key=""),
                name="unique_bonus_idempotency_per_partner",
            )
        ]

    def __str__(self) -> str:
        if within_one_venue():
            return f"{self.get_transaction_type_display()} · {self.amount}"
        return f"{self.partner.name} / {self.transaction_type} / {self.amount}"
