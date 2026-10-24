import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models

from core.database.models import TimeStampedModel
from core.security.crypto import decrypt_text, encrypt_text

MIN_TRANSFER_WAIT_MINUTES = 15

_ROUTED_REPLY_LABEL_FIELDS = (
    "button_menu_label",
    "button_session_label",
    "button_cart_label",
    "button_checkout_label",
    "button_delivery_label",
    "button_pickup_label",
    "button_shipping_label",
    "button_help_label",
    "button_my_profile",
    "button_call_staff_label",
    "button_request_bill_label",
)


class Partner(TimeStampedModel):
    """Venue or business account that owns isolated data inside the platform."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"

    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    timezone = models.CharField(
        max_length=64,
        default="Europe/Kyiv",
        help_text="IANA timezone of the venue, e.g. Europe/Kyiv (AA-034).",
    )
    contact_phone = models.CharField(max_length=32, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)

    class Meta:
        ordering = ["name"]

    def clean(self) -> None:
        super().clean()
        # Reports and bonus rules build ZoneInfo(partner.timezone); an unknown
        # name would break them at runtime.
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValidationError({"timezone": "Unknown timezone name."}) from exc

    def __str__(self) -> str:
        return self.name


class BotInstance(TimeStampedModel):
    """Telegram bot configuration attached to a partner and used at runtime."""

    USERNAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,}$")
    TOKEN_PATTERN = re.compile(r"^\d+:[A-Za-z0-9_-]{20,}$")
    PLACEHOLDER_TOKEN_PREFIX = "seed-placeholder-token-"

    class Mode(models.TextChoices):
        POLLING = "polling", "Polling"
        WEBHOOK = "webhook", "Webhook"

    partner = models.ForeignKey(Partner, on_delete=models.CASCADE, related_name="bot_instances")
    display_name = models.CharField(max_length=255)
    username = models.CharField(max_length=255, unique=True)
    token_encrypted = models.TextField()
    mode = models.CharField(max_length=16, choices=Mode.choices, default=Mode.POLLING)
    webhook_url = models.URLField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["partner__name", "username"]
        constraints = [
            models.UniqueConstraint(
                fields=["partner", "username"],
                name="unique_bot_username_per_partner",
            )
        ]

    def __str__(self) -> str:
        return f"{self.partner.name} / @{self.username}"

    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.strip().removeprefix("@")

    @classmethod
    def looks_like_valid_username(cls, value: str) -> bool:
        return bool(cls.USERNAME_PATTERN.fullmatch(cls.normalize_username(value)))

    @classmethod
    def looks_like_valid_token(cls, value: str) -> bool:
        return bool(cls.TOKEN_PATTERN.fullmatch(value.strip()))

    @classmethod
    def is_placeholder_token(cls, value: str) -> bool:
        return value.strip().startswith(cls.PLACEHOLDER_TOKEN_PREFIX)

    @property
    def token(self) -> str:
        return decrypt_text(self.token_encrypted)

    def set_token(self, raw_token: str) -> None:
        self.token_encrypted = encrypt_text(raw_token)

    @property
    def has_usable_token(self) -> bool:
        try:
            raw_token = self.token
        except ImproperlyConfigured:
            return False
        return self.looks_like_valid_token(raw_token) and not self.is_placeholder_token(raw_token)

    @property
    def token_status_label(self) -> str:
        try:
            raw_token = self.token
        except ImproperlyConfigured:
            return "invalid"
        if self.is_placeholder_token(raw_token):
            return "placeholder"
        if self.looks_like_valid_token(raw_token):
            return "ready"
        return "invalid"


class PartnerBotSettings(TimeStampedModel):
    """Partner-level bot texts, button labels, and lightweight behavior flags."""

    partner = models.OneToOneField(
        Partner,
        on_delete=models.CASCADE,
        related_name="bot_settings",
    )
    module_menu_enabled = models.BooleanField(
        default=True,
        verbose_name="Меню",
        help_text="Категории и позиции. Нужен для заказов и быстрых продаж.",
    )
    module_tables_enabled = models.BooleanField(
        default=True,
        verbose_name="Столы и QR-сессии",
        help_text="Сценарий заказа за столом. Нужен для вызова персонала и запроса счёта.",
    )
    module_delivery_enabled = models.BooleanField(
        default=False,
        verbose_name="Доставка",
        help_text="Сценарий заказа на доставку. Включается независимо от столов.",
    )
    module_pickup_enabled = models.BooleanField(
        default=False,
        verbose_name="Самовывоз",
        help_text="Сценарий заказа на самовывоз. Включается независимо от столов.",
    )
    # Payment options per remote module (PQ-033): the owner turns on what the
    # bot offers; when both are on, the guest chooses at checkout.
    pickup_pay_transfer = models.BooleanField(
        default=True,
        verbose_name="Самовывоз: оплата переводом заранее",
        help_text="Гость переводит деньги на реквизиты, персонал подтверждает поступление.",
    )
    pickup_pay_on_receipt = models.BooleanField(
        default=False,
        verbose_name="Самовывоз: оплата при получении",
        help_text="Гость платит на месте наличными или картой, заказ сразу идёт в работу.",
    )
    delivery_pay_transfer = models.BooleanField(
        default=True,
        verbose_name="Доставка: оплата переводом заранее",
        help_text="Гость переводит деньги на реквизиты, персонал подтверждает поступление.",
    )
    delivery_pay_on_receipt = models.BooleanField(
        default=False,
        verbose_name="Доставка: оплата курьеру при получении",
        help_text="Гость платит курьеру наличными или картой, заказ сразу идёт в работу.",
    )
    module_shipping_enabled = models.BooleanField(
        default=False,
        verbose_name="Отправка почтой",
        help_text=(
            "Сценарий отправки заказа почтовой службой: гость выбирает службу и "
            "отделение, персонал отправляет и вводит ТТН. Без часов работы."
        ),
    )
    shipping_nova_poshta = models.BooleanField(default=True, verbose_name="Служба: Нова Пошта")
    shipping_ukrposhta = models.BooleanField(default=False, verbose_name="Служба: Укрпошта")
    shipping_meest = models.BooleanField(default=False, verbose_name="Служба: Meest")
    shipping_cost_carrier = models.BooleanField(
        default=True,
        verbose_name="Пересылка: по тарифу перевозчика",
        help_text="Получатель платит за пересылку на почте; в заказе её нет.",
    )
    shipping_cost_fixed = models.BooleanField(
        default=False,
        verbose_name="Пересылка: фиксированная цена в заказе",
        help_text="Цена ниже добавляется к заказу строкой «Пересылка».",
    )
    shipping_fee = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name="Цена пересылки, грн",
        help_text="Для варианта «фиксированная цена в заказе». 0 — бесплатно.",
    )
    shipping_free_from = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Бесплатная пересылка от суммы, грн",
        help_text="Для варианта «фиксированная цена в заказе». Пусто — всегда платная.",
    )
    shipping_min_order = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name="Минимальная сумма заказа на отправку, грн",
        help_text="0 — без минимума.",
    )
    shipping_pay_transfer = models.BooleanField(
        default=True,
        verbose_name="Отправка: оплата переводом заранее",
        help_text="Гость переводит деньги на реквизиты, персонал подтверждает поступление.",
    )
    shipping_pay_on_receipt = models.BooleanField(
        default=False,
        verbose_name="Отправка: наложенный платёж",
        help_text="Гость платит на почте при получении; заказ сразу идёт в работу.",
    )
    transfer_payment_details = models.TextField(
        blank=True,
        verbose_name="Реквизиты для оплаты переводом",
        help_text=(
            "Куда гость переводит деньги за заказы с предоплатой (самовывоз, "
            "доставка, отправка): номер карты или IBAN, получатель. Гость видит "
            "этот текст перед оплатой. Меняет только владелец."
        ),
    )
    # Unpaid remote orders (AA-037, PQ-036): limits against fake orders.
    remote_unpaid_orders_limit = models.PositiveSmallIntegerField(
        default=2,
        verbose_name="Неоплаченных заказов на гостя, не больше",
        help_text=(
            "Самовывоз, доставка, отправка: сколько открытых неоплаченных заказов "
            "может быть у одного гостя. 0 — без лимита."
        ),
    )
    transfer_wait_minutes = models.PositiveSmallIntegerField(
        default=60,
        verbose_name="Ждать перевод, минут",
        help_text=(
            "Заказ с оплатой переводом, оплату которого никто не подтвердил за это "
            "время, отменяется автоматически, бонусы возвращаются. Не меньше 15; "
            "0 — не отменять."
        ),
    )
    delivery_fee = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name="Стоимость доставки, грн",
        help_text="Добавляется к заказу отдельной строкой «Доставка». 0 — бесплатно.",
    )
    delivery_free_from = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Бесплатная доставка от суммы, грн",
        help_text="Если сумма заказа не меньше этой, доставка бесплатна. Пусто — всегда платная.",
    )
    delivery_min_order = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name="Минимальная сумма заказа на доставку, грн",
        help_text="0 — без минимума.",
    )
    module_cart_enabled = models.BooleanField(
        default=True,
        verbose_name="Корзина",
        help_text="Сбор позиций перед заказом. Требует меню и модуль заказов.",
    )
    module_orders_enabled = models.BooleanField(
        default=True,
        verbose_name="Заказы",
        help_text="Оформление и обработку заказов. Требует меню и хотя бы один "
        "сценарий: столы, доставку или самовывоз.",
    )
    module_billing_enabled = models.BooleanField(
        default=False,
        verbose_name="Счета и оплаты",
        help_text="Счета, оплаты и списание бонусов. Требует модуль заказов. "
        "Запрос счёта гостем доступен только при включённых столах.",
    )
    module_staff_call_enabled = models.BooleanField(
        default=False,
        verbose_name="Вызов персонала",
        help_text="Кнопка вызова официанта/бара/кальянщика. Требует столы.",
    )
    module_quick_sale_enabled = models.BooleanField(
        default=True,
        verbose_name="Быстрые продажи",
        help_text="Продажа на кассе по коду клиента. Требует меню.",
    )
    module_reports_enabled = models.BooleanField(
        default=True,
        verbose_name="Отчёт дня",
        help_text="Операционный отчёт в staff-боте.",
    )
    auto_close_table_session_after_payment = models.BooleanField(default=True)
    max_menu_items_per_category_message = models.PositiveSmallIntegerField(default=8)
    duplicate_request_cooldown_seconds = models.PositiveSmallIntegerField(default=45)
    allow_menu_without_session = models.BooleanField(default=True)
    staff_call_waiter_enabled = models.BooleanField(default=True)
    staff_call_bartender_enabled = models.BooleanField(default=True)
    staff_call_hookah_enabled = models.BooleanField(default=True)
    extra_config = models.JSONField(
        default=dict,
        blank=True,
        help_text="Reserved for future feature flags and partner-specific bot behavior.",
    )
    button_menu_label = models.CharField(max_length=64, default="Открыть меню")
    button_session_label = models.CharField(max_length=64, default="Мой стол")
    button_cart_label = models.CharField(max_length=64, default="Корзина")
    button_checkout_label = models.CharField(max_length=64, default="Оформить заказ")
    button_delivery_label = models.CharField(max_length=64, default="Заказать доставку")
    button_pickup_label = models.CharField(max_length=64, default="Самовывоз")
    button_shipping_label = models.CharField(max_length=64, default="Отправка почтой")
    button_help_label = models.CharField(max_length=64, default="Как заказать")
    button_my_profile = models.CharField(max_length=64, default="Мой профиль")
    button_profile_bonuses_label = models.CharField(max_length=64, default="Бонусы")
    button_call_staff_label = models.CharField(max_length=64, default="Позвать персонал")
    button_request_bill_label = models.CharField(max_length=64, default="Запросить счёт")
    button_call_waiter_label = models.CharField(max_length=64, default="Официант")
    button_call_bartender_label = models.CharField(max_length=64, default="Бар / касса")
    button_call_hookah_label = models.CharField(max_length=64, default="Кальянщик")
    welcome_message_template = models.TextField(
        blank=True,
        help_text="Variables: {partner_name}",
    )
    table_activated_message_template = models.TextField(
        blank=True,
        help_text="Variables: {partner_name}, {table_number}",
    )
    menu_header_template = models.TextField(
        blank=True,
        help_text="Variables: {partner_name}",
    )
    menu_requires_session_hint_template = models.TextField(
        blank=True,
        help_text="Shown when menu is visible but ordering still requires QR.",
    )
    menu_active_session_hint_template = models.TextField(
        blank=True,
        help_text="Variables: {table_number}",
    )
    menu_empty_message_template = models.TextField(blank=True)
    ordering_help_message_template = models.TextField(blank=True)
    cart_empty_message_template = models.TextField(blank=True)
    cart_cleared_message_template = models.TextField(blank=True)
    staff_call_prompt_template = models.TextField(
        blank=True,
        help_text="Shown when the guest opens the staff-call actions.",
    )
    staff_call_success_template = models.TextField(
        blank=True,
        help_text="Variables: {partner_name}, {table_number}, {call_target_label}",
    )
    request_bill_prompt_template = models.TextField(
        blank=True,
        help_text="Shown when the guest opens billing request options.",
    )
    request_bill_personal_success_template = models.TextField(
        blank=True,
        help_text="Variables: {partner_name}, {table_number}, {bill_public_id}",
    )
    request_bill_shared_success_template = models.TextField(
        blank=True,
        help_text="Variables: {partner_name}, {table_number}, {bill_public_id}",
    )
    request_bill_custom_success_template = models.TextField(
        blank=True,
        help_text="Variables: {partner_name}, {table_number}",
    )
    order_created_message_template = models.TextField(
        blank=True,
        help_text="Variables: {order_public_id}, {total_amount}, {status_display}",
    )

    class Meta:
        ordering = ["partner__name"]

    def __str__(self) -> str:
        return f"{self.partner.name} / bot settings"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, list[str]] = {}

        def add_error(field: str, message: str) -> None:
            errors.setdefault(field, []).append(message)

        if self.module_cart_enabled and not self.module_orders_enabled:
            add_error("module_cart_enabled", "Cart module requires orders module.")
        if self.module_cart_enabled and not self.module_menu_enabled:
            add_error("module_cart_enabled", "Cart module requires menu module.")
        if self.module_orders_enabled and not self.module_menu_enabled:
            add_error("module_orders_enabled", "Orders module requires menu module.")
        if self.module_orders_enabled and not (
            self.module_tables_enabled
            or self.module_delivery_enabled
            or self.module_pickup_enabled
            or self.module_shipping_enabled
        ):
            add_error(
                "module_orders_enabled",
                "Orders module requires tables, delivery, pickup or shipping module.",
            )
        for module, title, transfer_field, on_receipt_field in (
            ("module_pickup_enabled", "самовывоза", "pickup_pay_transfer", "pickup_pay_on_receipt"),
            (
                "module_delivery_enabled",
                "доставки",
                "delivery_pay_transfer",
                "delivery_pay_on_receipt",
            ),
            (
                "module_shipping_enabled",
                "отправки",
                "shipping_pay_transfer",
                "shipping_pay_on_receipt",
            ),
        ):
            if not getattr(self, module):
                continue
            if not (getattr(self, transfer_field) or getattr(self, on_receipt_field)):
                add_error(transfer_field, f"Для {title} включите хотя бы один способ оплаты.")
            if getattr(self, transfer_field) and not self.transfer_payment_details.strip():
                add_error(
                    "transfer_payment_details",
                    f"Для {title} с оплатой переводом укажите реквизиты.",
                )
        if self.module_shipping_enabled:
            if not (self.shipping_nova_poshta or self.shipping_ukrposhta or self.shipping_meest):
                add_error("shipping_nova_poshta", "Для отправки включите хотя бы одну службу.")
            if not (self.shipping_cost_carrier or self.shipping_cost_fixed):
                add_error(
                    "shipping_cost_carrier",
                    "Для отправки включите хотя бы один вариант оплаты пересылки.",
                )
        if 0 < self.transfer_wait_minutes < MIN_TRANSFER_WAIT_MINUTES:
            add_error(
                "transfer_wait_minutes",
                f"Не меньше {MIN_TRANSFER_WAIT_MINUTES} минут (или 0 — не отменять): "
                "перевод может идти не сразу.",
            )
        for field in (
            "delivery_fee",
            "delivery_free_from",
            "delivery_min_order",
            "shipping_fee",
            "shipping_free_from",
            "shipping_min_order",
        ):
            value = getattr(self, field)
            if value is not None and value < 0:
                add_error(field, "Сумма не может быть отрицательной.")
        if self.module_billing_enabled and not self.module_orders_enabled:
            add_error("module_billing_enabled", "Billing module requires orders module.")
        if self.module_staff_call_enabled and not self.module_tables_enabled:
            add_error("module_staff_call_enabled", "Staff call module requires tables module.")
        if self.module_quick_sale_enabled and not self.module_menu_enabled:
            add_error("module_quick_sale_enabled", "Quick sale module requires menu module.")
        if self.max_menu_items_per_category_message < 1:
            add_error(
                "max_menu_items_per_category_message",
                "At least one menu item must be shown per category message.",
            )

        labels_by_text: dict[str, str] = {}
        for field_name in _ROUTED_REPLY_LABEL_FIELDS:
            label = getattr(self, field_name).strip()
            if not label:
                add_error(field_name, "Action button label cannot be empty.")
                continue
            existing_field = labels_by_text.get(label)
            if existing_field is None:
                labels_by_text[label] = field_name
                continue
            add_error(
                existing_field,
                "Action button labels must be unique for each partner bot.",
            )
            add_error(
                field_name,
                "Action button labels must be unique for each partner bot.",
            )

        if errors:
            raise ValidationError(errors)


class OpeningHours(TimeStampedModel):
    """One opening interval of a venue on a weekday, in the venue's timezone.

    ``closes_at`` at or before ``opens_at`` means the interval ends after
    midnight (for example 18:00–03:00). A venue without intervals is treated
    as always open.
    """

    class Weekday(models.IntegerChoices):
        MONDAY = 0, "Понедельник"
        TUESDAY = 1, "Вторник"
        WEDNESDAY = 2, "Среда"
        THURSDAY = 3, "Четверг"
        FRIDAY = 4, "Пятница"
        SATURDAY = 5, "Суббота"
        SUNDAY = 6, "Воскресенье"

    partner = models.ForeignKey(
        Partner,
        on_delete=models.CASCADE,
        related_name="opening_hours",
    )
    weekday = models.PositiveSmallIntegerField(choices=Weekday.choices)
    opens_at = models.TimeField()
    closes_at = models.TimeField()

    class Meta:
        ordering = ["partner_id", "weekday", "opens_at"]
        verbose_name = "Часы работы"
        verbose_name_plural = "Часы работы"

    def __str__(self) -> str:
        return (
            f"{self.get_weekday_display()} {self.opens_at:%H:%M}–{self.closes_at:%H:%M}"
        )

    def clean(self) -> None:
        super().clean()
        if self.opens_at is not None and self.opens_at == self.closes_at:
            raise ValidationError({"closes_at": "Время закрытия совпадает со временем открытия."})
