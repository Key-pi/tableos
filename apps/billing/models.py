import secrets
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q

from core.database.models import PartnerBoundModel
from core.i18n import _
from core.i18n import gettext_lazy as _lazy


class Bill(PartnerBoundModel):
    """Payable check built from one or many orders without mutating the original order data."""

    class Status(models.TextChoices):
        DRAFT = "draft", _lazy("Черновик")
        ISSUED = "issued", _lazy("Выставлен")
        PARTIALLY_PAID = "partially_paid", _lazy("Оплачен частично")
        PAID = "paid", _lazy("Оплачен")
        CANCELED = "canceled", _lazy("Аннулирован")

    class Kind(models.TextChoices):
        PERSONAL = "personal", _lazy("Личный")
        SHARED = "shared", _lazy("Общий")
        SPLIT = "split", _lazy("Деление")

    class Source(models.TextChoices):
        INTERNAL = "internal", "Botera Shop"
        POS = "pos", _lazy("Касса (POS)")

    public_id = models.CharField(max_length=12, editable=False, verbose_name=_lazy("Номер"))
    table = models.ForeignKey(
        "tables.Table",
        on_delete=models.PROTECT,
        related_name="bills",
        null=True,
        blank=True,
        verbose_name=_lazy("Стол"),
    )
    primary_guest = models.ForeignKey(
        "users.GuestProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="billing_bills",
        verbose_name=_lazy("Гость счёта"),
    )
    # Bills of a table belong to one table visit (ADR-0007); bills without a
    # table and bills created before visits existed have none.
    visit = models.ForeignKey(
        "tables.TableVisit",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="bills",
        verbose_name=_lazy("Визит"),
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
        verbose_name=_lazy("Статус"),
    )
    kind = models.CharField(
        max_length=16,
        choices=Kind.choices,
        default=Kind.SHARED,
        verbose_name=_lazy("Вид счёта"),
    )
    source = models.CharField(
        max_length=16,
        choices=Source.choices,
        default=Source.INTERNAL,
        verbose_name=_lazy("Источник"),
    )
    label = models.CharField(max_length=255, blank=True, verbose_name=_lazy("Подпись счёта"))
    subtotal_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name=_lazy("Сумма позиций"),
    )
    discount_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name=_lazy("Скидка"),
    )
    bonus_spent_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name=_lazy("Оплачено бонусами"),
    )
    total_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name=_lazy("Итого"),
    )
    paid_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name=_lazy("Оплачено"),
    )
    # Money given back to guests by refunds (PQ-037); payments stay as they were.
    refunded_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name=_lazy("Возвращено"),
    )
    external_ref = models.CharField(max_length=255, blank=True, verbose_name=_lazy("Внешний номер"))
    pos_tab_id = models.CharField(max_length=255, blank=True, verbose_name=_lazy(
        "Счёт в кассе (POS)",
    ))
    issued_at = models.DateTimeField(null=True, blank=True, verbose_name=_lazy("Выставлен"))
    closed_at = models.DateTimeField(null=True, blank=True, verbose_name=_lazy("Закрыт"))

    class Meta:
        verbose_name = _lazy("счёт")
        verbose_name_plural = _lazy("Счета")
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
        PERSONAL = "personal", _lazy("Личный счёт")
        SHARED = "shared", _lazy("Общий счёт")
        CUSTOM_SPLIT = "custom_split", _lazy("Особое деление")

    class Status(models.TextChoices):
        OPEN = "open", _lazy("Открыт")
        AUTO_PREPARED = "auto_prepared", _lazy("Счёт подготовлен")
        PROCESSED = "processed", _lazy("Обработан")
        CANCELED = "canceled", _lazy("Отменён")

    table = models.ForeignKey(
        "tables.Table",
        on_delete=models.PROTECT,
        related_name="billing_requests",
        verbose_name=_lazy("Стол"),
    )
    guest = models.ForeignKey(
        "users.GuestProfile",
        on_delete=models.PROTECT,
        related_name="billing_requests",
        verbose_name=_lazy("Гость"),
    )
    table_session = models.ForeignKey(
        "tables.TableSession",
        on_delete=models.PROTECT,
        related_name="billing_requests",
        verbose_name=_lazy("Сессия стола"),
    )
    request_type = models.CharField(
        max_length=32,
        choices=RequestType.choices,
        verbose_name=_lazy("Что запросили"),
    )
    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.OPEN,
        verbose_name=_lazy("Статус"),
    )
    bill = models.ForeignKey(
        "billing.Bill",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="billing_requests",
        verbose_name=_lazy("Счёт"),
    )
    note = models.CharField(max_length=255, blank=True, verbose_name=_lazy("Примечание"))
    processed_at = models.DateTimeField(null=True, blank=True, verbose_name=_lazy("Обработан"))

    class Meta:
        verbose_name = _lazy("запрос счёта")
        verbose_name_plural = _lazy("Запросы счёта")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.partner.name} / billing request / {self.request_type}"


