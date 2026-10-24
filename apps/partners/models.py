import hashlib
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models

from core.database.models import TimeStampedModel
from core.i18n import LANGUAGE_NAMES, LANGUAGES, default_language
from core.security.crypto import decrypt_text, encrypt_text

MIN_TRANSFER_WAIT_MINUTES = 15
LANGUAGE_CHOICES = [(code, LANGUAGE_NAMES[code]) for code in LANGUAGES]
GUEST_LANGUAGE_FIELDS = {code: f"language_{code}" for code in LANGUAGES}

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
        DRAFT = "draft", "Черновик"
        ACTIVE = "active", "Работает"
        SUSPENDED = "suspended", "Приостановлен"

    name = models.CharField(max_length=255, verbose_name="Название заведения")
    slug = models.SlugField(unique=True, verbose_name="Код (slug)")
    timezone = models.CharField(
        max_length=64,
        default="Europe/Kyiv",
        help_text="Часовой пояс заведения (IANA), например Europe/Kyiv.",
        verbose_name="Часовой пояс",
    )
    contact_phone = models.CharField(max_length=32, blank=True, verbose_name="Контактный телефон")
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
        verbose_name="Статус",
    )

    class Meta:
        verbose_name = "заведение"
        verbose_name_plural = "Заведения"
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

    partner = models.ForeignKey(
        Partner,
        on_delete=models.CASCADE,
        related_name="bot_instances",
        verbose_name="Заведение",
    )
    display_name = models.CharField(max_length=255, verbose_name="Название бота")
    username = models.CharField(max_length=255, unique=True, verbose_name="Имя бота (@)")
    token_encrypted = models.TextField(verbose_name="Токен (зашифрован)")
    mode = models.CharField(
        max_length=16,
        choices=Mode.choices,
        default=Mode.POLLING,
        verbose_name="Режим",
    )
    webhook_url = models.URLField(blank=True, verbose_name="Адрес webhook")
    is_active = models.BooleanField(default=True, verbose_name="Включён")

    class Meta:
        verbose_name = "бот"
        verbose_name_plural = "Боты"
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
        verbose_name="Заведение",
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
    auto_close_table_session_after_payment = models.BooleanField(
        default=True,
        verbose_name="Закрывать стол после полной оплаты",
    )
    max_menu_items_per_category_message = models.PositiveSmallIntegerField(
        default=8,
        verbose_name="Позиций меню в одном сообщении",
    )
    duplicate_request_cooldown_seconds = models.PositiveSmallIntegerField(
        default=45,
        verbose_name="Пауза между одинаковыми запросами, с",
    )
    allow_menu_without_session = models.BooleanField(
        default=True,
        verbose_name="Меню без сканирования стола",
    )
    staff_call_waiter_enabled = models.BooleanField(default=True, verbose_name="Вызов: официант")
    staff_call_bartender_enabled = models.BooleanField(default=True, verbose_name="Вызов: бармен")
    staff_call_hookah_enabled = models.BooleanField(default=True, verbose_name="Вызов: кальянщик")
    extra_config = models.JSONField(
        default=dict,
        blank=True,
        help_text="Резерв для будущих флагов и особых настроек бота заведения.",
        verbose_name="Доп. настройки (JSON)",
    )
    # Languages (PQ-040): the guest sees the language of their Telegram when
    # it is enabled here, otherwise the main one, and may switch in the profile.
    default_language = models.CharField(
        max_length=8,
        choices=LANGUAGE_CHOICES,
        default=default_language,
        verbose_name="Основной язык бота",
        help_text="Для гостей, чей язык Telegram не включён ниже. Должен быть включён.",
    )
    language_uk = models.BooleanField(default=True, verbose_name="Язык гостей: украинский")
    language_ru = models.BooleanField(default=True, verbose_name="Язык гостей: русский")
    language_en = models.BooleanField(default=True, verbose_name="Язык гостей: английский")
    button_menu_label = models.CharField(
        max_length=64,
        default="Открыть меню",
        verbose_name="Кнопка «Меню»",
    )
    button_session_label = models.CharField(
        max_length=64,
        default="Мой стол",
        verbose_name="Кнопка «Мой стол»",
    )
    button_cart_label = models.CharField(
        max_length=64,
        default="Корзина",
        verbose_name="Кнопка «Корзина»",
    )
    button_checkout_label = models.CharField(
        max_length=64,
        default="Оформить заказ",
        verbose_name="Кнопка «Оформить заказ»",
    )
    button_delivery_label = models.CharField(
        max_length=64,
        default="Заказать доставку",
        verbose_name="Кнопка «Доставка»",
    )
    button_pickup_label = models.CharField(
        max_length=64,
        default="Самовывоз",
        verbose_name="Кнопка «Самовывоз»",
    )
    button_shipping_label = models.CharField(
        max_length=64,
        default="Отправка почтой",
        verbose_name="Кнопка «Отправка почтой»",
    )
    button_help_label = models.CharField(
        max_length=64,
        default="Как заказать",
        verbose_name="Кнопка «Как заказать»",
    )
    button_my_profile = models.CharField(
        max_length=64,
        default="Мой профиль",
        verbose_name="Кнопка «Мой профиль»",
    )
    button_profile_bonuses_label = models.CharField(
        max_length=64,
        default="Бонусы",
        verbose_name="Кнопка «Бонусы»",
    )
    button_call_staff_label = models.CharField(
        max_length=64,
        default="Позвать персонал",
        verbose_name="Кнопка «Вызвать персонал»",
    )
    button_request_bill_label = models.CharField(
        max_length=64,
        default="Запросить счёт",
        verbose_name="Кнопка «Запросить счёт»",
    )
    button_call_waiter_label = models.CharField(
        max_length=64,
        default="Официант",
        verbose_name="Кнопка «Официант»",
    )
    button_call_bartender_label = models.CharField(
        max_length=64,
        default="Бар / касса",
        verbose_name="Кнопка «Бармен»",
    )
    button_call_hookah_label = models.CharField(
        max_length=64,
        default="Кальянщик",
        verbose_name="Кнопка «Кальянщик»",
    )
    welcome_message_template = models.TextField(
        blank=True,
        help_text="Переменные: {partner_name}",
        verbose_name="Приветствие",
    )
    table_activated_message_template = models.TextField(
        blank=True,
        help_text="Переменные: {partner_name}, {table_number}",
        verbose_name="Стол открыт",
    )
    menu_header_template = models.TextField(
        blank=True,
        help_text="Переменные: {partner_name}",
        verbose_name="Заголовок меню",
    )
    menu_requires_session_hint_template = models.TextField(
        blank=True,
        help_text="Показывается, когда меню видно, но для заказа за столом нужно сканировать QR.",
        verbose_name="Подсказка: меню после сканирования стола",
    )
    menu_active_session_hint_template = models.TextField(
        blank=True,
        help_text="Переменные: {table_number}",
        verbose_name="Подсказка: меню за столом",
    )
    menu_empty_message_template = models.TextField(blank=True, verbose_name="Меню пустое")
    ordering_help_message_template = models.TextField(
        blank=True,
        verbose_name="Как заказать (текст)",
    )
    cart_empty_message_template = models.TextField(blank=True, verbose_name="Корзина пустая")
    cart_cleared_message_template = models.TextField(blank=True, verbose_name="Корзина очищена")
    staff_call_prompt_template = models.TextField(
        blank=True,
        help_text="Показывается, когда гость открывает вызов персонала.",
        verbose_name="Вызов персонала: вопрос",
    )
    staff_call_success_template = models.TextField(
        blank=True,
        help_text="Переменные: {partner_name}, {table_number}, {call_target_label}",
        verbose_name="Вызов персонала: принят",
    )
    request_bill_prompt_template = models.TextField(
        blank=True,
        help_text="Показывается, когда гость открывает запрос счёта.",
        verbose_name="Запрос счёта: вопрос",
    )
    request_bill_personal_success_template = models.TextField(
        blank=True,
        help_text="Переменные: {partner_name}, {table_number}, {bill_public_id}",
        verbose_name="Запрос личного счёта: принят",
    )
    request_bill_shared_success_template = models.TextField(
        blank=True,
        help_text="Переменные: {partner_name}, {table_number}, {bill_public_id}",
        verbose_name="Запрос общего счёта: принят",
    )
    request_bill_custom_success_template = models.TextField(
        blank=True,
        help_text="Переменные: {partner_name}, {table_number}",
        verbose_name="Запрос особого деления: принят",
    )
    order_created_message_template = models.TextField(
        blank=True,
        help_text="Переменные: {order_public_id}, {total_amount}, {status_display}",
        verbose_name="Заказ принят (текст)",
    )

    class Meta:
        verbose_name = "настройки бота"
        verbose_name_plural = "Настройки ботов"
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
        if not getattr(self, GUEST_LANGUAGE_FIELDS.get(self.default_language, ""), False):
            add_error("default_language", "Основной язык должен быть среди включённых языков.")
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

    class Scope(models.TextChoices):
        # Modules with their own hours; empty = the venue's common hours,
        # used by a module that has none of its own.
        COMMON = "", "Общие (для всех модулей)"
        PICKUP = "pickup", "Самовывоз"
        DELIVERY = "delivery", "Доставка"

    partner = models.ForeignKey(
        Partner,
        on_delete=models.CASCADE,
        related_name="opening_hours",
        verbose_name="Заведение",
    )
    scope = models.CharField(
        max_length=16,
        choices=Scope.choices,
        default=Scope.COMMON,
        blank=True,
        verbose_name="Для чего",
        help_text=(
            "Свои часы модуля. Если у модуля своих часов нет, действуют общие; "
            "если нет и общих — модуль работает всегда."
        ),
    )
    weekday = models.PositiveSmallIntegerField(choices=Weekday.choices, verbose_name="День недели")
    opens_at = models.TimeField(verbose_name="Открытие")
    closes_at = models.TimeField(verbose_name="Закрытие")

    class Meta:
        ordering = ["partner_id", "scope", "weekday", "opens_at"]
        verbose_name = "Часы работы"
        verbose_name_plural = "Часы работы"

    def __str__(self) -> str:
        prefix = f"{self.get_scope_display()}: " if self.scope else ""
        return (
            f"{prefix}{self.get_weekday_display()} "
            f"{self.opens_at:%H:%M}–{self.closes_at:%H:%M}"
        )

    def clean(self) -> None:
        super().clean()
        if self.opens_at is not None and self.opens_at == self.closes_at:
            raise ValidationError({"closes_at": "Время закрытия совпадает со временем открытия."})


