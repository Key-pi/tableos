from django.db import models

from core.database.models import PartnerBoundModel


class BonusProgram(PartnerBoundModel):
    """Partner-defined loyalty ruleset used to accrue and spend bonuses."""

    class TriggerEvent(models.TextChoices):
        VISIT = "visit", "Visit"
        ORDER_COMPLETED = "order_completed", "Order completed"
        MANUAL_PURCHASE = "manual_purchase", "Manual purchase"

    class ProgramType(models.TextChoices):
        CASHBACK = "cashback", "Cashback"
        VISIT = "visit", "Visit"
        MILESTONE = "milestone", "Milestone"

    name = models.CharField(max_length=255)
    trigger_event = models.CharField(
        max_length=32,
        choices=TriggerEvent.choices,
        default=TriggerEvent.ORDER_COMPLETED,
    )
    program_type = models.CharField(max_length=16, choices=ProgramType.choices)
    strategy_code = models.CharField(
        max_length=64,
        blank=True,
        help_text="Optional custom strategy key for partner-specific bonus code paths.",
    )
    fixed_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    min_order_total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    milestone_order_count = models.PositiveIntegerField(default=0)
    max_redeem_share = models.DecimalField(max_digits=5, decimal_places=2, default=30)
    expires_in_days = models.PositiveIntegerField(default=90)
    config = models.JSONField(
        default=dict,
        blank=True,
        help_text="Reserved for custom strategy options and partner-specific tuning.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["partner__name", "name"]

    def __str__(self) -> str:
        return f"{self.partner.name} / {self.name}"


class BonusTransaction(PartnerBoundModel):
    """Ledger record for bonus accrual, redemption, expiration, or manual change."""

    class TransactionType(models.TextChoices):
        ACCRUAL = "accrual", "Accrual"
        REDEMPTION = "redemption", "Redemption"
        EXPIRATION = "expiration", "Expiration"
        MANUAL = "manual", "Manual"

    guest = models.ForeignKey(
        "users.GuestProfile",
        on_delete=models.CASCADE,
        related_name="bonus_transactions",
    )
    program = models.ForeignKey(
        BonusProgram,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
    )
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bonus_transactions",
    )
    transaction_type = models.CharField(max_length=16, choices=TransactionType.choices)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    expires_at = models.DateTimeField(null=True, blank=True)
    comment = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]

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
    )
    customer_code_snapshot = models.CharField(max_length=12, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    bonus_awarded_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    comment = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_walk_in_sales",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.partner.name} / walk-in {self.amount}"

    @property
    def loyalty_label(self) -> str:
        if self.customer_code_snapshot:
            return self.customer_code_snapshot
        return "Аноним"


class WalkInSaleItem(PartnerBoundModel):
    """Persisted line item for a walk-in cashier sale created outside table ordering."""

    sale = models.ForeignKey(
        WalkInSale,
        on_delete=models.CASCADE,
        related_name="items",
    )
    menu_item = models.ForeignKey(
        "menu.MenuItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="walk_in_sale_items",
    )
    item_name = models.CharField(max_length=255)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["created_at", "item_name"]

    def __str__(self) -> str:
        return f"{self.sale} / {self.item_name} x{self.quantity}"
