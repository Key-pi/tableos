import re

from django.core.exceptions import ImproperlyConfigured
from django.db import models

from core.database.models import TimeStampedModel
from core.security.crypto import decrypt_text, encrypt_text


class Partner(TimeStampedModel):
    """Venue or business account that owns isolated data inside the platform."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"

    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    timezone = models.CharField(max_length=64, default="Europe/Kiev")
    contact_phone = models.CharField(max_length=32, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)

    class Meta:
        ordering = ["name"]

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
    allow_menu_without_session = models.BooleanField(default=True)
    show_help_button = models.BooleanField(default=True)
    show_session_button = models.BooleanField(default=True)
    show_cart_button = models.BooleanField(default=True)
    show_checkout_button = models.BooleanField(default=True)
    show_loyalty_button = models.BooleanField(default=True)
    show_call_staff_button = models.BooleanField(default=False)
    show_request_bill_button = models.BooleanField(default=False)
    staff_call_waiter_enabled = models.BooleanField(default=True)
    staff_call_bartender_enabled = models.BooleanField(default=True)
    staff_call_hookah_enabled = models.BooleanField(default=True)
    guest_flow_code = models.CharField(
        max_length=64,
        blank=True,
        help_text="Optional named guest flow branch for partner-specific code paths.",
    )
    staff_flow_code = models.CharField(
        max_length=64,
        blank=True,
        help_text="Optional named staff flow branch for partner-specific code paths.",
    )
    extra_config = models.JSONField(
        default=dict,
        blank=True,
        help_text="Reserved for future feature flags and partner-specific bot behavior.",
    )
    button_menu_label = models.CharField(max_length=64, default="Открыть меню")
    button_session_label = models.CharField(max_length=64, default="Мой стол")
    button_cart_label = models.CharField(max_length=64, default="Корзина")
    button_checkout_label = models.CharField(max_length=64, default="Оформить заказ")
    button_help_label = models.CharField(max_length=64, default="Как заказать")
    button_loyalty_label = models.CharField(max_length=64, default="Бонусы")
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
