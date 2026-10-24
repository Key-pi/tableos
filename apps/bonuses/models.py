from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from core.database.models import PartnerBoundModel


class BonusProgram(PartnerBoundModel):
    """Partner-defined loyalty ruleset used to accrue and spend bonuses."""

    class TriggerEvent(models.TextChoices):
        VISIT = "visit", "Визит"
        ORDER_COMPLETED = "order_completed", "Оплаченный заказ"
        MANUAL_PURCHASE = "manual_purchase", "Покупка, внесённая вручную"

    class ProgramType(models.TextChoices):
        CASHBACK = "cashback", "Кешбэк"
        VISIT = "visit", "За визит"
        MILESTONE = "milestone", "За N-й заказ"

    class AccrualBase(models.TextChoices):
        FULL_TOTAL = "full_total", "Вся сумма покупки"
        MONEY_PAID = "money_paid", "Только оплаченное деньгами (без бонусов)"

    name = models.CharField(max_length=255, verbose_name="Название")
    trigger_event = models.CharField(
        max_length=32,
        choices=TriggerEvent.choices,
        default=TriggerEvent.ORDER_COMPLETED,
        verbose_name="Событие",
    )
    program_type = models.CharField(
        max_length=16,
        choices=ProgramType.choices,
        verbose_name="Тип программы",
    )
    strategy_code = models.CharField(
        max_length=64,
        blank=True,
        help_text="Необязательный ключ особой стратегии начисления.",
        verbose_name="Код стратегии",
    )
    fixed_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name="Фиксированная сумма",
    )
    percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, verbose_name="Процент")
    min_order_total = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name="Минимальная сумма",
    )
    accrual_base = models.CharField(
        max_length=16,
        choices=AccrualBase.choices,
        default=AccrualBase.FULL_TOTAL,
        help_text=(
            "Только для кешбэка: начислять со всей суммы покупки или только с "
            "оплаченного деньгами (PQ-030). Минимальная сумма всегда сравнивается "
            "со всей суммой."
        ),
        verbose_name="База начисления",
    )
    milestone_order_count = models.PositiveIntegerField(default=0, verbose_name="Каждый N-й заказ")
    max_redeem_share = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=30,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Какую долю счёта можно оплатить бонусами, % (0–100).",
        verbose_name="Можно оплатить бонусами, % счёта",
    )
    expires_in_days = models.PositiveIntegerField(default=90, verbose_name="Сгорают через, дней")
    config = models.JSONField(
        default=dict,
        blank=True,
        help_text="Резерв для настроек особой стратегии.",
        verbose_name="Настройки (JSON)",
    )
    is_active = models.BooleanField(default=True, verbose_name="Работает")

    class Meta:
        verbose_name = "бонусная программа"
        verbose_name_plural = "Бонусные программы"
        ordering = ["partner__name", "name"]

    def __str__(self) -> str:
        return f"{self.partner.name} / {self.name}"


class BonusTransaction(PartnerBoundModel):
    """Ledger record for bonus accrual, redemption, expiration, or manual change."""

    class TransactionType(models.TextChoices):
        ACCRUAL = "accrual", "Начисление"
        REDEMPTION = "redemption", "Списание"
        EXPIRATION = "expiration", "Сгорание"
        MANUAL = "manual", "Вручную"
        # Compensating credit, e.g. bonuses returned when a bill is voided.
        REVERSAL = "reversal", "Возврат"

    guest = models.ForeignKey(
        "users.GuestProfile",
        on_delete=models.CASCADE,
        related_name="bonus_transactions",
        verbose_name="Гость",
    )
    program = models.ForeignKey(
        BonusProgram,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
        verbose_name="Программа",
    )
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bonus_transactions",
        verbose_name="Заказ",
    )
    bill = models.ForeignKey(
        "billing.Bill",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bonus_transactions",
        verbose_name="Счёт",
    )
    walk_in_sale = models.ForeignKey(
        "bonuses.WalkInSale",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bonus_transactions",
        verbose_name="Быстрая продажа",
    )
    transaction_type = models.CharField(
        max_length=16,
        choices=TransactionType.choices,
        verbose_name="Операция",
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Сумма")
    expires_at = models.DateTimeField(null=True, blank=True, verbose_name="Сгорают")
    comment = models.CharField(max_length=255, blank=True, verbose_name="Комментарий")
    source_type = models.CharField(max_length=32, blank=True, verbose_name="Тип источника")
    source_id = models.CharField(max_length=64, blank=True, verbose_name="ID источника")
    idempotency_key = models.CharField(
        max_length=128,
        blank=True,
        verbose_name="Ключ идемпотентности",
    )

    class Meta:
        verbose_name = "бонусная операция"
        verbose_name_plural = "Бонусные операции"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["partner", "idempotency_key"],
                condition=~Q(idempotency_key=""),
                name="unique_bonus_idempotency_per_partner",
            )
        ]

    def __str__(self) -> str:
        return f"{self.partner.name} / {self.transaction_type} / {self.amount}"


class WalkInSale(PartnerBoundModel):
    """Manual cashier sale without a table session but with loyalty accrual support."""

    guest = models.ForeignKey(
        "users.GuestProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="walk_in_sales",
        verbose_name="Гость",
    )
    customer_code_snapshot = models.CharField(
        max_length=12,
        blank=True,
        verbose_name="Код клиента (на момент продажи)",
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
        verbose_name="Сумма продажи",
    )
    bonus_awarded_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name="Начислено бонусов",
    )
    bonus_spent_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name="Оплачено бонусами",
    )
    comment = models.CharField(max_length=255, blank=True, verbose_name="Комментарий")
    created_by = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_walk_in_sales",
        verbose_name="Кто создал",
    )

    class Meta:
        verbose_name = "быстрая продажа"
        verbose_name_plural = "Быстрые продажи"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name="walk_in_sale_amount_positive",
            )
        ]

    def __str__(self) -> str:
        return f"{self.partner.name} / walk-in {self.amount}"

    @property
    def loyalty_label(self) -> str:
        if self.customer_code_snapshot:
            return self.customer_code_snapshot
        return "Аноним"

    @property
    def net_amount(self):
        # Amount the customer actually pays after bonus redemption.
        return self.amount - self.bonus_spent_amount


class WalkInSaleItem(PartnerBoundModel):
    """Persisted line item for a walk-in cashier sale created outside table ordering."""

    sale = models.ForeignKey(
        WalkInSale,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="Продажа",
    )
    menu_item = models.ForeignKey(
        "menu.MenuItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="walk_in_sale_items",
        verbose_name="Позиция меню",
    )
    item_name = models.CharField(max_length=255, verbose_name="Позиция")
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Цена")
    quantity = models.PositiveIntegerField(default=1, verbose_name="Количество")

    class Meta:
        verbose_name = "позиция быстрой продажи"
        verbose_name_plural = "Позиции быстрых продаж"
        ordering = ["created_at", "item_name"]

    def __str__(self) -> str:
        return f"{self.sale} / {self.item_name} x{self.quantity}"
