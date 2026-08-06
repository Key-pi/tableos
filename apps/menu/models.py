import secrets
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from core.database.models import PartnerBoundModel


class MenuCategory(PartnerBoundModel):
    """Logical menu section used to group items inside a partner catalog."""

    name = models.CharField(max_length=255)
    sort_order = models.PositiveIntegerField(default=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["partner", "name"],
                name="unique_menu_category_name_per_partner",
            )
        ]

    def __str__(self) -> str:
        return f"{self.partner.name} / {self.name}"


class MenuItem(PartnerBoundModel):
    """Sellable menu position shown to guests and reused in carts and orders."""

    class ItemType(models.TextChoices):
        INTERNAL = "internal", "Internal"
        PARTNER = "partner", "Partner"

    category = models.ForeignKey(MenuCategory, on_delete=models.CASCADE, related_name="items")
    public_id = models.CharField(max_length=12, editable=False)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    image_url = models.URLField(blank=True)
    item_type = models.CharField(max_length=16, choices=ItemType.choices, default=ItemType.INTERNAL)
    is_available = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=100)

    class Meta:
        ordering = ["category__sort_order", "sort_order", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["partner", "public_id"],
                name="unique_menu_item_public_id_per_partner",
            ),
            models.CheckConstraint(
                condition=Q(price__gt=0),
                name="menu_item_price_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.partner.name} / {self.name}"

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = secrets.token_hex(4)
        return super().save(*args, **kwargs)
