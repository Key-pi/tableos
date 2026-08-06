from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

STATUS_LABELS = {
    "accepted": "Взять в работу",
    "preparing": "Готовим",
    "ready": "Готово",
    "delivering": "Нести",
    "completed": "Завершить",
    "canceled": "Отменить",
}


def build_staff_order_actions_keyboard(
    *,
    order_public_id: str,
    available_statuses: list[str],
) -> InlineKeyboardMarkup | None:
    status_buttons = [
        InlineKeyboardButton(
            text=STATUS_LABELS.get(status, status),
            callback_data=f"stafforder:{order_public_id}:{status}",
        )
        for status in available_statuses
    ]
    keyboard = []
    if status_buttons:
        for index in range(0, len(status_buttons), 2):
            keyboard.append(status_buttons[index:index + 2])
    keyboard.append(
        [
            InlineKeyboardButton(
                text="Обновить карточку",
                callback_data=f"stafforderrefresh:{order_public_id}",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def build_staff_orders_overview_keyboard(
    *,
    order_shortcuts: list[tuple[str, str, bool]] | None = None,
    can_quick_sale: bool = False,
    can_view_day_report: bool = False,
    can_manage_billing: bool = False,
    can_view_tables: bool = False,
) -> InlineKeyboardMarkup:
    inline_keyboard = [
        [
            InlineKeyboardButton(
                text="Обновить список",
                callback_data="stafforders:refresh",
            ),
            InlineKeyboardButton(
                text="Уведомления",
                callback_data="staffnotif:refresh",
            ),
        ],
    ]
    for order_public_id, label, can_accept in order_shortcuts or []:
        row = [
            InlineKeyboardButton(
                text=label,
                callback_data=f"stafforderopen:{order_public_id}",
            )
        ]
        if can_accept:
            row.append(
                InlineKeyboardButton(
                    text="Взять в работу",
                    callback_data=f"stafforder:{order_public_id}:accepted",
                )
            )
        inline_keyboard.append(row)
    billing_row = []
    if can_view_tables:
        billing_row.append(
            InlineKeyboardButton(
                text="Столы",
                callback_data="stafftables:refresh",
            )
        )
    if can_manage_billing:
        billing_row.append(
            InlineKeyboardButton(
                text="Счета и оплаты",
                callback_data="staffbill:list",
            )
        )
    if billing_row:
        inline_keyboard.append(billing_row)
    if can_quick_sale:
        inline_keyboard.append(
            [
                InlineKeyboardButton(
                    text="Быстрая продажа",
                    callback_data="staffsale:start",
                )
            ]
        )
    if can_view_day_report:
        inline_keyboard.append(
            [
                InlineKeyboardButton(
                    text="Отчёт дня",
                    callback_data="staffreport:today",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=inline_keyboard)


def build_staff_sale_code_prompt_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Без кода (Аноним)",
                    callback_data="staffsale:anon",
                )
            ]
        ]
    )


def build_staff_notifications_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Обновить",
                    callback_data="staffnotif:refresh",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Отметить всё прочитанным",
                    callback_data="staffnotif:readall",
                )
            ]
        ]
    )


