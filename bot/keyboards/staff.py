from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# Status buttons: one copy for the bot, the Mini App and Admin.
from apps.billing.staff_actions import (  # noqa: F401
    DELIVERY_STATUS_LABELS,
    SHIPPING_STATUS_LABELS,
    STATUS_LABELS,
)
from core.i18n import N_, _

# Lists of the staff screens show this many rows a page; the rest is paged.
PAGE_SIZE = 8


def page_slice(items, page: int, *, size: int = PAGE_SIZE):
    """The items of one page, the page actually shown and the number of pages."""

    pages = max(1, (len(items) + size - 1) // size)
    page = min(max(page, 0), pages - 1)
    return items[page * size:(page + 1) * size], page, pages


def pager_row(prefix: str, page: int, pages: int) -> list[InlineKeyboardButton]:
    """«← 1/3 →» under a list; ``prefix`` + ``:p:<n>`` opens page n."""

    if pages <= 1:
        return []
    row = []
    if page > 0:
        row.append(InlineKeyboardButton(text="←", callback_data=f"{prefix}:p:{page - 1}"))
    row.append(
        InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data=f"{prefix}:p:{page}")
    )
    if page < pages - 1:
        row.append(InlineKeyboardButton(text="→", callback_data=f"{prefix}:p:{page + 1}"))
    return row


def back_row(text: str, callback_data: str) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text=text, callback_data=callback_data)]


def build_staff_order_actions_keyboard(
    *,
    order_public_id: str,
    available_statuses: list[str],
    transfer_check_pending: bool = False,
    receipt_payment_pending: bool = False,
    cash_on_delivery: bool = False,
    ship_needs_tracking: bool = False,
    status_labels: dict[str, str] | None = None,
    can_refund: bool = False,
    cancel_unpaid: bool = False,
    receipt_refusal: bool | None = None,
) -> InlineKeyboardMarkup | None:
    """``cancel_unpaid``: an order paid on receipt that is not with the courier
    yet — «Отменить» goes through the cancellation that returns bonuses.
    ``receipt_refusal``: «гость не забрал» next to the payment buttons (not
    for a completed order: the guest has it)."""

    if receipt_refusal is None:
        receipt_refusal = receipt_payment_pending

    labels = status_labels or STATUS_LABELS
    keyboard = []
    if receipt_payment_pending:
        # Paid on receipt (PQ-033): record the money when the guest pays; a
        # refusal replaces the plain cancel, so bonuses come back.
        if cash_on_delivery:
            # By post the carrier passes the money on later (PQ-034).
            keyboard.append(
                [
                    InlineKeyboardButton(
                        text=_("💵 Наложенный платёж получен"),
                        callback_data=f"staffrccod:{order_public_id}",
                    )
                ]
            )
        else:
            keyboard.append(
                [
                    InlineKeyboardButton(
                        text=_("💵 Оплачено наличными"),
                        callback_data=f"staffrccash:{order_public_id}",
                    ),
                    InlineKeyboardButton(
                        text=_("💳 Оплачено картой"),
                        callback_data=f"staffrccard:{order_public_id}",
                    ),
                ]
            )
        available_statuses = [status for status in available_statuses if status != "canceled"]
    if transfer_check_pending:
        # A prepaid order goes into work only after the transfer is checked.
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=_("✅ Оплата получена"),
                    callback_data=f"stafftxok:{order_public_id}",
                ),
                InlineKeyboardButton(
                    text=_("❌ Оплаты нет"),
                    callback_data=f"stafftxno:{order_public_id}",
                ),
            ]
        )
        available_statuses = []
    def status_callback(status: str) -> str:
        # «Отправлен» asks for the ТТН first (PQ-034).
        if ship_needs_tracking and status == "delivering":
            return f"staffship:{order_public_id}"
        if cancel_unpaid and status == "canceled":
            return f"staffrcno:{order_public_id}"
        return f"stafforder:{order_public_id}:{status}"

    status_buttons = [
        InlineKeyboardButton(
            text=_(labels[status]) if status in labels else status,
            callback_data=status_callback(status),
        )
        for status in available_statuses
    ]
    if receipt_refusal:
        status_buttons.append(
            InlineKeyboardButton(
                text=_("❌ Посылку не забрали") if cash_on_delivery else _("❌ Гость не забрал"),
                callback_data=f"staffrcno:{order_public_id}",
            )
        )
    if status_buttons:
        for index in range(0, len(status_buttons), 2):
            keyboard.append(status_buttons[index:index + 2])
    if can_refund:
        # Refund of a paid order (PQ-037); the right is checked on press.
        keyboard.append(
            [InlineKeyboardButton(text=_("↩️ Возврат"), callback_data=f"strf:{order_public_id}:0")]
        )
    keyboard.append(
        [
            InlineKeyboardButton(text=_("← К заказам"), callback_data="stafforders:refresh"),
            InlineKeyboardButton(
                text=_("Обновить"),
                callback_data=f"stafforderrefresh:{order_public_id}",
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def build_staff_transfer_reject_confirm_keyboard(order_public_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_("Да, оплаты нет — отменить заказ"),
                    callback_data=f"stafftxnoy:{order_public_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=_("Назад"),
                    callback_data=f"stafforderrefresh:{order_public_id}",
                )
            ],
        ]
    )


