import secrets
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from core.database.models import PartnerBoundModel


class MenuCategory(PartnerBoundModel):
    """Logical menu section used to group items inside a partner catalog."""

    name = models.CharField(max_length=255, verbose_name="Название")
    sort_order = models.PositiveIntegerField(default=100, verbose_name="Порядок")
    is_active = models.BooleanField(default=True, verbose_name="Показывать")

    class Meta:
        verbose_name = "категория меню"
        verbose_name_plural = "Категории меню"
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
        INTERNAL = "internal", "Своя позиция"
        PARTNER = "partner", "Позиция партнёра"

    category = models.ForeignKey(
        MenuCategory,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="Категория",
    )
    public_id = models.CharField(max_length=12, editable=False, verbose_name="Код позиции")
    name = models.CharField(max_length=255, verbose_name="Название")
    description = models.TextField(blank=True, verbose_name="Описание")
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
        verbose_name="Цена",
    )
    image_url = models.URLField(blank=True, verbose_name="Ссылка на фото")
    item_type = models.CharField(
        max_length=16,
        choices=ItemType.choices,
        default=ItemType.INTERNAL,
        verbose_name="Тип",
    )
    is_available = models.BooleanField(default=True, verbose_name="В наличии")
    # Ordering channels the item is sold in (ECOSYSTEM_DIRECTION.md): a hookah
    # may be table-only, a bottled drink pickup-only.
    available_at_table = models.BooleanField(default=True, verbose_name="За столом")
    available_for_pickup = models.BooleanField(default=True, verbose_name="Самовывоз")
    available_for_delivery = models.BooleanField(default=True, verbose_name="Доставка")
    available_for_shipping = models.BooleanField(default=True, verbose_name="Отправка почтой")
    sort_order = models.PositiveIntegerField(default=100, verbose_name="Порядок")

    CHANNEL_FIELDS = {
        "table": "available_at_table",
        "pickup": "available_for_pickup",
        "delivery": "available_for_delivery",
        "shipping": "available_for_shipping",
    }

    class Meta:
        verbose_name = "позиция меню"
        verbose_name_plural = "Позиции меню"
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

    def is_sold_in(self, channel: str) -> bool:
        return bool(getattr(self, self.CHANNEL_FIELDS[channel]))

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = secrets.token_hex(4)
        return super().save(*args, **kwargs)
