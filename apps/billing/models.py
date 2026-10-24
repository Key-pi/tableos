import secrets
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q

from core.database.models import PartnerBoundModel


class Bill(PartnerBoundModel):
    """Payable check built from one or many orders without mutating the original order data."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        ISSUED = "issued", "Выставлен"
        PARTIALLY_PAID = "partially_paid", "Оплачен частично"
        PAID = "paid", "Оплачен"
        CANCELED = "canceled", "Аннулирован"

    class Kind(models.TextChoices):
        PERSONAL = "personal", "Личный"
        SHARED = "shared", "Общий"
        SPLIT = "split", "Деление"

    class Source(models.TextChoices):
        INTERNAL = "internal", "Botera Shop"
        POS = "pos", "Касса (POS)"

    public_id = models.CharField(max_length=12, editable=False, verbose_name="Номер")
    table = models.ForeignKey(
        "tables.Table",
        on_delete=models.PROTECT,
        related_name="bills",
        null=True,
        blank=True,
        verbose_name="Стол",
    )
    primary_guest = models.ForeignKey(
        "users.GuestProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="billing_bills",
        verbose_name="Гость счёта",
    )
    # Bills of a table belong to one table visit (ADR-0007); bills without a
    # table and bills created before visits existed have none.
    visit = models.ForeignKey(
        "tables.TableVisit",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="bills",
        verbose_name="Визит",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
        verbose_name="Статус",
    )
    kind = models.CharField(
        max_length=16,
        choices=Kind.choices,
        default=Kind.SHARED,
        verbose_name="Вид счёта",
    )
    source = models.CharField(
        max_length=16,
        choices=Source.choices,
        default=Source.INTERNAL,
        verbose_name="Источник",
    )
    label = models.CharField(max_length=255, blank=True, verbose_name="Подпись счёта")
    subtotal_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name="Сумма позиций",
    )
    discount_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name="Скидка",
    )
    bonus_spent_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name="Оплачено бонусами",
    )
    total_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name="Итого",
    )
    paid_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name="Оплачено",
    )
    external_ref = models.CharField(max_length=255, blank=True, verbose_name="Внешний номер")
    pos_tab_id = models.CharField(max_length=255, blank=True, verbose_name="Счёт в кассе (POS)")
    issued_at = models.DateTimeField(null=True, blank=True, verbose_name="Выставлен")
    closed_at = models.DateTimeField(null=True, blank=True, verbose_name="Закрыт")

    class Meta:
        verbose_name = "счёт"
        verbose_name_plural = "Счета"
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
        PERSONAL = "personal", "Личный счёт"
        SHARED = "shared", "Общий счёт"
        CUSTOM_SPLIT = "custom_split", "Особое деление"

    class Status(models.TextChoices):
        OPEN = "open", "Открыт"
        AUTO_PREPARED = "auto_prepared", "Счёт подготовлен"
        PROCESSED = "processed", "Обработан"
        CANCELED = "canceled", "Отменён"

    table = models.ForeignKey(
        "tables.Table",
        on_delete=models.PROTECT,
        related_name="billing_requests",
        verbose_name="Стол",
    )
    guest = models.ForeignKey(
        "users.GuestProfile",
        on_delete=models.PROTECT,
        related_name="billing_requests",
        verbose_name="Гость",
    )
    table_session = models.ForeignKey(
        "tables.TableSession",
        on_delete=models.PROTECT,
        related_name="billing_requests",
        verbose_name="Сессия стола",
    )
    request_type = models.CharField(
        max_length=32,
        choices=RequestType.choices,
        verbose_name="Что запросили",
    )
    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.OPEN,
        verbose_name="Статус",
    )
    bill = models.ForeignKey(
        "billing.Bill",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="billing_requests",
        verbose_name="Счёт",
    )
    note = models.CharField(max_length=255, blank=True, verbose_name="Примечание")
    processed_at = models.DateTimeField(null=True, blank=True, verbose_name="Обработан")

    class Meta:
        verbose_name = "запрос счёта"
        verbose_name_plural = "Запросы счёта"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.partner.name} / billing request / {self.request_type}"


class BillOrder(PartnerBoundModel):
    """Join model that tracks which orders were grouped into one bill."""

    bill = models.ForeignKey(
        Bill,
        on_delete=models.CASCADE,
        related_name="bill_orders",
        verbose_name="Счёт",
    )
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.PROTECT,
        related_name="bill_links",
        verbose_name="Заказ",
    )

    class Meta:
        verbose_name = "заказ в счёте"
        verbose_name_plural = "Заказы в счетах"
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

    bill = models.ForeignKey(
        Bill,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="Счёт",
    )
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.PROTECT,
        related_name="billing_items",
        verbose_name="Заказ",
    )
    order_item = models.ForeignKey(
        "orders.OrderItem",
        on_delete=models.PROTECT,
        related_name="billing_items",
        verbose_name="Строка заказа",
    )
    item_name = models.CharField(max_length=255, verbose_name="Позиция")
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Цена")
    quantity = models.PositiveIntegerField(default=1, verbose_name="Количество")
    line_total = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name="Сумма строки",
    )
    comment = models.CharField(max_length=255, blank=True, verbose_name="Комментарий")
    # Removed lines stay for history; only active lines count and allocate.
    is_active = models.BooleanField(default=True, verbose_name="Активен")
    removed_at = models.DateTimeField(null=True, blank=True, verbose_name="Убрана")
    removed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="removed_bill_items",
        verbose_name="Кто убрал",
    )

    class Meta:
        verbose_name = "строка счёта"
        verbose_name_plural = "Строки счетов"
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["partner", "order_item"],
                condition=Q(is_active=True),
                name="unique_active_order_item_bill_allocation",
            )
        ]

    def __str__(self) -> str:
        return f"{self.bill.public_id} / {self.item_name} x{self.quantity}"

    def save(self, *args, **kwargs):
        self.line_total = (Decimal(self.unit_price) * self.quantity).quantize(Decimal("0.01"))
        return super().save(*args, **kwargs)


class Payment(PartnerBoundModel):
    """Payment attempt or completed payment attached to one bill."""

    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает"
        AUTHORIZED = "authorized", "Авторизована"
        PAID = "paid", "Проведена"
        FAILED = "failed", "Ошибка"
        REFUNDED = "refunded", "Возвращена"
        CANCELED = "canceled", "Отменена"

    class Method(models.TextChoices):
        CASH = "cash", "Наличные"
        TERMINAL = "terminal", "Карта (терминал)"
        TRANSFER = "transfer", "Перевод"
        ONLINE = "online", "Онлайн"
        MIXED = "mixed", "Смешанная"
        BONUSES = "bonuses", "Бонусы"
        # Cash on delivery by post (PQ-034): the carrier passes the money on.
        COD = "cod", "Наложенный платёж"

    bill = models.ForeignKey(
        Bill,
        on_delete=models.CASCADE,
        related_name="payments",
        verbose_name="Счёт",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        verbose_name="Статус",
    )
    method = models.CharField(
        max_length=16,
        choices=Method.choices,
        default=Method.CASH,
        verbose_name="Способ",
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Сумма")
    provider_code = models.CharField(max_length=64, blank=True, verbose_name="Платёжный провайдер")
    external_payment_id = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Номер платежа у провайдера",
    )
    comment = models.CharField(max_length=255, blank=True, verbose_name="Комментарий")
    paid_at = models.DateTimeField(null=True, blank=True, verbose_name="Оплачен")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="billing_payments",
        verbose_name="Кто создал",
    )

    class Meta:
        verbose_name = "оплата"
        verbose_name_plural = "Оплаты"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.bill.public_id} / {self.method} / {self.amount}"


class BillAdjustment(PartnerBoundModel):
    """Append-only record of a correction made to a bill by an authorised person."""

    class Kind(models.TextChoices):
        REMOVE_LINE = "remove_line", "Строка убрана"
        ORDER_CANCELED = "order_canceled", "Заказ отменён"
        VOID = "void", "Счёт аннулирован"

    class ReasonCode(models.TextChoices):
        INPUT_ERROR = "input_error", "Ошибка ввода"
        GUEST_REFUSED = "guest_refused", "Отказ гостя"
        COMPLIMENT = "compliment", "Комплимент заведения"
        PAYMENT_NOT_RECEIVED = "payment_not_received", "Оплата не поступила"
        OTHER = "other", "Другое"

    bill = models.ForeignKey(
        Bill,
        on_delete=models.PROTECT,
        related_name="adjustments",
        verbose_name="Счёт",
    )
    kind = models.CharField(max_length=24, choices=Kind.choices, verbose_name="Что изменено")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bill_adjustments",
        verbose_name="Кто изменил",
    )
    reason_code = models.CharField(
        max_length=24,
        choices=ReasonCode.choices,
        verbose_name="Причина",
    )
    reason_text = models.CharField(max_length=255, blank=True, verbose_name="Комментарий к причине")
    total_before = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Итог до")
    total_after = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Итог после")
    payload = models.JSONField(default=dict, blank=True, verbose_name="Данные изменения")

    class Meta:
        verbose_name = "изменение счёта"
        verbose_name_plural = "Изменения счетов"
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.bill.public_id} / {self.kind} / {self.reason_code}"