def build_staff_receipt_refusal_confirm_keyboard(order_public_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_("Да, отменить заказ"),
                    callback_data=f"staffrcnoy:{order_public_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=_("Назад"),
                    callback_data=f"stafforderrefresh:{order_public_id}",
                )
            ],
        ]
    )


ORDER_ROW_ACTIONS = {
    # A new order goes into work at once; a prepaid one only after the transfer
    # is checked, so its row opens the card with the check (PQ-033).
    "accept": (N_("Взять в работу"), "stafforder:{order}:accepted"),
    "check": (N_("💳 Проверить оплату"), "stafforderopen:{order}"),
}


def build_staff_orders_overview_keyboard(
    *,
    order_shortcuts: list[tuple[str, str, object]] | None = None,
    page: int = 0,
    pages: int = 1,
    can_quick_sale: bool = False,
    can_view_day_report: bool = False,
    can_manage_billing: bool = False,
    can_view_tables: bool = False,
) -> InlineKeyboardMarkup:
    """``order_shortcuts``: (public id, label, row action) — the action is
    ``"accept"``, ``"check"`` or nothing (``True`` means ``"accept"``)."""

    inline_keyboard = []
    for order_public_id, label, action in order_shortcuts or []:
        row = [
            InlineKeyboardButton(
                text=label,
                callback_data=f"stafforderopen:{order_public_id}",
            )
        ]
        action = "accept" if action is True else action
        if action in ORDER_ROW_ACTIONS:
            text, callback = ORDER_ROW_ACTIONS[action]
            row.append(
                InlineKeyboardButton(
                    text=_(text), callback_data=callback.format(order=order_public_id)
                )
            )
        inline_keyboard.append(row)
    pager = pager_row("stafforders", page, pages)
    if pager:
        inline_keyboard.append(pager)
    inline_keyboard.append(
        [
            InlineKeyboardButton(text=_("← Рабочий режим"), callback_data="staffhome"),
            InlineKeyboardButton(text=_("Обновить"), callback_data=f"stafforders:p:{page}"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=inline_keyboard)


def build_staff_sale_code_prompt_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_("Без кода (Аноним)"),
                    callback_data="staffsale:anon",
                )
            ]
        ]
    )


