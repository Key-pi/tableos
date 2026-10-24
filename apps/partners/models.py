import hashlib
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models

from core.database.models import TimeStampedModel
from core.i18n import LANGUAGE_NAMES, LANGUAGES, _, default_language
from core.i18n import gettext_lazy as _lazy
from core.security.crypto import decrypt_text, encrypt_text

MIN_TRANSFER_WAIT_MINUTES = 15
LANGUAGE_CHOICES = [(code, LANGUAGE_NAMES[code]) for code in LANGUAGES]
GUEST_LANGUAGE_FIELDS = {code: f"language_{code}" for code in LANGUAGES}


class Partner(TimeStampedModel):
    """Venue or business account that owns isolated data inside the platform."""

    class Status(models.TextChoices):
        DRAFT = "draft", _lazy("Черновик")
        ACTIVE = "active", _lazy("Работает")
        SUSPENDED = "suspended", _lazy("Приостановлен")

    name = models.CharField(max_length=255, verbose_name=_lazy("Название заведения"))
    slug = models.SlugField(unique=True, verbose_name=_lazy("Код (slug)"))
    timezone = models.CharField(
        max_length=64,
        default="Europe/Kyiv",
        help_text=_lazy("Часовой пояс заведения (IANA), например Europe/Kyiv."),
        verbose_name=_lazy("Часовой пояс"),
    )
    contact_phone = models.CharField(max_length=32, blank=True, verbose_name=_lazy(
        "Контактный телефон",
    ))
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
        verbose_name=_lazy("Статус"),
    )

    class Meta:
        verbose_name = _lazy("заведение")
        verbose_name_plural = _lazy("Заведения")
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
        verbose_name=_lazy("Заведение"),
    )
    display_name = models.CharField(max_length=255, verbose_name=_lazy("Название бота"))
    username = models.CharField(max_length=255, unique=True, verbose_name=_lazy("Имя бота (@)"))
    token_encrypted = models.TextField(verbose_name=_lazy("Токен (зашифрован)"))
    mode = models.CharField(
        max_length=16,
        choices=Mode.choices,
        default=Mode.POLLING,
        verbose_name=_lazy("Режим"),
    )
    webhook_url = models.URLField(blank=True, verbose_name=_lazy("Адрес webhook"))
    is_active = models.BooleanField(default=True, verbose_name=_lazy("Включён"))

    class Meta:
        verbose_name = _lazy("бот")
        verbose_name_plural = _lazy("Боты")
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
        verbose_name=_lazy("Заведение"),
    )
    module_menu_enabled = models.BooleanField(
        default=True,
        verbose_name=_lazy("Меню"),
        help_text=_lazy("Категории и позиции. Нужен для заказов и быстрых продаж."),
    )
    module_tables_enabled = models.BooleanField(
        default=True,
        verbose_name=_lazy("Столы и QR-сессии"),
        help_text=_lazy("Сценарий заказа за столом. Нужен для вызова персонала и запроса счёта."),
    )
    module_delivery_enabled = models.BooleanField(
        default=False,
        verbose_name=_lazy("Доставка"),
        help_text=_lazy("Сценарий заказа на доставку. Включается независимо от столов."),
    )
    module_pickup_enabled = models.BooleanField(
        default=False,
        verbose_name=_lazy("Самовывоз"),
        help_text=_lazy("Сценарий заказа на самовывоз. Включается независимо от столов."),
    )
    # Payment options per remote module (PQ-033): the owner turns on what the
    # bot offers; when both are on, the guest chooses at checkout.
    pickup_pay_transfer = models.BooleanField(
        default=True,
        verbose_name=_lazy("Самовывоз: оплата переводом заранее"),
        help_text=_lazy("Гость переводит деньги на реквизиты, персонал подтверждает поступление."),
    )
    pickup_pay_on_receipt = models.BooleanField(
        default=False,
        verbose_name=_lazy("Самовывоз: оплата при получении"),
        help_text=_lazy("Гость платит на месте наличными или картой, заказ сразу идёт в работу."),
    )
    delivery_pay_transfer = models.BooleanField(
        default=True,
        verbose_name=_lazy("Доставка: оплата переводом заранее"),
        help_text=_lazy("Гость переводит деньги на реквизиты, персонал подтверждает поступление."),
    )
    delivery_pay_on_receipt = models.BooleanField(
        default=False,
        verbose_name=_lazy("Доставка: оплата курьеру при получении"),
        help_text=_lazy("Гость платит курьеру наличными или картой, заказ сразу идёт в работу."),
    )
    module_shipping_enabled = models.BooleanField(
        default=False,
        verbose_name=_lazy("Отправка почтой"),
        help_text=(
            _lazy(
                "Сценарий отправки заказа почтовой службой: гость выбирает службу и отделение, "
                "персонал отправляет и вводит ТТН. Без часов работы.",
            )
        ),
    )
    shipping_nova_poshta = models.BooleanField(default=True, verbose_name=_lazy(
        "Служба: Нова Пошта",
    ))
    shipping_ukrposhta = models.BooleanField(default=False, verbose_name=_lazy("Служба: Укрпошта"))
    shipping_meest = models.BooleanField(default=False, verbose_name=_lazy("Служба: Meest"))
    shipping_cost_carrier = models.BooleanField(
        default=True,
        verbose_name=_lazy("Пересылка: по тарифу перевозчика"),
        help_text=_lazy("Получатель платит за пересылку на почте; в заказе её нет."),
    )
    shipping_cost_fixed = models.BooleanField(
        default=False,
        verbose_name=_lazy("Пересылка: фиксированная цена в заказе"),
        help_text=_lazy("Цена ниже добавляется к заказу строкой «Пересылка»."),
    )
    shipping_fee = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name=_lazy("Цена пересылки, грн"),
        help_text=_lazy("Для варианта «фиксированная цена в заказе». 0 — бесплатно."),
    )
    shipping_free_from = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_lazy("Бесплатная пересылка от суммы, грн"),
        help_text=_lazy("Для варианта «фиксированная цена в заказе». Пусто — всегда платная."),
    )
    shipping_min_order = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name=_lazy("Минимальная сумма заказа на отправку, грн"),
        help_text=_lazy("0 — без минимума."),
    )
    shipping_pay_transfer = models.BooleanField(
        default=True,
        verbose_name=_lazy("Отправка: оплата переводом заранее"),
        help_text=_lazy("Гость переводит деньги на реквизиты, персонал подтверждает поступление."),
    )
    shipping_pay_on_receipt = models.BooleanField(
        default=False,
        verbose_name=_lazy("Отправка: наложенный платёж"),
        help_text=_lazy("Гость платит на почте при получении; заказ сразу идёт в работу."),
    )
    transfer_payment_details = models.TextField(
        blank=True,
        verbose_name=_lazy("Реквизиты для оплаты переводом"),
        help_text=(
            _lazy(
                "Куда гость переводит деньги за заказы с предоплатой (самовывоз, доставка, "
                "отправка): номер карты или IBAN, получатель. Гость видит этот текст перед "
                "оплатой. Меняет только владелец.",
            )
        ),
    )
    # Unpaid remote orders (AA-037, PQ-036): limits against fake orders.
    remote_unpaid_orders_limit = models.PositiveSmallIntegerField(
        default=2,
        verbose_name=_lazy("Неоплаченных заказов на гостя, не больше"),
        help_text=(
            _lazy(
                "Самовывоз, доставка, отправка: сколько открытых неоплаченных заказов может быть "
                "у одного гостя. 0 — без лимита.",
            )
        ),
    )
    transfer_wait_minutes = models.PositiveSmallIntegerField(
        default=60,
        verbose_name=_lazy("Ждать перевод, минут"),
        help_text=(
            _lazy(
                "Заказ с оплатой переводом, оплату которого никто не подтвердил за это время, "
                "отменяется автоматически, бонусы возвращаются. Не меньше 15; 0 — не отменять.",
            )
        ),
    )
    delivery_fee = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name=_lazy("Стоимость доставки, грн"),
        help_text=_lazy("Добавляется к заказу отдельной строкой «Доставка». 0 — бесплатно."),
    )
    delivery_free_from = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_lazy("Бесплатная доставка от суммы, грн"),
        help_text=_lazy(
            "Если сумма заказа не меньше этой, доставка бесплатна. Пусто — всегда платная.",
        ),
    )
    delivery_min_order = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name=_lazy("Минимальная сумма заказа на доставку, грн"),
        help_text=_lazy("0 — без минимума."),
    )
    module_cart_enabled = models.BooleanField(
        default=True,
        verbose_name=_lazy("Корзина"),
        help_text=_lazy("Сбор позиций перед заказом. Требует меню и модуль заказов."),
    )
    module_orders_enabled = models.BooleanField(
        default=True,
        verbose_name=_lazy("Заказы"),
        help_text=_lazy(
            "Оформление и обработку заказов. Требует меню и хотя бы один сценарий: столы, "
            "доставку или самовывоз.",
        ),
    )
    module_billing_enabled = models.BooleanField(
        default=False,
        verbose_name=_lazy("Счета и оплаты"),
        help_text=_lazy(
            "Счета, оплаты и списание бонусов. Требует модуль заказов. Запрос счёта гостем "
            "доступен только при включённых столах.",
        ),
    )
    module_staff_call_enabled = models.BooleanField(
        default=False,
        verbose_name=_lazy("Вызов персонала"),
        help_text=_lazy("Кнопка вызова официанта/бара/кальянщика. Требует столы."),
    )
    module_quick_sale_enabled = models.BooleanField(
        default=True,
        verbose_name=_lazy("Быстрые продажи"),
        help_text=_lazy("Продажа на кассе по коду клиента. Требует меню."),
    )
    module_reports_enabled = models.BooleanField(
        default=True,
        verbose_name=_lazy("Отчёт дня"),
        help_text=_lazy("Операционный отчёт в staff-боте."),
    )
    auto_close_table_session_after_payment = models.BooleanField(
        default=True,
        verbose_name=_lazy("Закрывать стол после полной оплаты"),
    )
    max_menu_items_per_category_message = models.PositiveSmallIntegerField(
        default=8,
        verbose_name=_lazy("Позиций меню в одном сообщении"),
    )
    duplicate_request_cooldown_seconds = models.PositiveSmallIntegerField(
        default=45,
        verbose_name=_lazy("Пауза между одинаковыми запросами, с"),
    )
    allow_menu_without_session = models.BooleanField(
        default=True,
        verbose_name=_lazy("Меню без сканирования стола"),
    )
    staff_call_waiter_enabled = models.BooleanField(default=True, verbose_name=_lazy(
        "Вызов: официант",
    ))
    staff_call_bartender_enabled = models.BooleanField(default=True, verbose_name=_lazy(
        "Вызов: бармен",
    ))
    staff_call_hookah_enabled = models.BooleanField(default=True, verbose_name=_lazy(
        "Вызов: кальянщик",
    ))
    extra_config = models.JSONField(
        default=dict,
        blank=True,
        help_text=_lazy("Резерв для будущих флагов и особых настроек бота заведения."),
        verbose_name=_lazy("Доп. настройки (JSON)"),
    )
    # Languages (PQ-040): the guest sees the language of their Telegram when
    # it is enabled here, otherwise the main one, and may switch in the profile.
    default_language = models.CharField(
        max_length=8,
        choices=LANGUAGE_CHOICES,
        default=default_language,
        verbose_name=_lazy("Основной язык бота"),
        help_text=_lazy("Для гостей, чей язык Telegram не включён ниже. Должен быть включён."),
    )
    language_uk = models.BooleanField(default=True, verbose_name=_lazy("Язык гостей: украинский"))
    language_ru = models.BooleanField(default=True, verbose_name=_lazy("Язык гостей: русский"))
    language_en = models.BooleanField(default=True, verbose_name=_lazy("Язык гостей: английский"))

    class Meta:
        verbose_name = _lazy("настройки бота")
        verbose_name_plural = _lazy("Настройки ботов")
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
            ("module_pickup_enabled", _(
                "самовывоза",
            ), "pickup_pay_transfer", "pickup_pay_on_receipt"),
            (
                "module_delivery_enabled",
                _("доставки"),
                "delivery_pay_transfer",
                "delivery_pay_on_receipt",
            ),
            (
                "module_shipping_enabled",
                _("отправки"),
                "shipping_pay_transfer",
                "shipping_pay_on_receipt",
            ),
        ):
            if not getattr(self, module):
                continue
            if not (getattr(self, transfer_field) or getattr(self, on_receipt_field)):
                add_error(transfer_field, _(
                    "Для {title} включите хотя бы один способ оплаты.",
                    title=title,
                ))
            if getattr(self, transfer_field) and not self.transfer_payment_details.strip():
                add_error(
                    "transfer_payment_details",
                    _("Для {title} с оплатой переводом укажите реквизиты.", title=title),
                )
        if self.module_shipping_enabled:
            if not (self.shipping_nova_poshta or self.shipping_ukrposhta or self.shipping_meest):
                add_error("shipping_nova_poshta", _("Для отправки включите хотя бы одну службу."))
            if not (self.shipping_cost_carrier or self.shipping_cost_fixed):
                add_error(
                    "shipping_cost_carrier",
                    _("Для отправки включите хотя бы один вариант оплаты пересылки."),
                )
        if 0 < self.transfer_wait_minutes < MIN_TRANSFER_WAIT_MINUTES:
            add_error(
                "transfer_wait_minutes",
                _(
                    "Не меньше {min_transfer_wait_minutes} минут (или 0 — не отменять): перевод "
                    "может идти не сразу.",
                    min_transfer_wait_minutes=MIN_TRANSFER_WAIT_MINUTES,
                ),
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
                add_error(field, _("Сумма не может быть отрицательной."))
        if not getattr(self, GUEST_LANGUAGE_FIELDS.get(self.default_language, ""), False):
            add_error("default_language", _("Основной язык должен быть среди включённых языков."))
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

        if errors:
            raise ValidationError(errors)


class OpeningHours(TimeStampedModel):
    """One opening interval of a venue on a weekday, in the venue's timezone.

    ``closes_at`` at or before ``opens_at`` means the interval ends after
    midnight (for example 18:00–03:00). A venue without intervals is treated
    as always open.
    """

    class Weekday(models.IntegerChoices):
        MONDAY = 0, _lazy("Понедельник")
        TUESDAY = 1, _lazy("Вторник")
        WEDNESDAY = 2, _lazy("Среда")
        THURSDAY = 3, _lazy("Четверг")
        FRIDAY = 4, _lazy("Пятница")
        SATURDAY = 5, _lazy("Суббота")
        SUNDAY = 6, _lazy("Воскресенье")

    class Scope(models.TextChoices):
        # Modules with their own hours; empty = the venue's common hours,
        # used by a module that has none of its own.
        COMMON = "", _lazy("Общие (для всех модулей)")
        PICKUP = "pickup", _lazy("Самовывоз")
        DELIVERY = "delivery", _lazy("Доставка")

    partner = models.ForeignKey(
        Partner,
        on_delete=models.CASCADE,
        related_name="opening_hours",
        verbose_name=_lazy("Заведение"),
    )
    scope = models.CharField(
        max_length=16,
        choices=Scope.choices,
        default=Scope.COMMON,
        blank=True,
        verbose_name=_lazy("Для чего"),
        help_text=(
            _lazy(
                "Свои часы модуля. Если у модуля своих часов нет, действуют общие; если нет и "
                "общих — модуль работает всегда.",
            )
        ),
    )
    weekday = models.PositiveSmallIntegerField(choices=Weekday.choices, verbose_name=_lazy(
        "День недели",
    ))
    opens_at = models.TimeField(verbose_name=_lazy("Открытие"))
    closes_at = models.TimeField(verbose_name=_lazy("Закрытие"))

    class Meta:
        ordering = ["partner_id", "scope", "weekday", "opens_at"]
        verbose_name = _lazy("Часы работы")
        verbose_name_plural = _lazy("Часы работы")

    def __str__(self) -> str:
        prefix = f"{self.get_scope_display()}: " if self.scope else ""
        return (
            f"{prefix}{self.get_weekday_display()} "
            f"{self.opens_at:%H:%M}–{self.closes_at:%H:%M}"
        )

    def clean(self) -> None:
        super().clean()
        if self.opens_at is not None and self.opens_at == self.closes_at:
            raise ValidationError({"closes_at": _(
                "Время закрытия совпадает со временем открытия.",
            )})


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
        verbose_name=_lazy("Заведение"),
    )
    language = models.CharField(max_length=8, choices=LANGUAGE_CHOICES, verbose_name=_lazy("Язык"))
    msgid = models.TextField(verbose_name=_lazy("Текст в каталоге"))
    key = models.CharField(max_length=40, editable=False, verbose_name=_lazy("Ключ"))
    text = models.TextField(verbose_name=_lazy("Свой текст"))

    class Meta:
        ordering = ["partner_id", "language", "msgid"]
        verbose_name = _lazy("текст бота")
        verbose_name_plural = _lazy("Тексты бота")
        constraints = [
            models.UniqueConstraint(
                fields=["partner", "language", "key"], name="uniq_bot_text_per_language"
            )
        ]

    def __str__(self) -> str:
        return f"{self.language}: {self.text[:60]}"

    def clean(self) -> None:
        super().clean()
        from apps.partners.bot_texts import BUTTON_TEXTS, editable_texts, text_problems
        from core.i18n import _, gettext_in

        if self.msgid not in editable_texts():
            raise ValidationError({"msgid": _("Такого текста нет в каталоге бота.")})
        problems = text_problems(self.msgid, self.text)
        if not problems and self.msgid in BUTTON_TEXTS.values() and self.partner_id:
            label = self.text.strip()
            own = dict(
                BotText.objects.filter(partner_id=self.partner_id, language=self.language)
                .exclude(id=self.id)
                .values_list("msgid", "text")
            )
            for other in BUTTON_TEXTS.values():
                if other == self.msgid:
                    continue
                if (own.get(other) or gettext_in(self.language, other)).strip() == label:
                    problems.append(_("Такая подпись уже у другой кнопки: бот их спутает."))
                    break
        if problems:
            raise ValidationError({"text": problems})

    def save(self, *args, **kwargs):
        self.key = text_key(self.msgid)
        super().save(*args, **kwargs)
