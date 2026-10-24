import secrets

from django.db import models
from django.db.models import Q

from core.database.models import PartnerBoundModel
from core.i18n import gettext_lazy as _lazy


class Order(PartnerBoundModel):
    """Confirmed guest order tied to a table session and tracked by status."""

    class Status(models.TextChoices):
        # Labels are what guests and staff read («Новый статус: В пути»).
        NEW = "new", _lazy("Новый")
        ACCEPTED = "accepted", _lazy("Принят")
        PREPARING = "preparing", _lazy("Готовится")
        READY = "ready", _lazy("Готов")
        DELIVERING = "delivering", _lazy("В пути")
        COMPLETED = "completed", _lazy("Завершён")
        CANCELED = "canceled", _lazy("Отменён")

    class PaymentMethod(models.TextChoices):
        CASH = "cash", _lazy("Наличные")
        TERMINAL = "terminal", _lazy("Карта (терминал)")
        TRANSFER = "transfer", _lazy("Перевод")
        MIXED = "mixed", _lazy("Смешанная")
        BONUSES = "bonuses", _lazy("Бонусы")
        COD = "cod", _lazy("Наложенный платёж")

    class Channel(models.TextChoices):
        # How the guest receives the order (ECOSYSTEM_DIRECTION.md): a table
        # is one channel among others.
        TABLE = "table", _lazy("За столом")
        PICKUP = "pickup", _lazy("Самовывоз")
        DELIVERY = "delivery", _lazy("Доставка")
        SHIPPING = "shipping", _lazy("Отправка почтой")

    class Carrier(models.TextChoices):
        # Shipping by post (PQ-034); no carrier API, staff enter the ТТН.
        NOVA_POSHTA = "nova_poshta", _lazy("Нова Пошта")
        UKRPOSHTA = "ukrposhta", _lazy("Укрпошта")
        MEEST = "meest", "Meest"

    class ShippingCost(models.TextChoices):
        # Who pays the postage, chosen by the guest from the owner's options.
        CARRIER = "carrier", _lazy("По тарифу перевозчика, платит получатель")
        FIXED = "fixed", _lazy("В заказе")

    class PaymentTiming(models.TextChoices):
        # Remote orders (PQ-033): the guest pays in advance by transfer, which
        # staff confirm, or on receipt. Empty for table orders (paid by bill).
        PREPAID = "prepaid", _lazy("Переводом заранее")
        ON_RECEIPT = "on_receipt", _lazy("При получении")

    # Channels where the guest is not at a table (ADR-0009): they go through
    # apps/billing/remote_orders.py. How each order is paid is
    # ``payment_timing``, chosen at checkout from the module's options.
    REMOTE_CHANNELS = frozenset({Channel.PICKUP, Channel.DELIVERY, Channel.SHIPPING})
    # Old name, kept for existing imports: these channels are no longer
    # always prepaid.
    PREPAID_CHANNELS = REMOTE_CHANNELS

    public_id = models.CharField(max_length=12, editable=False, verbose_name=_lazy("Номер"))
    guest = models.ForeignKey(
        "users.GuestProfile",
        on_delete=models.PROTECT,
        related_name="orders",
        verbose_name=_lazy("Гость"),
    )
    table = models.ForeignKey(
        "tables.Table",
        on_delete=models.PROTECT,
        related_name="orders",
        null=True,
        blank=True,
        verbose_name=_lazy("Стол"),
    )
    table_session = models.ForeignKey(
        "tables.TableSession",
        on_delete=models.PROTECT,
        related_name="orders",
        null=True,
        blank=True,
        verbose_name=_lazy("Сессия стола"),
    )
    assigned_employee = models.ForeignKey(
        "employees.EmployeeProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_orders",
        verbose_name=_lazy("Ответственный"),
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.NEW,
        verbose_name=_lazy("Статус"),
    )
    channel = models.CharField(
        max_length=16,
        choices=Channel.choices,
        default=Channel.TABLE,
        verbose_name=_lazy("Способ заказа"),
    )
    requested_for = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            _lazy("Самовывоз и доставка: к какому времени; пусто — как можно скорее.")
        ),
        verbose_name=_lazy("Ко времени"),
    )
    # Delivery (PQ-032): where to bring the order, a snapshot taken at checkout.
    delivery_address = models.TextField(blank=True, verbose_name=_lazy("Адрес доставки"))
    delivery_latitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True, verbose_name=_lazy("Широта")
    )
    delivery_longitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True, verbose_name=_lazy("Долгота")
    )
    # Shipping by post (PQ-034): a snapshot taken at checkout, then the ТТН.
    shipping_carrier = models.CharField(
        max_length=16, choices=Carrier.choices, blank=True, verbose_name=_lazy("Служба")
    )
    shipping_destination = models.TextField(blank=True, verbose_name=_lazy("Куда отправить"))
    shipping_cost = models.CharField(
        max_length=16, choices=ShippingCost.choices, blank=True, verbose_name=_lazy("Пересылка")
    )
    tracking_number = models.CharField(max_length=40, blank=True, verbose_name=_lazy("ТТН"))
    payment_timing = models.CharField(
        max_length=16,
        choices=PaymentTiming.choices,
        blank=True,
        default="",
        verbose_name=_lazy("Когда оплата"),
    )
    payment_method = models.CharField(
        max_length=16,
        choices=PaymentMethod.choices,
        default=PaymentMethod.CASH,
        verbose_name=_lazy("Способ оплаты"),
    )
    comment = models.TextField(blank=True, verbose_name=_lazy("Комментарий"))
    subtotal_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name=_lazy("Сумма позиций"),
    )
    bonus_spent = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name=_lazy("Оплачено бонусами"),
    )
    discount_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name=_lazy("Скидка"),
    )
    total_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name=_lazy("Итого"),
    )
    accepted_at = models.DateTimeField(null=True, blank=True, verbose_name=_lazy("Принят"))
    received_at = models.DateTimeField(null=True, blank=True, verbose_name=_lazy("Получен"))
    paid_at = models.DateTimeField(null=True, blank=True, verbose_name=_lazy("Оплачен"))

    class Meta:
        verbose_name = _lazy("заказ")
        verbose_name_plural = _lazy("Заказы")
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
        verbose_name=_lazy("Заказ"),
    )
    from_status = models.CharField(max_length=16, blank=True, verbose_name=_lazy("Из статуса"))
    to_status = models.CharField(
        max_length=16,
        choices=Order.Status.choices,
        verbose_name=_lazy("В статус"),
    )
    changed_by = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="changed_order_statuses",
        verbose_name=_lazy("Кто изменил"),
    )
    note = models.CharField(max_length=255, blank=True, verbose_name=_lazy("Примечание"))

    class Meta:
        verbose_name = _lazy("смена статуса")
        verbose_name_plural = _lazy("История статусов")
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.order.public_id}: {self.from_status or '-'} -> {self.to_status}"


