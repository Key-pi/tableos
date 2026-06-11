from dataclasses import dataclass
from typing import Any

from apps.partners.models import Partner, PartnerBotSettings


def _render_template(template: str, **context) -> str:
    if not template:
        return ""
    # Keep formatting narrow and explicit so partner-defined templates cannot
    # accidentally depend on arbitrary runtime objects or hidden fields.
    try:
        return template.format(**context)
    except (KeyError, ValueError):
        # Bad partner template text must not break bot delivery at runtime.
        return template


@dataclass(slots=True)
class BotContent:
    button_menu_label: str
    button_session_label: str
    button_cart_label: str
    button_checkout_label: str
    button_help_label: str
    button_loyalty_label: str
    button_call_staff_label: str
    button_request_bill_label: str
    button_call_waiter_label: str
    button_call_bartender_label: str
    button_call_hookah_label: str
    show_help_button: bool
    show_session_button: bool
    show_cart_button: bool
    show_checkout_button: bool
    show_loyalty_button: bool
    show_call_staff_button: bool
    show_request_bill_button: bool
    staff_call_waiter_enabled: bool
    staff_call_bartender_enabled: bool
    staff_call_hookah_enabled: bool
    allow_menu_without_session: bool
    guest_flow_code: str
    staff_flow_code: str
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

    @classmethod
    def defaults(cls) -> "BotContent":
        return cls(
            button_menu_label="Открыть меню",
            button_session_label="Мой стол",
            button_cart_label="Корзина",
            button_checkout_label="Оформить заказ",
            button_help_label="Как заказать",
            button_loyalty_label="Мой профиль",
            button_call_staff_label="Позвать персонал",
            button_request_bill_label="Запросить счёт",
            button_call_waiter_label="Официант",
            button_call_bartender_label="Бар / касса",
            button_call_hookah_label="Кальянщик",
            show_help_button=True,
            show_session_button=True,
            show_cart_button=True,
            show_checkout_button=True,
            show_loyalty_button=True,
            show_call_staff_button=False,
            show_request_bill_button=False,
            staff_call_waiter_enabled=True,
            staff_call_bartender_enabled=True,
            staff_call_hookah_enabled=True,
            allow_menu_without_session=True,
            guest_flow_code="",
            staff_flow_code="",
            extra_config={},
            welcome_message_template=(
                "Добро пожаловать в {partner_name}.\n"
                "Сканируйте QR-код стола, чтобы открыть сессию и оформить заказ."
            ),
            table_activated_message_template=(
                "Стол #{table_number} активирован для {partner_name}.\n"
                "Теперь можно открыть меню и оформить заказ."
            ),
            menu_header_template="<b>Меню {partner_name}</b>",
            menu_requires_session_hint_template=(
                "Меню можно смотреть уже сейчас.\n"
                "Чтобы оформить заказ, сначала отсканируйте QR-код вашего стола."
            ),
            menu_active_session_hint_template="Активный стол: #{table_number}",
            menu_empty_message_template="Сейчас в меню нет доступных позиций.",
            ordering_help_message_template="",
            cart_empty_message_template=(
                "Корзина пока пуста. Откройте меню и добавьте позиции кнопками."
            ),
            cart_cleared_message_template="Корзина очищена.",
            staff_call_prompt_template=(
                "Кого нужно позвать к столу? Выберите нужный вариант ниже."
            ),
            staff_call_success_template=(
                "{call_target_label} уже получает уведомление по столу #{table_number}."
            ),
            request_bill_prompt_template=(
                "Как вы хотите оформить счёт? Выберите подходящий вариант ниже."
            ),
            request_bill_personal_success_template=(
                "Подготовили персональный счёт #{bill_public_id} по столу #{table_number}."
            ),
            request_bill_shared_success_template=(
                "Подготовили общий счёт #{bill_public_id} по столу #{table_number}."
            ),
            request_bill_custom_success_template=(
                "Запрос на нестандартное разделение счёта "
                "по столу #{table_number} отправлен персоналу."
            ),
            order_created_message_template=(
                "Заказ принят: #{order_public_id}\n"
                "Сумма: {total_amount} грн\n"
                "Мы уже передали его персоналу."
            ),
        )

    @classmethod
    def from_settings(cls, settings: PartnerBotSettings) -> "BotContent":
        defaults = cls.defaults()
        return cls(
            button_menu_label=settings.button_menu_label or defaults.button_menu_label,
            button_session_label=settings.button_session_label or defaults.button_session_label,
            button_cart_label=settings.button_cart_label or defaults.button_cart_label,
            button_checkout_label=settings.button_checkout_label or defaults.button_checkout_label,
            button_help_label=settings.button_help_label or defaults.button_help_label,
            button_loyalty_label=settings.button_loyalty_label or defaults.button_loyalty_label,
            button_call_staff_label=(
                settings.button_call_staff_label or defaults.button_call_staff_label
            ),
            button_request_bill_label=(
                settings.button_request_bill_label or defaults.button_request_bill_label
            ),
            button_call_waiter_label=(
                settings.button_call_waiter_label or defaults.button_call_waiter_label
            ),
            button_call_bartender_label=(
                settings.button_call_bartender_label or defaults.button_call_bartender_label
            ),
            button_call_hookah_label=(
                settings.button_call_hookah_label or defaults.button_call_hookah_label
            ),
            show_help_button=settings.show_help_button,
            show_session_button=settings.show_session_button,
            show_cart_button=settings.show_cart_button,
            show_checkout_button=settings.show_checkout_button,
            show_loyalty_button=settings.show_loyalty_button,
            show_call_staff_button=settings.show_call_staff_button,
            show_request_bill_button=settings.show_request_bill_button,
            staff_call_waiter_enabled=settings.staff_call_waiter_enabled,
            staff_call_bartender_enabled=settings.staff_call_bartender_enabled,
            staff_call_hookah_enabled=settings.staff_call_hookah_enabled,
            allow_menu_without_session=settings.allow_menu_without_session,
            guest_flow_code=settings.guest_flow_code,
            staff_flow_code=settings.staff_flow_code,
            extra_config=settings.extra_config or {},
            welcome_message_template=(
                settings.welcome_message_template or defaults.welcome_message_template
            ),
            table_activated_message_template=(
                settings.table_activated_message_template
                or defaults.table_activated_message_template
            ),
            menu_header_template=settings.menu_header_template or defaults.menu_header_template,
            menu_requires_session_hint_template=(
                settings.menu_requires_session_hint_template
                or defaults.menu_requires_session_hint_template
            ),
            menu_active_session_hint_template=(
                settings.menu_active_session_hint_template
                or defaults.menu_active_session_hint_template
            ),
            menu_empty_message_template=(
                settings.menu_empty_message_template or defaults.menu_empty_message_template
            ),
            ordering_help_message_template=(
                settings.ordering_help_message_template or defaults.ordering_help_message_template
            ),
            cart_empty_message_template=(
                settings.cart_empty_message_template or defaults.cart_empty_message_template
            ),
            cart_cleared_message_template=(
                settings.cart_cleared_message_template or defaults.cart_cleared_message_template
            ),
            staff_call_prompt_template=(
                settings.staff_call_prompt_template or defaults.staff_call_prompt_template
            ),
            staff_call_success_template=(
                settings.staff_call_success_template or defaults.staff_call_success_template
            ),
            request_bill_prompt_template=(
                settings.request_bill_prompt_template or defaults.request_bill_prompt_template
            ),
            request_bill_personal_success_template=(
                settings.request_bill_personal_success_template
                or defaults.request_bill_personal_success_template
            ),
            request_bill_shared_success_template=(
                settings.request_bill_shared_success_template
                or defaults.request_bill_shared_success_template
            ),
            request_bill_custom_success_template=(
                settings.request_bill_custom_success_template
                or defaults.request_bill_custom_success_template
            ),
            order_created_message_template=(
                settings.order_created_message_template or defaults.order_created_message_template
            ),
        )

    def supports_cart(self) -> bool:
        return True

    def cart_disabled_message(self) -> str:
        return "В этом боте корзина сейчас недоступна для выбранного сценария заказа."

    def config_value(self, key: str, default: Any = None) -> Any:
        return self.extra_config.get(key, default)

    def feature_enabled(self, key: str, default: bool = False) -> bool:
        value = self.extra_config.get(key, default)
        return value if isinstance(value, bool) else default

    def ordering_entry_lines(self, *, table_number: int) -> list[str]:
        lines = [self.menu_active_session_hint(table_number=table_number)]
        actions: list[str] = []
        if self.supports_cart():
            actions.append(self.button_menu_label)
            if self.show_cart_button:
                actions.append(self.button_cart_label)
            if self.show_checkout_button:
                actions.append(self.button_checkout_label)
        if self.supports_staff_call():
            actions.append(self.button_call_staff_label)
        if self.supports_billing_request():
            actions.append(self.button_request_bill_label)
        if actions:
            lines.append("Доступные действия:")
            lines.extend(f"• {action}" for action in actions)
        return lines

    def supports_staff_call(self) -> bool:
        return self.show_call_staff_button and bool(self.available_staff_call_targets())

    def supports_billing_request(self) -> bool:
        return self.show_request_bill_button

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

        lines = [
            "1. Откройте меню и выберите нужные позиции.",
            "2. Если вы в заведении, сначала активируйте стол по QR-коду.",
        ]
        if self.supports_cart():
            lines.extend(
                [
                    "3. Добавьте позиции кнопками из меню.",
                    "4. Проверьте заказ в корзине.",
                    "5. Нажмите «Оформить заказ».",
                ]
            )
        if self.supports_staff_call():
            step_index = len(lines) + 1
            lines.append(
                f"{step_index}. После активации стола можно позвать персонал кнопкой "
                f'"{self.button_call_staff_label}".'
            )
        if self.supports_billing_request():
            step_index = len(lines) + 1
            lines.append(
                f"{step_index}. Когда будете готовы к оплате, используйте "
                f'"{self.button_request_bill_label}".'
            )
        return "\n".join(lines)

    def welcome_message(self, *, partner_name: str) -> str:
        return _render_template(self.welcome_message_template, partner_name=partner_name)

    def table_activated_message(self, *, partner_name: str, table_number: int) -> str:
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
