import secrets
from decimal import Decimal

from django.conf import settings
from django.db import models

from core.database.models import PartnerBoundModel


class Bill(PartnerBoundModel):
    """Payable check built from one or many orders without mutating the original order data."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ISSUED = "issued", "Issued"
        PARTIALLY_PAID = "partially_paid", "Partially paid"
        PAID = "paid", "Paid"
        CANCELED = "canceled", "Canceled"

    class Kind(models.TextChoices):
        PERSONAL = "personal", "Personal"
        SHARED = "shared", "Shared"
        SPLIT = "split", "Split"

    class Source(models.TextChoices):
        INTERNAL = "internal", "Internal"
        POS = "pos", "POS"

    public_id = models.CharField(max_length=12, editable=False)
    table = models.ForeignKey(
        "tables.Table",
        on_delete=models.PROTECT,
        related_name="bills",
        null=True,
        blank=True,
    )
    primary_guest = models.ForeignKey(
        "users.GuestProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="billing_bills",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.SHARED)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.INTERNAL)
    label = models.CharField(max_length=255, blank=True)
    subtotal_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    bonus_spent_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    paid_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    external_ref = models.CharField(max_length=255, blank=True)
    pos_tab_id = models.CharField(max_length=255, blank=True)
    issued_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["partner", "public_id"],
                name="unique_bill_public_id_per_partner",
            )
        ]

    def __str__(self) -> str:
        return f"{self.partner.name} / bill {self.public_id}"

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = secrets.token_hex(4)
        return super().save(*args, **kwargs)


class BillingRequest(PartnerBoundModel):
    """Guest settlement intent that records how the table wants to pay before final payment."""

    class RequestType(models.TextChoices):
        PERSONAL = "personal", "Personal bill"
        SHARED = "shared", "Shared bill"
        CUSTOM_SPLIT = "custom_split", "Custom split"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        AUTO_PREPARED = "auto_prepared", "Auto prepared"
        PROCESSED = "processed", "Processed"
        CANCELED = "canceled", "Canceled"

    table = models.ForeignKey(
        "tables.Table",
        on_delete=models.PROTECT,
        related_name="billing_requests",
    )
    guest = models.ForeignKey(
        "users.GuestProfile",
        on_delete=models.PROTECT,
        related_name="billing_requests",
    )
    table_session = models.ForeignKey(
        "tables.TableSession",
        on_delete=models.PROTECT,
        related_name="billing_requests",
    )
    request_type = models.CharField(max_length=32, choices=RequestType.choices)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.OPEN)
    bill = models.ForeignKey(
        "billing.Bill",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="billing_requests",
    )
    note = models.CharField(max_length=255, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.partner.name} / billing request / {self.request_type}"


class BillOrder(PartnerBoundModel):
    """Join model that tracks which orders were grouped into one bill."""

    bill = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name="bill_orders")
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.PROTECT,
        related_name="bill_links",
    )

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["bill", "order"],
                name="unique_order_per_bill",
            )
        ]

    def __str__(self) -> str:
        return f"{self.bill.public_id} <- {self.order.public_id}"


class BillItem(PartnerBoundModel):
    """Allocated order line inside a bill so one order can be split across multiple checks."""

    bill = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name="items")
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.PROTECT,
        related_name="billing_items",
    )
    order_item = models.ForeignKey(
        "orders.OrderItem",
        on_delete=models.PROTECT,
        related_name="billing_items",
    )
    item_name = models.CharField(max_length=255)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField(default=1)
    line_total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    comment = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.bill.public_id} / {self.item_name} x{self.quantity}"

    def save(self, *args, **kwargs):
        self.line_total = (Decimal(self.unit_price) * self.quantity).quantize(Decimal("0.01"))
        return super().save(*args, **kwargs)


class Payment(PartnerBoundModel):
    """Payment attempt or completed payment attached to one bill."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        AUTHORIZED = "authorized", "Authorized"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"
        REFUNDED = "refunded", "Refunded"
        CANCELED = "canceled", "Canceled"

    class Method(models.TextChoices):
        CASH = "cash", "Cash"
        TERMINAL = "terminal", "Terminal"
        ONLINE = "online", "Online"
        MIXED = "mixed", "Mixed"

    bill = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name="payments")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    method = models.CharField(max_length=16, choices=Method.choices, default=Method.CASH)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    provider_code = models.CharField(max_length=64, blank=True)
    external_payment_id = models.CharField(max_length=255, blank=True)
    comment = models.CharField(max_length=255, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="billing_payments",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.bill.public_id} / {self.method} / {self.amount}"