class BillOrder(PartnerBoundModel):
    """Join model that tracks which orders were grouped into one bill."""

    bill = models.ForeignKey(
        Bill,
        on_delete=models.CASCADE,
        related_name="bill_orders",
        verbose_name=_lazy("Счёт"),
    )
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.PROTECT,
        related_name="bill_links",
        verbose_name=_lazy("Заказ"),
    )

    class Meta:
        verbose_name = _lazy("заказ в счёте")
        verbose_name_plural = _lazy("Заказы в счетах")
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
        verbose_name=_lazy("Счёт"),
    )
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.PROTECT,
        related_name="billing_items",
        verbose_name=_lazy("Заказ"),
    )
    order_item = models.ForeignKey(
        "orders.OrderItem",
        on_delete=models.PROTECT,
        related_name="billing_items",
        verbose_name=_lazy("Строка заказа"),
    )
    item_name = models.CharField(max_length=255, verbose_name=_lazy("Позиция"))
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name=_lazy("Цена"))
    quantity = models.PositiveIntegerField(default=1, verbose_name=_lazy("Количество"))
    line_total = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name=_lazy("Сумма строки"),
    )
    comment = models.CharField(max_length=255, blank=True, verbose_name=_lazy("Комментарий"))
    # Removed lines stay for history; only active lines count and allocate.
    is_active = models.BooleanField(default=True, verbose_name=_lazy("Активен"))
    removed_at = models.DateTimeField(null=True, blank=True, verbose_name=_lazy("Убрана"))
    removed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="removed_bill_items",
        verbose_name=_lazy("Кто убрал"),
    )

    class Meta:
        verbose_name = _lazy("строка счёта")
        verbose_name_plural = _lazy("Строки счетов")
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
        PENDING = "pending", _lazy("Ожидает")
        AUTHORIZED = "authorized", _lazy("Авторизована")
        PAID = "paid", _lazy("Проведена")
        FAILED = "failed", _lazy("Ошибка")
        REFUNDED = "refunded", _lazy("Возвращена")
        CANCELED = "canceled", _lazy("Отменена")

    class Method(models.TextChoices):
        CASH = "cash", _lazy("Наличные")
        TERMINAL = "terminal", _lazy("Карта (терминал)")
        TRANSFER = "transfer", _lazy("Перевод")
        ONLINE = "online", _lazy("Онлайн")
        MIXED = "mixed", _lazy("Смешанная")
        BONUSES = "bonuses", _lazy("Бонусы")
        # Cash on delivery by post (PQ-034): the carrier passes the money on.
        COD = "cod", _lazy("Наложенный платёж")

    bill = models.ForeignKey(
        Bill,
        on_delete=models.CASCADE,
        related_name="payments",
        verbose_name=_lazy("Счёт"),
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        verbose_name=_lazy("Статус"),
    )
    method = models.CharField(
        max_length=16,
        choices=Method.choices,
        default=Method.CASH,
        verbose_name=_lazy("Способ"),
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name=_lazy("Сумма"))
    provider_code = models.CharField(max_length=64, blank=True, verbose_name=_lazy(
        "Платёжный провайдер",
    ))
    external_payment_id = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_lazy("Номер платежа у провайдера"),
    )
    comment = models.CharField(max_length=255, blank=True, verbose_name=_lazy("Комментарий"))
    paid_at = models.DateTimeField(null=True, blank=True, verbose_name=_lazy("Оплачен"))
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="billing_payments",
        verbose_name=_lazy("Кто создал"),
    )

    class Meta:
        verbose_name = _lazy("оплата")
        verbose_name_plural = _lazy("Оплаты")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.bill.public_id} / {self.method} / {self.amount}"


