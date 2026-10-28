from dataclasses import dataclass
from typing import Any

from apps.partners.bot_texts import (
    BUTTON_TEXTS,
    INLINE_BUTTON_TEXTS,
    TEMPLATE_TEXTS,
    WHOLE_TEXTS,
)
from apps.partners.models import Partner, PartnerBotSettings
from bot.utils.html import escape_html
from core.i18n import N_, _, own_text


def _render_template(template: str, **context) -> str:
    if not template:
        return ""
    # Keep formatting narrow and explicit so partner-defined templates cannot
    # accidentally depend on arbitrary runtime objects or hidden fields.
    # The template is trusted partner markup; substituted values are escaped.
    escaped_context = {key: escape_html(value) for key, value in context.items()}
    try:
        return template.format(**escaped_context)
    except (KeyError, ValueError):
        # Bad partner template text must not break bot delivery at runtime.
        return template


@dataclass(slots=True)
class BotContent:
    module_menu_enabled: bool
    module_tables_enabled: bool
    module_delivery_enabled: bool
    module_pickup_enabled: bool
    module_shipping_enabled: bool
    module_cart_enabled: bool
    module_orders_enabled: bool
    module_billing_enabled: bool
    module_staff_call_enabled: bool
    module_quick_sale_enabled: bool
    module_reports_enabled: bool
    auto_close_table_session_after_payment: bool
    max_menu_items_per_category_message: int
    duplicate_request_cooldown_seconds: int
    button_menu_label: str
    button_session_label: str
    button_cart_label: str
    button_checkout_label: str
    button_delivery_label: str
    button_pickup_label: str
    button_shipping_label: str
    button_help_label: str
    button_my_profile: str
    button_profile_bonuses_label: str
    button_call_staff_label: str
    button_request_bill_label: str
    button_call_waiter_label: str
    button_call_bartender_label: str
    button_call_hookah_label: str
    staff_call_waiter_enabled: bool
    staff_call_bartender_enabled: bool
    staff_call_hookah_enabled: bool
    allow_menu_without_session: bool
    extra_config: dict[str, Any]
    welcome_message_template: str
    table_activated_message_template: str
    menu_header_template: str
    menu_requires_session_hint_template: str
    menu_active_session_hint_template: str
    menu_empty_message_template: str
    ordering_help_message_template: str
    cart_empty_message_template: str
    cart_cleared_message_template: str
    staff_call_prompt_template: str
    staff_call_success_template: str
    request_bill_prompt_template: str
    request_bill_personal_success_template: str
    request_bill_shared_success_template: str
    request_bill_custom_success_template: str
    order_created_message_template: str

    @classmethod
    def for_partner(cls, partner: Partner) -> "BotContent":
        try:
            settings = partner.bot_settings
        except PartnerBotSettings.DoesNotExist:
            return cls.defaults()
        return cls.from_settings(settings)

    @staticmethod
    def _texts() -> dict[str, str]:
        """Buttons and messages in the active language with the owner's texts (PQ-040)."""

        texts = {
            field: _(msgid)
            for field, msgid in {**BUTTON_TEXTS, **INLINE_BUTTON_TEXTS, **TEMPLATE_TEXTS}.items()
        }
        texts.update({field: own_text(msgid) for field, msgid in WHOLE_TEXTS.items()})
        return texts

    @classmethod
    def defaults(cls) -> "BotContent":
        return cls(
            module_menu_enabled=True,
            module_tables_enabled=True,
            module_delivery_enabled=False,
            module_pickup_enabled=False,
            module_shipping_enabled=False,
            module_cart_enabled=True,
            module_orders_enabled=True,
            module_billing_enabled=False,
            module_staff_call_enabled=False,
            module_quick_sale_enabled=True,
            module_reports_enabled=True,
            auto_close_table_session_after_payment=True,
            max_menu_items_per_category_message=8,
            duplicate_request_cooldown_seconds=45,
            staff_call_waiter_enabled=True,
            staff_call_bartender_enabled=True,
            staff_call_hookah_enabled=True,
            allow_menu_without_session=True,
            extra_config={},
            **cls._texts(),
        )

    @classmethod
    def from_settings(cls, settings: PartnerBotSettings) -> "BotContent":
        return cls(
            module_menu_enabled=settings.module_menu_enabled,
            module_tables_enabled=settings.module_tables_enabled,
            module_delivery_enabled=settings.module_delivery_enabled,
            module_pickup_enabled=settings.module_pickup_enabled,
            module_shipping_enabled=settings.module_shipping_enabled,
            module_cart_enabled=settings.module_cart_enabled,
            module_orders_enabled=settings.module_orders_enabled,
            module_billing_enabled=settings.module_billing_enabled,
            module_staff_call_enabled=settings.module_staff_call_enabled,
            module_quick_sale_enabled=settings.module_quick_sale_enabled,
            module_reports_enabled=settings.module_reports_enabled,
            auto_close_table_session_after_payment=(
                settings.auto_close_table_session_after_payment
            ),
            max_menu_items_per_category_message=settings.max_menu_items_per_category_message,
            duplicate_request_cooldown_seconds=settings.duplicate_request_cooldown_seconds,
            staff_call_waiter_enabled=settings.staff_call_waiter_enabled,
            staff_call_bartender_enabled=settings.staff_call_bartender_enabled,
            staff_call_hookah_enabled=settings.staff_call_hookah_enabled,
            allow_menu_without_session=settings.allow_menu_without_session,
            extra_config=settings.extra_config or {},
            **cls._texts(),
        )

    def supports_cart(self) -> bool:
        return self.module_cart_enabled and self.supports_orders()

    def supports_loyalty(self) -> bool:
        return True

    def supports_menu(self) -> bool:
        return self.module_menu_enabled

    def supports_tables(self) -> bool:
        return self.module_tables_enabled

    def supports_orders(self) -> bool:
        if not self.module_orders_enabled or not self.module_menu_enabled:
            return False
        return (
            self.supports_table_orders()
            or self.supports_remote_orders()
        )

    def supports_table_orders(self) -> bool:
        return (
            self.module_orders_enabled
            and self.module_menu_enabled
            and self.module_tables_enabled
        )

    def supports_delivery_orders(self) -> bool:
        return (
            self.module_orders_enabled
            and self.module_menu_enabled
            and self.module_delivery_enabled
        )

    def supports_pickup_orders(self) -> bool:
        return (
            self.module_orders_enabled
            and self.module_menu_enabled
            and self.module_pickup_enabled
        )

    def supports_shipping_orders(self) -> bool:
        return (
            self.module_orders_enabled
            and self.module_menu_enabled
            and self.module_shipping_enabled
        )

    def supports_remote_orders(self) -> bool:
        """Any channel where the guest orders without a table (ADR-0009)."""

        return (
            self.supports_pickup_orders()
            or self.supports_delivery_orders()
            or self.supports_shipping_orders()
        )

    def supports_help(self) -> bool:
        return (
            self.supports_menu()
            or self.supports_orders()
            or self.supports_staff_call()
            or self.supports_billing_request()
        )

    def supports_quick_sale(self) -> bool:
        return self.module_quick_sale_enabled and self.module_menu_enabled

    def supports_reports(self) -> bool:
        return self.module_reports_enabled

    def supports_billing(self) -> bool:
        # Staff-side billing/payments capability. Unlike the guest bill request
        # (which needs an active table), payments only require the orders module.
        return self.module_billing_enabled and self.supports_orders()

    def cart_disabled_message(self) -> str:
        return _("В этом боте корзина сейчас недоступна для выбранного сценария заказа.")

    def config_value(self, key: str, default: Any = None) -> Any:
        return self.extra_config.get(key, default)

    def feature_enabled(self, key: str, default: bool = False) -> bool:
        value = self.extra_config.get(key, default)
        return value if isinstance(value, bool) else default

    def supports_staff_call(self) -> bool:
        return (
            self.module_staff_call_enabled
            and self.module_tables_enabled
            and bool(self.available_staff_call_targets())
        )

    def supports_billing_request(self) -> bool:
        return (
            self.module_billing_enabled
            and self.module_tables_enabled
            and self.supports_orders()
        )

    def available_staff_call_targets(self) -> list[tuple[str, str]]:
        targets: list[tuple[str, str]] = []
        if self.staff_call_waiter_enabled:
            targets.append(("waiter", self.button_call_waiter_label))
        if self.staff_call_bartender_enabled:
            targets.append(("bartender", self.button_call_bartender_label))
        if self.staff_call_hookah_enabled:
            targets.append(("hookah", self.button_call_hookah_label))
        return targets

    def ordering_help_message(self) -> str:
        if self.ordering_help_message_template:
            return self.ordering_help_message_template

        ways: list[str] = []
        if self.supports_cart() and self.supports_table_orders():
            table_line = _(
                "<b>За столом:</b> отсканируйте QR-код на столе, добавьте позиции из меню "
                "и нажмите «Оформить» в корзине."
            )
            if self.supports_staff_call():
                table_line += " " + _(
                    "Позвать персонал — «{button}».",
                    button=escape_html(self.button_call_staff_label),
                )
            if self.supports_billing_request():
                table_line += " " + _(
                    "Оплатить — «{button}».",
                    button=escape_html(self.button_request_bill_label),
                )
            ways.append(table_line)
        remote_ways = (
            (
                self.supports_pickup_orders(),
                self.button_pickup_label,
                N_("<b>{button}:</b> добавьте позиции из меню и нажмите «Оформить» в "
                   "корзине — выберете время и оплату."),
            ),
            (
                self.supports_delivery_orders(),
                self.button_delivery_label,
                N_("<b>{button}:</b> добавьте позиции из меню и нажмите «Оформить» в "
                   "корзине — адрес, время и оплата."),
            ),
            (
                self.supports_shipping_orders(),
                self.button_shipping_label,
                N_("<b>{button}:</b> добавьте позиции из меню и нажмите «Оформить» в "
                   "корзине — служба доставки, отделение и оплата."),
            ),
        )
        if self.supports_cart():
            for enabled, button, text in remote_ways:
                if enabled:
                    ways.append(_(text, button=escape_html(button)))

        if not ways:
            if self.supports_menu():
                return _(
                    "Меню — кнопка «{button_menu_label}». Бонусы — «{button_my_profile}».",
                    button_menu_label=escape_html(self.button_menu_label),
                    button_my_profile=escape_html(self.button_my_profile),
                )
            return _(
                "В этом боте сейчас доступен профиль клиента. Откройте его кнопкой "
                "«{button_my_profile}».",
                button_my_profile=escape_html(self.button_my_profile),
            )
        lines = [_("<b>Как заказать</b>"), ""]
        lines.extend(f"• {line}" for line in ways)
        lines.append("")
        lines.append(
            _(
                "Бонусы и ваши заказы — «{button_my_profile}».",
                button_my_profile=escape_html(self.button_my_profile),
            )
        )
        return "\n".join(lines)

    def welcome_message(self, *, partner_name: str) -> str:
        return _render_template(self.welcome_message_template, partner_name=partner_name)

    def start_hint_lines(self, *, with_ways: bool = False) -> list[str]:
        """What to do after /start. ``with_ways``: buttons that choose pickup,
        delivery or shipping follow this text."""

        lines: list[str] = []
        if with_ways:
            lines.append(_("<b>Как хотите заказать?</b>"))
        if self.supports_tables() and self.supports_table_orders():
            lines.append(_("За столом — отсканируйте QR-код на столе."))
        if not lines and self.supports_menu():
            lines.append(
                _(
                    "Меню — кнопка «{button_menu_label}».",
                    button_menu_label=escape_html(self.button_menu_label),
                )
            )
        return lines

    def table_activated_message(self, *, partner_name: str, table_number: int) -> str:
        if not self.table_activated_message_template:
            lines = [
                _("Ваш стол: #{table_number}", table_number=table_number),
                _("Заведение: {partner_name}", partner_name=escape_html(partner_name)),
                "",
            ]
            if self.supports_cart():
                lines.append(
                    _(
                        "Стол активирован. Теперь можно открыть меню, собрать корзину и "
                        "оформить заказ."
                    )
                )
            elif self.supports_menu():
                lines.append(_("Стол активирован. Теперь можно открыть меню."))
            else:
                lines.append(_("Стол активирован."))
            return "\n".join(lines)

        return _render_template(
            self.table_activated_message_template,
            partner_name=partner_name,
            table_number=table_number,
        )

    def menu_header(self, *, partner_name: str) -> str:
        return _render_template(self.menu_header_template, partner_name=partner_name)

    def menu_requires_session_hint(self) -> str:
        return self.menu_requires_session_hint_template

    def menu_active_session_hint(self, *, table_number: int) -> str:
        return _render_template(self.menu_active_session_hint_template, table_number=table_number)

    def order_created_message(
        self,
        *,
        order_public_id: str,
        total_amount,
        status_display: str,
    ) -> str:
        return _render_template(
            self.order_created_message_template,
            order_public_id=order_public_id,
            total_amount=total_amount,
            status_display=status_display,
        )

    def staff_call_prompt_message(self) -> str:
        return self.staff_call_prompt_template

    def staff_call_success_message(
        self,
        *,
        partner_name: str,
        table_number: int,
        call_target_label: str,
    ) -> str:
        return _render_template(
            self.staff_call_success_template,
            partner_name=partner_name,
            table_number=table_number,
            call_target_label=call_target_label,
        )

    def request_bill_prompt_message(self) -> str:
        return self.request_bill_prompt_template

    def request_bill_personal_success_message(
        self,
        *,
        partner_name: str,
        table_number: int,
        bill_public_id: str,
    ) -> str:
        return _render_template(
            self.request_bill_personal_success_template,
            partner_name=partner_name,
            table_number=table_number,
            bill_public_id=bill_public_id,
        )

    def request_bill_shared_success_message(
        self,
        *,
        partner_name: str,
        table_number: int,
        bill_public_id: str,
    ) -> str:
        return _render_template(
            self.request_bill_shared_success_template,
            partner_name=partner_name,
            table_number=table_number,
            bill_public_id=bill_public_id,
        )

    def request_bill_custom_success_message(
        self,
        *,
        partner_name: str,
        table_number: int,
    ) -> str:
        return _render_template(
            self.request_bill_custom_success_template,
            partner_name=partner_name,
            table_number=table_number,
        )