class Cart(PartnerBoundModel):
    """Draft basket for a guest before checkout into a final order."""

    class Status(models.TextChoices):
        ACTIVE = "active", _lazy("Активна")
        CHECKED_OUT = "checked_out", _lazy("Оформлена")
        ABANDONED = "abandoned", _lazy("Брошена")

    guest = models.ForeignKey(
        "users.GuestProfile",
        on_delete=models.CASCADE,
        related_name="carts",
        verbose_name=_lazy("Гость"),
    )
    table_session = models.ForeignKey(
        "tables.TableSession",
        on_delete=models.CASCADE,
        related_name="carts",
        null=True,
        blank=True,
        verbose_name=_lazy("Сессия стола"),
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
        verbose_name=_lazy("Статус"),
    )
    # The flow the guest chose: at the table or pickup. Checkout follows it.
    channel = models.CharField(
        max_length=16,
        choices=Order.Channel.choices,
        default=Order.Channel.TABLE,
        verbose_name=_lazy("Способ заказа"),
    )
    comment = models.TextField(blank=True, verbose_name=_lazy("Комментарий"))
    subtotal_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name=_lazy("Сумма позиций"),
    )
    total_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name=_lazy("Итого"),
    )
    checked_out_at = models.DateTimeField(null=True, blank=True, verbose_name=_lazy("Оформлена"))

    class Meta:
        verbose_name = _lazy("корзина")
        verbose_name_plural = _lazy("Корзины")
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
    """A line of a confirmed order: a menu item snapshot, or a service charge.

    Service lines (the delivery fee, PQ-032) have no menu item; they are
    ordinary order lines for totals, bills, bonuses and reports.
    """

    class Kind(models.TextChoices):
        ITEM = "item", _lazy("Позиция меню")
        DELIVERY_FEE = "delivery_fee", _lazy("Доставка")

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name=_lazy("Заказ"),
    )
    kind = models.CharField(
        max_length=16,
        choices=Kind.choices,
        default=Kind.ITEM,
        verbose_name=_lazy("Вид строки"),
    )
    menu_item = models.ForeignKey(
        "menu.MenuItem",
        on_delete=models.PROTECT,
        related_name="order_items",
        null=True,
        blank=True,
        verbose_name=_lazy("Позиция меню"),
    )
    item_name = models.CharField(max_length=255, verbose_name=_lazy("Позиция"))
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name=_lazy("Цена"))
    quantity = models.PositiveIntegerField(default=1, verbose_name=_lazy("Количество"))
    comment = models.CharField(max_length=255, blank=True, verbose_name=_lazy("Комментарий"))

    class Meta:
        verbose_name = _lazy("строка заказа")
        verbose_name_plural = _lazy("Строки заказов")
        ordering = ["created_at"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(kind="item", menu_item__isnull=False)
                    | models.Q(kind="delivery_fee", menu_item__isnull=True)
                ),
                name="order_item_menu_item_matches_kind",
            )
        ]

    def __str__(self) -> str:
        return f"{self.item_name} x{self.quantity}"


class CartItem(PartnerBoundModel):
    """Draft basket line for a menu item before the order is created."""

    cart = models.ForeignKey(
        Cart,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name=_lazy("Корзина"),
    )
    menu_item = models.ForeignKey(
        "menu.MenuItem",
        on_delete=models.PROTECT,
        related_name="cart_items",
        verbose_name=_lazy("Позиция меню"),
    )
    item_name = models.CharField(max_length=255, verbose_name=_lazy("Позиция"))
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name=_lazy("Цена"))
    quantity = models.PositiveIntegerField(default=1, verbose_name=_lazy("Количество"))
    comment = models.CharField(max_length=255, blank=True, verbose_name=_lazy("Комментарий"))

    class Meta:
        verbose_name = _lazy("строка корзины")
        verbose_name_plural = _lazy("Строки корзин")
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["cart", "menu_item"],
                name="unique_menu_item_per_cart",
            )
        ]

    def __str__(self) -> str:
        return f"{self.item_name} x{self.quantity}"
