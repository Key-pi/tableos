"""The owner's own bot texts (PQ-040): what can be replaced and how it is checked.

Every text of the bot catalog can be replaced per language (``BotText``). The
reply-keyboard buttons and the venue messages that used to be fields of
``PartnerBotSettings`` are catalog texts too; they are listed here because
the bot builds its keyboard and greetings from them
(``bot/services/content.py``).
"""

from __future__ import annotations

import ast
import functools
import pathlib
import re
from html.parser import HTMLParser

from django.conf import settings

from core.i18n import N_, placeholders

# Reply-keyboard buttons: the bot routes a guest's message by its text, so in
# one language every button must be non-empty, one line and unique.
BUTTON_TEXTS = {
    "button_menu_label": N_("Открыть меню"),
    "button_session_label": N_("Мой стол"),
    "button_cart_label": N_("Корзина"),
    "button_checkout_label": N_("Оформить заказ"),
    "button_delivery_label": N_("Заказать доставку"),
    "button_pickup_label": N_("Самовывоз"),
    "button_shipping_label": N_("Отправка почтой"),
    "button_help_label": N_("Как заказать"),
    "button_my_profile": N_("Мой профиль"),
    "button_call_staff_label": N_("Позвать персонал"),
    "button_request_bill_label": N_("Запросить счёт"),
}
# Inline buttons: may repeat a reply button, never empty.
INLINE_BUTTON_TEXTS = {
    "button_profile_bonuses_label": N_("Бонусы"),
    "button_call_waiter_label": N_("Официант"),
    "button_call_bartender_label": N_("Бар / касса"),
    "button_call_hookah_label": N_("Кальянщик"),
}
TEMPLATE_TEXTS = {
    "welcome_message_template": N_("Добро пожаловать в {partner_name}."),
    "menu_header_template": N_("<b>Меню {partner_name}</b>"),
    "menu_requires_session_hint_template": N_(
        "Меню можно смотреть уже сейчас.\n"
        "Чтобы оформить заказ, сначала отсканируйте QR-код вашего стола."
    ),
    "menu_active_session_hint_template": N_("Активный стол: #{table_number}"),
    "menu_empty_message_template": N_("Сейчас в меню нет доступных позиций."),
    "cart_empty_message_template": N_(
        "Корзина пока пуста. Откройте меню и добавьте позиции кнопками."
    ),
    "cart_cleared_message_template": N_("Корзина очищена."),
    "staff_call_prompt_template": N_("Кого нужно позвать к столу? Выберите нужный вариант ниже."),
    "staff_call_success_template": N_(
        "{call_target_label} уже получает уведомление по столу #{table_number}."
    ),
    "request_bill_prompt_template": N_(
        "Как вы хотите оформить счёт? Выберите подходящий вариант ниже."
    ),
    "request_bill_personal_success_template": N_(
        "Подготовили персональный счёт #{bill_public_id} по столу #{table_number}."
    ),
    "request_bill_shared_success_template": N_(
        "Подготовили общий счёт #{bill_public_id} по столу #{table_number}."
    ),
    "request_bill_custom_success_template": N_(
        "Запрос на нестандартное разделение счёта по столу #{table_number} отправлен персоналу."
    ),
    "order_created_message_template": N_(
        "Заказ принят: #{order_public_id}\n"
        "Сумма: {total_amount} грн\n"
        "Мы уже передали его персоналу."
    ),
}
# Whole messages the bot otherwise assembles itself: shown only when the
# owner wrote their own text; the catalog text is the description.
WHOLE_TEXTS = {
    "table_activated_message_template": N_(
        "Свой текст «Стол открыт» целиком вместо собранного ботом. "
        "Подстановки: {partner_name}, {table_number}"
    ),
    "ordering_help_message_template": N_(
        "Свой текст «Как заказать» целиком вместо собранного ботом"
    ),
}
OWNER_TEXTS = {**BUTTON_TEXTS, **INLINE_BUTTON_TEXTS, **TEMPLATE_TEXTS, **WHOLE_TEXTS}

# Files whose texts only the Admin shows: the owner does not replace those.
_ADMIN_ONLY = re.compile(
    r"^(core/|apps/\w+/(models|admin|admin_actions|forms|apps|constants)\.py$)"
)
_GROUPS = (
    ("staff", re.compile(r"^bot/(handlers|keyboards)/staff")),
    ("notifications", re.compile(r"^apps/notifications/")),
    ("guest", re.compile(r"^bot/")),
)
GROUP_LABELS = {
    "guest": N_("Гостю в боте"),
    "staff": N_("Персоналу в боте"),
    "notifications": N_("Уведомления"),
    "services": N_("Ответы и ошибки"),
}


def _catalog_file() -> pathlib.Path:
    return pathlib.Path(settings.LOCALE_PATHS[0]) / "uk" / "LC_MESSAGES" / "django.po"


@functools.lru_cache(maxsize=1)
def editable_texts() -> dict[str, str]:
    """Catalog texts the owner may replace: msgid -> group (read from the catalog)."""

    texts: dict[str, str] = {}
    for block in _catalog_file().read_text(encoding="utf-8").split("\n\n"):
        paths: list[str] = []
        parts: list[str] = []
        state = None
        for line in block.splitlines():
            if line.startswith("#:"):
                paths.extend(line[2:].split())
            elif line.startswith("msgid "):
                state, parts = "msgid", [ast.literal_eval(line[6:])]
            elif line.startswith("msgstr "):
                state = "msgstr"
            elif line.startswith('"') and state == "msgid":
                parts.append(ast.literal_eval(line))
        msgid = "".join(parts)
        if not msgid or not paths or all(_ADMIN_ONLY.match(path) for path in paths):
            continue
        texts[msgid] = next(
            (name for name, pattern in _GROUPS if any(pattern.match(p) for p in paths)),
            "services",
        )
    for text in OWNER_TEXTS.values():
        texts.setdefault(text, "guest")
    return texts


# Telegram HTML: the tags a bot message may use.
ALLOWED_TAGS = {
    "b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "a", "code", "pre",
    "tg-spoiler", "span", "blockquote",
}


class _TagChecker(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED_TAGS:
            self.errors.append(tag)
        else:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"/{tag}")
        else:
            self.stack.pop()


def text_problems(msgid: str, text: str) -> list[str]:
    """Why an owner's text cannot replace the catalog text; [] if it can."""

    from core.i18n import _

    problems: list[str] = []
    if not text.strip():
        problems.append(_("Текст не может быть пустым."))
        return problems
    allowed = placeholders(msgid)
    try:
        text.format_map({name: "" for name in allowed})
    except (KeyError, IndexError):
        problems.append(
            _(
                "Можно подставлять только: {names}.",
                names=", ".join(f"{{{name}}}" for name in sorted(allowed)) or "—",
            )
        )
    except ValueError:
        problems.append(_("Фигурные скобки { } — только для подстановок."))
    checker = _TagChecker()
    checker.feed(text)
    checker.close()
    if checker.errors or checker.stack:
        problems.append(
            _("Разметка: только теги Telegram (<b>, <i>, <u>, <s>, <a>, <code>, <pre>) и "
              "каждый закрыт.")
        )
    if msgid in BUTTON_TEXTS.values() or msgid in INLINE_BUTTON_TEXTS.values():
        if "\n" in text.strip() or len(text.strip()) > 64:
            problems.append(_("Подпись кнопки — одна строка до 64 знаков."))
    return problems