class BillAdjustment(PartnerBoundModel):
    """Append-only record of a correction made to a bill by an authorised person."""

    class Kind(models.TextChoices):
        REMOVE_LINE = "remove_line", _lazy("Строка убрана")
        ORDER_CANCELED = "order_canceled", _lazy("Заказ отменён")
        VOID = "void", _lazy("Счёт аннулирован")

    class ReasonCode(models.TextChoices):
        INPUT_ERROR = "input_error", _lazy("Ошибка ввода")
        GUEST_REFUSED = "guest_refused", _lazy("Отказ гостя")
        COMPLIMENT = "compliment", _lazy("Комплимент заведения")
        PAYMENT_NOT_RECEIVED = "payment_not_received", _lazy("Оплата не поступила")
        OTHER = "other", _lazy("Другое")

    bill = models.ForeignKey(
        Bill,
        on_delete=models.PROTECT,
        related_name="adjustments",
        verbose_name=_lazy("Счёт"),
    )
    kind = models.CharField(max_length=24, choices=Kind.choices, verbose_name=_lazy("Что изменено"))
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bill_adjustments",
        verbose_name=_lazy("Кто изменил"),
    )
    reason_code = models.CharField(
        max_length=24,
        choices=ReasonCode.choices,
        verbose_name=_lazy("Причина"),
    )
    reason_text = models.CharField(max_length=255, blank=True, verbose_name=_lazy(
        "Комментарий к причине",
    ))
    total_before = models.DecimalField(max_digits=10, decimal_places=2, verbose_name=_lazy(
        "Итог до",
    ))
    total_after = models.DecimalField(max_digits=10, decimal_places=2, verbose_name=_lazy(
        "Итог после",
    ))
    payload = models.JSONField(default=dict, blank=True, verbose_name=_lazy("Данные изменения"))

    class Meta:
        verbose_name = _lazy("изменение счёта")
        verbose_name_plural = _lazy("Изменения счетов")
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.bill.public_id} / {self.kind} / {self.reason_code}"


class Refund(PartnerBoundModel):
    """Money (and bonuses) given back for lines of a paid bill (PQ-037).

    Append-only: the bill's payments are never edited; each refund records
    the lines it covers, the money returned to the guest by hand, the
    bonuses returned and the cashback taken back.
    """

    class ReasonCode(models.TextChoices):
        OUT_OF_STOCK = "out_of_stock", _lazy("Нет в наличии")
        QUALITY = "quality", _lazy("Претензия к качеству")
        GUEST_REFUSED = "guest_refused", _lazy("Отказ гостя")
        INPUT_ERROR = "input_error", _lazy("Ошибка ввода")
        OTHER = "other", _lazy("Другое")

    class Method(models.TextChoices):
        # How the money went back to the guest; done by hand, outside the bot.
        CASH = "cash", _lazy("Наличными")
        TRANSFER = "transfer", _lazy("Переводом на карту")
        TERMINAL = "terminal", _lazy("На карту через терминал")
        NONE = "none", _lazy("Деньги не возвращались (только бонусы)")

    bill = models.ForeignKey(
        Bill, on_delete=models.PROTECT, related_name="refunds", verbose_name=_lazy("Счёт")
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bill_refunds",
        verbose_name=_lazy("Кто вернул"),
    )
    reason_code = models.CharField(
        max_length=24, choices=ReasonCode.choices, verbose_name=_lazy("Причина")
    )
    reason_text = models.CharField(max_length=255, blank=True, verbose_name=_lazy("Комментарий"))
    method = models.CharField(max_length=16, choices=Method.choices, verbose_name=_lazy(
        "Как вернули",
    ))
    is_full = models.BooleanField(default=False, verbose_name=_lazy("Полный возврат"))
    money_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, verbose_name=_lazy("Возвращено денег")
    )
    bonus_returned = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, verbose_name=_lazy("Возвращено бонусов")
    )
    cashback_revoked = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, verbose_name=_lazy("Снято кешбэка")
    )
    cashback_not_revoked = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name=_lazy("Кешбэк не снят (гость уже потратил)"),
    )

    class Meta:
        verbose_name = _lazy("возврат")
        verbose_name_plural = _lazy("Возвраты")
        ordering = ["created_at"]

    def __str__(self) -> str:
        return _(
            "{public_id} / возврат {money_amount}",
            public_id=self.bill.public_id,
            money_amount=self.money_amount,
        )


class RefundLine(PartnerBoundModel):
    """One refunded bill line; a line is refunded at most once."""

    refund = models.ForeignKey(
        Refund, on_delete=models.PROTECT, related_name="lines", verbose_name=_lazy("Возврат")
    )
    bill_item = models.OneToOneField(
        BillItem, on_delete=models.PROTECT, related_name="refund_line", verbose_name=_lazy(
            "Строка счёта",
        )
    )
    money_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, verbose_name=_lazy("Деньги")
    )
    bonus_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, verbose_name=_lazy("Бонусы")
    )

    class Meta:
        verbose_name = _lazy("строка возврата")
        verbose_name_plural = _lazy("Строки возвратов")
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.bill_item} / {self.money_amount}"