def build_staff_notification_item_keyboard(
    *,
    notification_id: str,
    category: str,
    order_public_id: str | None = None,
) -> InlineKeyboardMarkup:
    keyboard: list[list[InlineKeyboardButton]] = []
    if category == "order_created" and order_public_id:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text="Взять в работу",
                    callback_data=f"staffnotifaccept:{notification_id}:{order_public_id}",
                ),
                InlineKeyboardButton(
                    text="Открыть заказ",
                    callback_data=f"staffnotiforder:{order_public_id}",
                ),
            ]
        )
    elif order_public_id:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text="Открыть заказ",
                    callback_data=f"staffnotiforder:{order_public_id}",
                )
            ]
        )
    elif category == "billing_request":
        keyboard.append(
            [
                InlineKeyboardButton(
                    text="Открыть счета",
                    callback_data="staffbill:list",
                )
            ]
        )
    keyboard.append(
        [
            InlineKeyboardButton(
                text="Прочитано",
                callback_data=f"staffnotifread:{notification_id}",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def build_staff_home_keyboard(
    *,
    can_quick_sale: bool = False,
    can_view_day_report: bool = False,
    can_manage_billing: bool = False,
    can_view_orders: bool = True,
    can_view_tables: bool = False,
) -> InlineKeyboardMarkup:
    first_row = []
    if can_view_orders:
        first_row.append(
            InlineKeyboardButton(
                text="Открытые заказы",
                callback_data="stafforders:refresh",
            )
        )
    first_row.append(
        InlineKeyboardButton(
            text="Уведомления",
            callback_data="staffnotif:refresh",
        )
    )
    inline_keyboard = [first_row]
    billing_row = []
    if can_view_tables:
        billing_row.append(
            InlineKeyboardButton(
                text="Столы",
                callback_data="stafftables:refresh",
            )
        )
    if can_manage_billing:
        billing_row.append(
            InlineKeyboardButton(
                text="Счета и оплаты",
                callback_data="staffbill:list",
            )
        )
    if billing_row:
        inline_keyboard.append(billing_row)
    if can_quick_sale:
        inline_keyboard.append(
            [
                InlineKeyboardButton(
                    text="Быстрая продажа",
                    callback_data="staffsale:start",
                )
            ]
        )
    if can_view_day_report:
        inline_keyboard.append(
            [
                InlineKeyboardButton(
                    text="Отчёт дня",
                    callback_data="staffreport:today",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=inline_keyboard)


def build_staff_report_summary_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Счета",
                    callback_data="staffreportsec:bills:1",
                ),
                InlineKeyboardButton(
                    text="Быстрые продажи",
                    callback_data="staffreportsec:walkins:1",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Столы",
                    callback_data="staffreportsec:tables:1",
                ),
                InlineKeyboardButton(
                    text="Хвосты",
                    callback_data="staffreportsec:tails:1",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Обновить сводку",
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
                text="← Назад",
                callback_data=f"staffreportsec:{section}:{page - 1}",
            )
        )
    if page < total_pages:
        nav_row.append(
            InlineKeyboardButton(
                text="Дальше →",
                callback_data=f"staffreportsec:{section}:{page + 1}",
            )
        )
    if nav_row:
        inline_keyboard.append(nav_row)

    inline_keyboard.append(
        [
            InlineKeyboardButton(
                text="К сводке дня",
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
                    text="К списку продаж",
                    callback_data=f"staffreportsec:{section}:{page}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="К сводке дня",
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
                    text="К списку столов",
                    callback_data=f"staffreportsec:tables:{page}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="К сводке дня",
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
    for category_id, label in categories:
        prefix = "• " if category_id == selected_category_id else ""
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=f"{prefix}{label}",
                    callback_data=f"staffsale:category:{category_id}",
                )
            ]
        )

    for item_id, label, price in category_items:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=f"＋ {label} • {price} грн",
                    callback_data=f"staffsale:add:{item_id}",
                )
            ]
        )

    for item_id, label, quantity in draft_items:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text="－",
                    callback_data=f"staffsale:sub:{item_id}",
                ),
                InlineKeyboardButton(
                    text=f"{label} x{quantity}",
                    callback_data="staffsale:noop",
                ),
                InlineKeyboardButton(
                    text="＋",
                    callback_data=f"staffsale:add:{item_id}",
                ),
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                text="Изменить код",
                callback_data="staffsale:change_customer",
            ),
            InlineKeyboardButton(
                text="Комментарий" if not has_comment else "Изменить комментарий",
                callback_data="staffsale:comment",
            ),
        ]
    )
    if has_comment:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text="Убрать комментарий",
                    callback_data="staffsale:comment_clear",
                )
            ]
        )
    if can_redeem:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=(
                        "Отменить списание бонусов"
                        if redeem_active
                        else "Списать бонусы"
                    ),
                    callback_data="staffsale:redeem",
                )
            ]
        )
    keyboard.append(
        [
            InlineKeyboardButton(
                text="Очистить",
                callback_data="staffsale:clear",
            ),
            InlineKeyboardButton(
                text="Отмена",
                callback_data="staffsale:cancel",
            ),
        ]
    )
    keyboard.append(
        [
            InlineKeyboardButton(
                text="Подтвердить продажу" if can_confirm else "Добавьте позиции",
                callback_data="staffsale:confirm" if can_confirm else "staffsale:noop",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def build_staff_billing_overview_keyboard(
    *,
    unbilled_order_shortcuts: list[tuple[str, str, str | None]] | None = None,
    can_quick_sale: bool = False,
    can_view_day_report: bool = False,
) -> InlineKeyboardMarkup:
    inline_keyboard = [
        [
            InlineKeyboardButton(
                text="Обновить",
                callback_data="staffbill:list",
            ),
            InlineKeyboardButton(
                text="Заказы",
                callback_data="stafforders:refresh",
            ),
        ],
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
                    text="Стол",
                    callback_data=f"stafftableopen:{table_id}",
                )
            )
        inline_keyboard.append(row)
    if can_quick_sale:
        inline_keyboard.append(
            [
                InlineKeyboardButton(
                    text="Быстрая продажа",
                    callback_data="staffsale:start",
                )
            ]
        )
    if can_view_day_report:
        inline_keyboard.append(
            [
                InlineKeyboardButton(
                    text="Отчёт дня",
                    callback_data="staffreport:today",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=inline_keyboard)


def build_staff_bill_actions_keyboard(
    *,
    bill_public_id: str,
    can_issue: bool,
    can_take_payment: bool,
    can_pay_with_bonuses: bool = False,
) -> InlineKeyboardMarkup:
    keyboard: list[list[InlineKeyboardButton]] = []
    if can_issue:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text="Выдать счёт",
                    callback_data=f"staffbillissue:{bill_public_id}",
                )
            ]
        )
    if can_pay_with_bonuses:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text="Оплатить бонусами",
                    callback_data=f"staffbillpaybonus:{bill_public_id}",
                )
            ]
        )
    if can_take_payment:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text="Наличные",
                    callback_data=f"staffbillpaycash:{bill_public_id}",
                ),
                InlineKeyboardButton(
                    text="Терминал",
                    callback_data=f"staffbillpayterminal:{bill_public_id}",
                ),
            ]
        )
    keyboard.append(
        [
            InlineKeyboardButton(
                text="Обновить счёт",
                callback_data=f"staffbillrefresh:{bill_public_id}",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def build_staff_table_actions_keyboard(
    *,
    table_id: str,
    can_create_shared_bill: bool,
    can_create_personal_bills: bool,
    session_actions: list[tuple[str, str]] | None = None,
) -> InlineKeyboardMarkup:
    keyboard: list[list[InlineKeyboardButton]] = []
    if can_create_shared_bill or can_create_personal_bills:
        row: list[InlineKeyboardButton] = []
        if can_create_shared_bill:
            row.append(
                InlineKeyboardButton(
                    text="Общий счёт",
                    callback_data=f"stafftablebillshared:{table_id}",
                )
            )
        if can_create_personal_bills:
            row.append(
                InlineKeyboardButton(
                    text="По гостям",
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
            InlineKeyboardButton(
                text="Обновить стол",
                callback_data=f"stafftableopen:{table_id}",
            ),
            InlineKeyboardButton(
                text="Счета и оплаты",
                callback_data="staffbill:list",
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def build_staff_tables_overview_keyboard(
    *,
    table_shortcuts: list[tuple[str, str]] | None = None,
    can_quick_sale: bool = False,
    can_view_day_report: bool = False,
) -> InlineKeyboardMarkup:
    inline_keyboard = [
        [
            InlineKeyboardButton(
                text="Обновить",
                callback_data="stafftables:refresh",
            ),
            InlineKeyboardButton(
                text="Заказы",
                callback_data="stafforders:refresh",
            ),
            InlineKeyboardButton(
                text="Счета",
                callback_data="staffbill:list",
            ),
        ],
    ]
    for table_id, label in table_shortcuts or []:
        inline_keyboard.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"stafftableopen:{table_id}",
                )
            ]
        )
    if can_quick_sale:
        inline_keyboard.append(
            [
                InlineKeyboardButton(
                    text="Быстрая продажа",
                    callback_data="staffsale:start",
                )
            ]
        )
    if can_view_day_report:
        inline_keyboard.append(
            [
                InlineKeyboardButton(
                    text="Отчёт дня",
                    callback_data="staffreport:today",
                )
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
                    text="Открыть счёт",
                    callback_data=f"staffbillopen:{bill_public_id}",
                )
            ]
        )
    if can_process_request:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text="Обработано",
                    callback_data=f"staffbillreqprocess:{request_id}",
                ),
                InlineKeyboardButton(
                    text="Отменить",
                    callback_data=f"staffbillreqcancel:{request_id}",
                ),
            ]
        )
    keyboard.append(
        [
            InlineKeyboardButton(
                text="Обновить запрос",
                callback_data=f"staffbillreqrefresh:{request_id}",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)