def build_staff_notifications_keyboard(
    rows: list[tuple[str, str]] | None = None,
    *,
    page: int = 0,
    pages: int = 1,
) -> InlineKeyboardMarkup:
    """The unread notifications as one list: (notification id, label) rows."""

    keyboard = [
        [
            InlineKeyboardButton(
                text=label,
                callback_data=f"staffnotifopen:{_hex(notification_id)}:{page}",
            )
        ]
        for notification_id, label in rows or []
    ]
    pager = pager_row("staffnotif", page, pages)
    if pager:
        keyboard.append(pager)
    if rows:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=_("✓ Отметить всё прочитанным"),
                    callback_data="staffnotif:readall",
                )
            ]
        )
    keyboard.append(
        [
            InlineKeyboardButton(text=_("← Рабочий режим"), callback_data="staffhome"),
            InlineKeyboardButton(text=_("Обновить"), callback_data=f"staffnotif:p:{page}"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def _hex(value) -> str:
    """A UUID as 32 hex digits: callback data must stay within 64 bytes."""

    return str(value).replace("-", "")


def build_staff_notification_item_keyboard(
    *,
    notification_id: str,
    category: str,
    order_public_id: str | None = None,
    page: int | None = None,
    order_action: str = "accept",
    can_take_call: bool = False,
) -> InlineKeyboardMarkup:
    """Buttons of one notification. ``page`` is set when it was opened from the
    list: then «Прочитано» and «←» lead back to that page.

    ``order_action``: ``"accept"`` for a new order that can go into work,
    ``"check"`` when a transfer must be checked first, ``""`` for neither.
    """

    notification_hex = _hex(notification_id)
    suffix = f":{page}" if page is not None else ""
    keyboard: list[list[InlineKeyboardButton]] = []
    if can_take_call:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=_("🏃 Иду"),
                    callback_data=f"staffcalltake:{notification_hex}{suffix}",
                )
            ]
        )
    if category == "order_created" and order_public_id and order_action == "accept":
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=_("Взять в работу"),
                    callback_data=f"staffnotifaccept:{notification_hex}:{order_public_id}",
                ),
                InlineKeyboardButton(
                    text=_("Открыть заказ"),
                    callback_data=f"staffnotiforder:{order_public_id}",
                ),
            ]
        )
    elif order_public_id and order_action == "check":
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=_("💳 Проверить оплату"),
                    callback_data=f"staffnotiforder:{order_public_id}",
                )
            ]
        )
    elif order_public_id:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=_("Открыть заказ"),
                    callback_data=f"staffnotiforder:{order_public_id}",
                )
            ]
        )
    elif category == "billing_request":
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=_("Открыть счета"),
                    callback_data="staffbill:list",
                )
            ]
        )
    read_row = [
        InlineKeyboardButton(
            text=_("Прочитано"),
            callback_data=f"staffnotifread:{notification_hex}{suffix}",
        )
    ]
    if page is not None:
        read_row.insert(
            0,
            InlineKeyboardButton(
                text=_("← К уведомлениям"), callback_data=f"staffnotif:p:{page}"
            ),
        )
    keyboard.append(read_row)
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def build_staff_home_keyboard(
    *,
    can_quick_sale: bool = False,
    can_view_day_report: bool = False,
    can_manage_billing: bool = False,
    can_view_orders: bool = True,
    can_view_tables: bool = False,
    can_invite: bool = False,
) -> InlineKeyboardMarkup:
    first_row = []
    if can_view_orders:
        first_row.append(
            InlineKeyboardButton(
                text=_("Открытые заказы"),
                callback_data="stafforders:refresh",
            )
        )
    first_row.append(
        InlineKeyboardButton(
            text=_("Уведомления"),
            callback_data="staffnotif:refresh",
        )
    )
    inline_keyboard = [first_row]
    billing_row = []
    if can_view_tables:
        billing_row.append(
            InlineKeyboardButton(
                text=_("Столы"),
                callback_data="stafftables:refresh",
            )
        )
    if can_manage_billing:
        billing_row.append(
            InlineKeyboardButton(
                text=_("Счета и оплаты"),
                callback_data="staffbill:list",
            )
        )
    if billing_row:
        inline_keyboard.append(billing_row)
    if can_quick_sale:
        inline_keyboard.append(
            [
                InlineKeyboardButton(
                    text=_("Быстрая продажа"),
                    callback_data="staffsale:start",
                )
            ]
        )
    if can_view_day_report:
        inline_keyboard.append(
            [
                InlineKeyboardButton(
                    text=_("Отчёт дня"),
                    callback_data="staffreport:today",
                )
            ]
        )
    if can_invite:
        # Staff invite links (PQ-046): bot/handlers/staff_invites.py.
        inline_keyboard.append(
            [InlineKeyboardButton(text=_("👥 Пригласить сотрудника"), callback_data="staffinv")]
        )
    return InlineKeyboardMarkup(inline_keyboard=inline_keyboard)


def build_staff_report_summary_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_("Счета"),
                    callback_data="staffreportsec:bills:1",
                ),
                InlineKeyboardButton(
                    text=_("Быстрые продажи"),
                    callback_data="staffreportsec:walkins:1",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_("Столы"),
                    callback_data="staffreportsec:tables:1",
                ),
                InlineKeyboardButton(
                    text=_("Не закрыто"),
                    callback_data="staffreportsec:tails:1",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_("Обновить сводку"),
                    callback_data="staffreport:today",
                )
            ],
        ]
    )