def text_key(msgid: str) -> str:
    return hashlib.sha1(msgid.encode("utf-8")).hexdigest()


class BotText(TimeStampedModel):
    """The owner's own wording of a bot text in one language (PQ-040).

    ``msgid`` is the catalog text (Russian, as in the code); the bot shows
    ``text`` instead of the catalog translation for this partner and language.
    """

    partner = models.ForeignKey(
        Partner,
        on_delete=models.CASCADE,
        related_name="bot_texts",
        verbose_name="Заведение",
    )
    language = models.CharField(max_length=8, choices=LANGUAGE_CHOICES, verbose_name="Язык")
    msgid = models.TextField(verbose_name="Текст в каталоге")
    key = models.CharField(max_length=40, editable=False, verbose_name="Ключ")
    text = models.TextField(verbose_name="Свой текст")

    class Meta:
        ordering = ["partner_id", "language", "msgid"]
        verbose_name = "текст бота"
        verbose_name_plural = "Тексты бота"
        constraints = [
            models.UniqueConstraint(
                fields=["partner", "language", "key"], name="uniq_bot_text_per_language"
            )
        ]

    def __str__(self) -> str:
        return f"{self.language}: {self.text[:60]}"

    def save(self, *args, **kwargs):
        self.key = text_key(self.msgid)
        super().save(*args, **kwargs)
