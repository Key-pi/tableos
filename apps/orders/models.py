import secrets

from django.db import models
from django.db.models import Q

from core.database.models import PartnerBoundModel


class Order(PartnerBoundModel):
    """Confirmed guest order tied to a table session and tracked by status."""

    class Status(models.TextChoices):
        NEW = "new", "New"
        ACCEPTED = "accepted", "Accepted"
        PREPARING = "preparing", "Preparing"
        READY = "ready", "Ready"
        DELIVERING = "delivering", "Delivering"
        COMPLETED = "completed", "Completed"
        CANCELED = "canceled", "Canceled"

    class PaymentMethod(models.TextChoices):
        CASH = "cash", "Cash"
        TERMINAL = "terminal", "Terminal"

    public_id = models.CharField(max_length=12, editable=False)
    guest = models.ForeignKey(
        "users.GuestProfile",
        on_delete=models.PROTECT,
        related_name="orders",
    )
    table = models.ForeignKey(
        "tables.Table",
        on_delete=models.PROTECT,
        related_name="orders",
        null=True,
        blank=True,
    )
    table_session = models.ForeignKey(
        "tables.TableSession",
        on_delete=models.PROTECT,
        related_name="orders",
        null=True,
        blank=True,
    )
    assigned_employee = models.ForeignKey(
        "employees.EmployeeProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_orders",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NEW)
    payment_method = models.CharField(
        max_length=16,
        choices=PaymentMethod.choices,
        default=PaymentMethod.CASH,
    )
    comment = models.TextField(blank=True)
    subtotal_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    bonus_spent = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    accepted_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["partner", "public_id"],
                name="unique_order_public_id_per_partner",
            )
        ]

    def __str__(self) -> str:
        return f"{self.partner.name} / order {self.public_id}"

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = secrets.token_hex(4)
        return super().save(*args, **kwargs)


class OrderStatusHistory(PartnerBoundModel):
    """Audit trail entry that records who changed an order status and when."""

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="status_history",
    )
    from_status = models.CharField(max_length=16, blank=True)
    to_status = models.CharField(max_length=16, choices=Order.Status.choices)
    changed_by = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="changed_order_statuses",
    )
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.order.public_id}: {self.from_status or '-'} -> {self.to_status}"


class Cart(PartnerBoundModel):
    """Draft basket for a guest before checkout into a final order."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        CHECKED_OUT = "checked_out", "Checked out"
        ABANDONED = "abandoned", "Abandoned"

    guest = models.ForeignKey(
        "users.GuestProfile",
        on_delete=models.CASCADE,
        related_name="carts",
    )
    table_session = models.ForeignKey(
        "tables.TableSession",
        on_delete=models.CASCADE,
        related_name="carts",
        null=True,
        blank=True,
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    comment = models.TextField(blank=True)
    subtotal_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    checked_out_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["partner", "guest"],
                condition=Q(status="active"),
                name="unique_active_cart_per_partner_guest",
            )
        ]

    def __str__(self) -> str:
        return f"{self.partner.name} / cart / {self.guest_id} / {self.status}"


class OrderItem(PartnerBoundModel):
    """Snapshot of a purchased menu item inside a confirmed order."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    menu_item = models.ForeignKey(
        "menu.MenuItem",
        on_delete=models.PROTECT,
        related_name="order_items",
    )
    item_name = models.CharField(max_length=255)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField(default=1)
    comment = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.item_name} x{self.quantity}"


class CartItem(PartnerBoundModel):
    """Draft basket line for a menu item before the order is created."""

    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    menu_item = models.ForeignKey(
        "menu.MenuItem",
        on_delete=models.PROTECT,
        related_name="cart_items",
    )
    item_name = models.CharField(max_length=255)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField(default=1)
    comment = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["cart", "menu_item"],
                name="unique_menu_item_per_cart",
            )
        ]

    def __str__(self) -> str:
        return f"{self.item_name} x{self.quantity}"