def build_staff_report_section_keyboard(
    *,
    section: str,
    page: int,
    total_pages: int,
    item_shortcuts: list[tuple[str, str]] | None = None,
) -> InlineKeyboardMarkup:
    inline_keyboard: list[list[InlineKeyboardButton]] = []
    for label, callback_data in item_shortcuts or []:
        inline_keyboard.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=callback_data,
                )
            ]
        )

    nav_row: list[InlineKeyboardButton] = []
    if page > 1:
        nav_row.append(
            InlineKeyboardButton(
                text=_("← Назад"),
                callback_data=f"staffreportsec:{section}:{page - 1}",
            )
        )
    if page < total_pages:
        nav_row.append(
            InlineKeyboardButton(
                text=_("Дальше →"),
                callback_data=f"staffreportsec:{section}:{page + 1}",
            )
        )
    if nav_row:
        inline_keyboard.append(nav_row)

    inline_keyboard.append(
        [
            InlineKeyboardButton(
                text=_("К сводке дня"),
                callback_data="staffreport:today",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=inline_keyboard)


def build_staff_report_walkin_detail_keyboard(*, section: str, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_("К списку продаж"),
                    callback_data=f"staffreportsec:{section}:{page}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=_("К сводке дня"),
                    callback_data="staffreport:today",
                )
            ],
        ]
    )


def build_staff_report_table_detail_keyboard(*, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_("К списку столов"),
                    callback_data=f"staffreportsec:tables:{page}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=_("К сводке дня"),
                    callback_data="staffreport:today",
                )
            ],
        ]
    )


def build_staff_sale_keyboard(
    *,
    categories: list[tuple[str, str]],
    selected_category_id: str | None,
    category_items: list[tuple[str, str, str]],
    draft_items: list[tuple[str, str, int]],
    can_confirm: bool,
    has_comment: bool,
    can_redeem: bool = False,
    redeem_active: bool = False,
) -> InlineKeyboardMarkup:
    keyboard: list[list[InlineKeyboardButton]] = []
    category_row: list[InlineKeyboardButton] = []
    for category_id, label in categories:
        prefix = "• " if category_id == selected_category_id else ""
        category_row.append(
            InlineKeyboardButton(
                text=f"{prefix}{label}",
                callback_data=f"staffsale:category:{category_id}",
            )
        )
        if len(category_row) == 2:
            keyboard.append(category_row)
            category_row = []
    if category_row:
        keyboard.append(category_row)

    for item_id, label, price, *extra in category_items:
        # An item with variants or add-ons opens its choice (catalog step 2).
        callback = extra[0] if extra and extra[0] else f"staffsale:add:{item_id}"
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=_("➕ {label} — {price} грн", label=label, price=price),
                    callback_data=callback,
                )
            ]
        )

    for item_id, label, quantity, *extra in draft_items:
        # A line with a choice goes by its number in the draft: its key is
        # longer than Telegram's 64 bytes.
        add_callback, sub_callback = extra if extra else (
            f"staffsale:add:{item_id}", f"staffsale:sub:{item_id}"
        )
        keyboard.append(
            [
                InlineKeyboardButton(
                    text="➖",
                    callback_data=sub_callback,
                ),
                InlineKeyboardButton(
                    text=f"{label} × {quantity}",
                    callback_data="staffsale:noop",
                ),
                InlineKeyboardButton(
                    text="➕",
                    callback_data=add_callback,
                ),
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                text=_("Изменить код"),
                callback_data="staffsale:change_customer",
            ),
            InlineKeyboardButton(
                text=_("Комментарий") if not has_comment else _("Изменить комментарий"),
                callback_data="staffsale:comment",
            ),
        ]
    )
    if has_comment:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=_("Убрать комментарий"),
                    callback_data="staffsale:comment_clear",
                )
            ]
        )
    if can_redeem:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=(
                        _("Отменить списание бонусов")
                        if redeem_active
                        else _("Списать бонусы")
                    ),
                    callback_data="staffsale:redeem",
                )
            ]
        )
    keyboard.append(
        [
            InlineKeyboardButton(
                text=_("Очистить"),
                callback_data="staffsale:clear",
            ),
            InlineKeyboardButton(
                text=_("Отмена"),
                callback_data="staffsale:cancel",
            ),
        ]
    )
    keyboard.append(
        [
            InlineKeyboardButton(
                text=_("Подтвердить продажу") if can_confirm else _("Добавьте позиции"),
                callback_data="staffsale:confirm" if can_confirm else "staffsale:noop",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def build_staff_billing_overview_keyboard(
    *,
    rows: list[tuple[str, str]] | None = None,
    unbilled_order_shortcuts: list[tuple[str, str, str | None]] | None = None,
    page: int = 0,
    pages: int = 1,
    can_quick_sale: bool = False,
    can_view_day_report: bool = False,
) -> InlineKeyboardMarkup:
    """Bill requests, open bills and unbilled orders as one list.

    ``rows`` are (callback data, label); ``unbilled_order_shortcuts`` are
    (order public id, label, table id) with a «Стол» button next to them.
    """

    inline_keyboard = [
        [InlineKeyboardButton(text=label, callback_data=callback_data)]
        for callback_data, label in rows or []
    ]
    for order_public_id, label, table_id in unbilled_order_shortcuts or []:
        row = [
            InlineKeyboardButton(
                text=label,
                callback_data=f"stafforderopen:{order_public_id}",
            )
        ]
        if table_id:
            row.append(
                InlineKeyboardButton(
                    text=_("Стол"),
                    callback_data=f"stafftableopen:{table_id}",
                )
            )
        inline_keyboard.append(row)
    pager = pager_row("staffbill", page, pages)
    if pager:
        inline_keyboard.append(pager)
    inline_keyboard.append(
        [
            InlineKeyboardButton(text=_("← Рабочий режим"), callback_data="staffhome"),
            InlineKeyboardButton(text=_("Обновить"), callback_data=f"staffbill:p:{page}"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=inline_keyboard)


def build_staff_bill_actions_keyboard(
    *,
    bill_public_id: str,
    can_issue: bool,
    can_take_payment: bool,
    can_pay_with_bonuses: bool = False,
    can_edit: bool = False,
    can_void: bool = False,
) -> InlineKeyboardMarkup:
    keyboard: list[list[InlineKeyboardButton]] = []
    if can_issue:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=_("Выдать счёт"),
                    callback_data=f"staffbillissue:{bill_public_id}",
                )
            ]
        )
    if can_pay_with_bonuses:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=_("Оплатить бонусами"),
                    callback_data=f"staffbillpaybonus:{bill_public_id}",
                )
            ]
        )
    if can_take_payment:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=_("Наличные"),
                    callback_data=f"staffbillpaycash:{bill_public_id}",
                ),
                InlineKeyboardButton(
                    text=_("Терминал"),
                    callback_data=f"staffbillpayterminal:{bill_public_id}",
                ),
            ]
        )
    change_row: list[InlineKeyboardButton] = []
    if can_edit:
        change_row.append(
            InlineKeyboardButton(
                text=_("Изменить счёт"),
                callback_data=f"staffbilledit:{bill_public_id}",
            )
        )
    if can_void:
        change_row.append(
            InlineKeyboardButton(
                text=_("Аннулировать"),
                callback_data=f"staffbillvoid:{bill_public_id}",
            )
        )
    if change_row:
        keyboard.append(change_row)
    keyboard.append(
        [
            InlineKeyboardButton(text=_("← К счетам"), callback_data="staffbill:list"),
            InlineKeyboardButton(
                text=_("Обновить"),
                callback_data=f"staffbillrefresh:{bill_public_id}",
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


# Short codes keep callback data under Telegram's 64-byte limit.
ADJUSTMENT_REASON_BUTTONS = (
    ("e", N_("Ошибка ввода")),
    ("g", N_("Отказ гостя")),
    ("c", N_("Комплимент заведения")),
    ("o", N_("Другое")),
)


def _button_label(text: str, limit: int = 60) -> str:
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def build_staff_bill_edit_keyboard(
    *,
    bill_public_id: str,
    line_actions: list[tuple[str, str]],
    order_actions: list[tuple[str, str]],
) -> InlineKeyboardMarkup:
    keyboard: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=_button_label(label),
                callback_data=f"staffbillrm:{order_item_id}",
            )
        ]
        for order_item_id, label in line_actions
    ]
    keyboard.extend(
        [
            InlineKeyboardButton(
                text=_button_label(label),
                callback_data=f"staffbilloc:{order_public_id}",
            )
        ]
        for order_public_id, label in order_actions
    )
    keyboard.append(
        [
            InlineKeyboardButton(
                text=_("Назад к счёту"),
                callback_data=f"staffbillrefresh:{bill_public_id}",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def build_staff_adjustment_reason_keyboard(
    *,
    callback_prefix: str,
    bill_public_id: str,
) -> InlineKeyboardMarkup:
    """Reason buttons; each sends ``{callback_prefix}:{code}``."""

    keyboard = [
        [
            InlineKeyboardButton(
                text=_(label),
                callback_data=f"{callback_prefix}:{code}",
            )
        ]
        for code, label in ADJUSTMENT_REASON_BUTTONS
    ]
    keyboard.append(
        [
            InlineKeyboardButton(
                text=_("Отмена"),
                callback_data=f"staffbillrefresh:{bill_public_id}",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def build_staff_adjustment_done_keyboard(
    *,
    bill_public_id: str,
    adjustment_id: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_("Добавить комментарий"),
                    callback_data=f"staffadjc:{adjustment_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=_("Открыть счёт"),
                    callback_data=f"staffbillrefresh:{bill_public_id}",
                )
            ],
        ]
    )


def build_staff_table_actions_keyboard(
    *,
    table_id: str,
    can_create_shared_bill: bool,
    can_create_personal_bills: bool,
    session_actions: list[tuple[str, str]] | None = None,
    visit_actions: list[tuple[str, str, bool]] | None = None,
) -> InlineKeyboardMarkup:
    """Table card actions; ``visit_actions`` are (visit id, title, has unbilled orders)."""

    keyboard: list[list[InlineKeyboardButton]] = []
    for visit_id, title, can_bill in visit_actions or []:
        visit_hex = str(visit_id).replace("-", "")
        row = []
        if can_bill:
            row.extend(
                [
                    InlineKeyboardButton(
                        text=_("{title}: общий счёт", title=title),
                        callback_data=f"stafftvshared:{visit_hex}",
                    ),
                    InlineKeyboardButton(
                        text=_("По гостям"),
                        callback_data=f"stafftvpersonal:{visit_hex}",
                    ),
                ]
            )
        row.append(
            InlineKeyboardButton(
                text=_("Закрыть визит") if can_bill else _("{title}: закрыть", title=title),
                callback_data=f"stafftvclose:{visit_hex}",
            )
        )
        keyboard.append(row)
    if can_create_shared_bill or can_create_personal_bills:
        row: list[InlineKeyboardButton] = []
        if can_create_shared_bill:
            row.append(
                InlineKeyboardButton(
                    text=_("Общий счёт"),
                    callback_data=f"stafftablebillshared:{table_id}",
                )
            )
        if can_create_personal_bills:
            row.append(
                InlineKeyboardButton(
                    text=_("По гостям"),
                    callback_data=f"stafftablebillpersonal:{table_id}",
                )
            )
        keyboard.append(row)
    for session_id, label in session_actions or []:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"staffsessionclose:{session_id}",
                )
            ]
        )
    keyboard.append(
        [
            InlineKeyboardButton(text=_("← К столам"), callback_data="stafftables:refresh"),
            InlineKeyboardButton(
                text=_("Обновить"),
                callback_data=f"stafftableopen:{table_id}",
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def build_staff_tables_overview_keyboard(
    *,
    table_shortcuts: list[tuple[str, str]] | None = None,
    page: int = 0,
    pages: int = 1,
    can_quick_sale: bool = False,
    can_view_day_report: bool = False,
) -> InlineKeyboardMarkup:
    inline_keyboard = [
        [
            InlineKeyboardButton(
                text=label,
                callback_data=f"stafftableopen:{table_id}",
            )
        ]
        for table_id, label in table_shortcuts or []
    ]
    pager = pager_row("stafftables", page, pages)
    if pager:
        inline_keyboard.append(pager)
    inline_keyboard.append(
        [
            InlineKeyboardButton(text=_("← Рабочий режим"), callback_data="staffhome"),
            InlineKeyboardButton(text=_("Обновить"), callback_data=f"stafftables:p:{page}"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=inline_keyboard)


def build_staff_billing_request_actions_keyboard(
    *,
    request_id: str,
    bill_public_id: str | None = None,
    can_process_request: bool = True,
) -> InlineKeyboardMarkup:
    keyboard: list[list[InlineKeyboardButton]] = []
    if bill_public_id:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=_("Открыть счёт"),
                    callback_data=f"staffbillopen:{bill_public_id}",
                )
            ]
        )
    if can_process_request:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=_("Обработано"),
                    callback_data=f"staffbillreqprocess:{request_id}",
                ),
                InlineKeyboardButton(
                    text=_("Отменить"),
                    callback_data=f"staffbillreqcancel:{request_id}",
                ),
            ]
        )
    keyboard.append(
        [
            InlineKeyboardButton(text=_("← К счетам"), callback_data="staffbill:list"),
            InlineKeyboardButton(
                text=_("Обновить"),
                callback_data=f"staffbillreqrefresh:{request_id}",
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)
