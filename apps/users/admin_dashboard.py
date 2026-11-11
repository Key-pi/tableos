"""The Admin start page of a venue (admin step 2, owner 2026-11-09, PQ-050).

«Требует внимания» first — what may lose money, then stock, the bot and the
kitchen screens; «Сегодня» — the day's numbers; quick actions; «Что ещё
настроить». A part shows only to whoever may open the page it leads to (that
page's Admin rights) and only while its module is on. The counts are
``apps/analytics/dashboard.py``; this module adds rights, texts and links.
The platform keeps Unfold's own start page (every table by app).

Unfold calls ``dashboard`` for the index (``UNFOLD["DASHBOARD_CALLBACK"]``);
``templates/admin/index.html`` draws it.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from urllib.parse import urlencode

from django.utils import timezone
from django.utils.formats import number_format

from apps.users.admin_navigation import _model_admin, _modules_on, _scope, _url
from core.i18n import N_, _

# A new order waiting longer than this is shown in red.
NEW_ORDER_LATE_MINUTES = 10


def _can(request, label: str, action: str = "view") -> bool:
    model_admin = _model_admin(label)
    if model_admin is None:
        return False
    return getattr(model_admin, f"has_{action}_permission")(request)


def _module(request, flag: str) -> bool:
    modules = _modules_on(request)
    return modules is None or flag in modules


def _list(label: str, **filters) -> str:
    url = _url(_model_admin(label), "changelist")
    return f"{url}?{urlencode(filters)}" if filters else url


def _minutes(since: datetime, now: datetime) -> int:
    return max(int((now - since).total_seconds() // 60), 0)


def _age(minutes: int) -> str:
    if minutes < 60:
        return _("{count} мин", count=minutes)
    if minutes < 48 * 60:
        return _("{count} ч", count=minutes // 60)
    return _("{count} дн.", count=minutes // (24 * 60))


def _alert(tone, icon, title, text, links) -> dict:
    return {"tone": tone, "icon": icon, "title": title, "text": text, "links": links}


def _attention(request, partner_id, now) -> tuple[list[dict], bool]:
    """The alerts, and whether this person may see any kind of them."""

    from apps.analytics import dashboard

    alerts: list[dict] = []
    watched = False
    orders = _can(request, "orders.order")
    if orders:
        watched = True
        transfers = dashboard.transfers_to_confirm(partner_id)
        if transfers:
            wait = dashboard.transfer_wait_minutes(partner_id)
            if wait:
                left = wait - _minutes(transfers.since, now)
                text = (
                    _("Первый отменится автоматически через {left}.", left=_age(left))
                    if left > 0 else _("Первый уже отменяется автоматически.")
                )
            else:
                text = _("Пока перевод не подтверждён, заказ не идёт в работу.")
            alerts.append(_alert(
                "danger", "account_balance",
                _("Переводы ждут подтверждения: {count}", count=transfers.count), text,
                [(_("Открыть заказы"), _list(
                    "orders.order", status__exact="new", payment_timing__exact="prepaid",
                    paid_at__isnull="True",
                ))],
            ))
    if _can(request, "billing.billingrequest"):
        watched = True
        bills = dashboard.guests_waiting_for_bill(partner_id)
        if bills:
            alerts.append(_alert(
                "danger", "request_quote",
                _("Гости ждут счёт: {count}", count=bills.count),
                _("Самый давний запрос — {age} назад.", age=_age(_minutes(bills.since, now))),
                [(_("Открыть запросы"), _list(
                    "billing.billingrequest", status__in="open,auto_prepared"
                ))],
            ))
    if orders:
        receipts = dashboard.receipts_to_record(partner_id)
        if receipts:
            alerts.append(_alert(
                "danger", "payments",
                _("Оплата при получении не отмечена: {count}", count=receipts.count),
                _(
                    "Заказ у курьера, на почте или уже у гостя, а деньги не записаны. "
                    "Самый давний — {age} назад.",
                    age=_age(_minutes(receipts.since, now)),
                ),
                [(_("Открыть заказы"), _list(
                    "orders.order", payment_timing__exact="on_receipt",
                    paid_at__isnull="True", status__in="delivering,completed",
                ))],
            ))
        new = dashboard.new_orders(partner_id)
        if new:
            waited = _minutes(new.since, now)
            alerts.append(_alert(
                "danger" if waited > NEW_ORDER_LATE_MINUTES else "warning", "receipt_long",
                _("Новые заказы: {count}", count=new.count),
                _("Самый давний ждёт {age}.", age=_age(waited)),
                [(_("Открыть заказы"), _list(
                    "orders.order", status__exact="new", payment_timing__in=",on_receipt"
                ))],
            ))
        parcels = dashboard.parcels_to_send(partner_id)
        if parcels:
            alerts.append(_alert(
                "warning", "local_shipping",
                _("Посылки ждут отправки: {count}", count=parcels.count),
                _(
                    "Приняты, но ТТН ещё нет. Самая давняя — {age} назад.",
                    age=_age(_minutes(parcels.since, now)),
                ),
                [(_("Открыть заказы"), _list(
                    "orders.order", channel__exact="shipping",
                    status__in="accepted,preparing,ready",
                ))],
            ))
    if _can(request, "menu.menuitem"):  # stock_problems is empty while stock is off
        watched = True
        stock = dashboard.stock_problems(partner_id)
        if stock:
            links = []
            for count, text, state in (
                (stock.below_zero, _("Ниже нуля — пересчитайте: {count}", count=stock.below_zero),
                 "below_zero"),
                (stock.out, _("Нет в наличии: {count}", count=stock.out), "out"),
                (stock.low, _("Осталось мало: {count}", count=stock.low), "low"),
            ):
                if count:
                    links.append((text, _list("menu.menuitem", stock=state)))
            tone = "danger" if stock.below_zero else "warning" if stock.out else "info"
            alerts.append(_alert(tone, "inventory_2", _("Остатки"), "", links))
    if _can(request, "partners.botinstance"):
        watched = True
        bot = dashboard.broken_bot(partner_id)
        if bot is not None:
            own = str(bot.partner_id) == str(partner_id)
            alerts.append(_alert(
                "danger", "smart_toy", _("Бот не отвечает гостям"),
                _("@{username}: {state}", username=bot.username, state=bot.runtime_state_label)
                + ("" if own else " " + _("Это бот сети — его настраивает владелец сети.")),
                [(_("Открыть ботов"), _list("partners.botinstance"))] if own else [],
            ))
    if _can(request, "orders.kitchenscreen") and _module(request, "module_kitchen_screen_enabled"):
        watched = True
        from apps.orders.kitchen import missing_screen_warnings

        for warning in missing_screen_warnings(partner_id):
            alerts.append(_alert(
                "info", "soup_kitchen", _("Экраны кухни и бара"), warning,
                [(_("Открыть экраны"), _list("orders.kitchenscreen"))],
            ))
    return alerts, watched


def _today(request, partner_id) -> dict | None:
    if not _can(request, "analytics.partnerdailymetric"):
        return None
    from apps.analytics.services import build_daily_operations_report

    report = build_daily_operations_report(partner_id=partner_id)
    sales = report.paid_bills_count + report.walk_in_sales_count
    average = report.total_revenue / sales if sales else None
    return {
        "revenue": _money(report.total_revenue),
        "refunds": _money(report.refunds_total) if report.refunds_total else "",
        "orders": report.created_orders_count,
        "sales": sales,
        "average": _money(average) if average is not None else "—",
        "open_orders": report.open_orders_count,
        "open_bills": report.open_bills_count,
        "link": _list("analytics.partnerdailymetric"),
    }


def _money(amount: Decimal) -> str:
    return _("{amount} грн", amount=number_format(amount, 2, force_grouping=True))


QUICK_ACTIONS = (
    ("bonuses.walkinsale", "point_of_sale", N_("Быстрая продажа"), "module_quick_sale_enabled"),
    ("menu.menuitem", "restaurant_menu", N_("Новая позиция"), ""),
    ("notifications.broadcastcampaign", "campaign", N_("Рассылка"), ""),
    ("employees.staffinvite", "person_add", N_("Пригласить сотрудника"), ""),
    ("analytics.trafficsource", "link", N_("Источник переходов"), ""),
    ("tables.table", "table_restaurant", N_("Новый стол"), "module_tables_enabled"),
    ("tables.reservation", "event", N_("Новая бронь"), "module_reservations_enabled"),
)


def _actions(request) -> list[dict]:
    return [
        {"title": _(title), "icon": icon, "link": _url(_model_admin(label), "add")}
        for label, icon, title, module in QUICK_ACTIONS
        if (not module or _module(request, module)) and _can(request, label, "add")
    ]


def _setup(request, partner_id) -> list[dict]:
    from apps.analytics import dashboard
    from core.admin_mixins import is_partner_owner_or_superuser

    def settings_link():
        model_admin = _model_admin("partners.partnerbotsettings")
        pk = model_admin.get_queryset(request).values_list("pk", flat=True).first()
        return _url(model_admin, "change", pk) if pk else None

    def card_link():
        return _url(_model_admin("partners.partner"), "change", partner_id)

    steps = {
        dashboard.SETUP_BOT: (
            _("Подключите бота"),
            _("Создайте бота в @BotFather и вставьте его токен."),
            lambda: _url(_model_admin("partners.botinstance"), "add")
            if _can(request, "partners.botinstance", "add") else None,
            lambda: _can(request, "partners.botinstance"),
        ),
        dashboard.SETUP_MENU: (
            _("Добавьте позиции меню"),
            _("Без них гостю нечего заказать."),
            lambda: _url(_model_admin("menu.menuitem"), "add"),
            lambda: _can(request, "menu.menuitem", "add"),
        ),
        dashboard.SETUP_PAYMENT_DETAILS: (
            _("Укажите реквизиты для перевода"),
            _("Без реквизитов оплата переводом заранее гостям не предлагается."),
            settings_link,
            lambda: is_partner_owner_or_superuser(request.user),
        ),
        dashboard.SETUP_PAYMENT_OPTIONS: (
            _("Выберите способы оплаты"),
            _(
                "У включённого способа заказа нет ни оплаты переводом, ни оплаты при "
                "получении — оформить такой заказ гость не сможет."
            ),
            settings_link,
            lambda: _can(request, "partners.partnerbotsettings", "change"),
        ),
        dashboard.SETUP_HOURS: (
            _("Укажите часы работы"),
            _("Без них самовывоз и доставка принимаются круглосуточно."),
            lambda: _url(_model_admin("partners.openinghours"), "add"),
            lambda: _can(request, "partners.openinghours", "add"),
        ),
        dashboard.SETUP_TABLES: (
            _("Добавьте столы"),
            _("У каждого стола свой QR-код — по нему гость заказывает за столом."),
            lambda: _url(_model_admin("tables.table"), "add"),
            lambda: _can(request, "tables.table", "add"),
        ),
        dashboard.SETUP_STAFF: (
            _("Добавьте сотрудников"),
            _("Кто будет принимать заказы в боте: пригласите их ссылкой."),
            lambda: _url(_model_admin("employees.staffinvite"), "add"),
            lambda: _can(request, "employees.staffinvite", "add"),
        ),
        dashboard.SETUP_CARD: (
            _("Заполните адрес и телефон"),
            _("Гости видят их в профиле заведения и при самовывозе."),
            card_link,
            lambda: _can(request, "partners.partner", "change"),
        ),
    }
    result = []
    for step in dashboard.setup_steps(partner_id):
        title, text, link, allowed = steps[step]
        if allowed():
            result.append({"title": title, "text": text, "link": link()})
    return result


def dashboard(request, context: dict) -> dict:
    """``UNFOLD["DASHBOARD_CALLBACK"]``."""

    partner_id = _scope(request)
    if partner_id is None:
        return context  # the platform: Unfold's start page
    alerts, watched = _attention(request, partner_id, timezone.now())
    context["title"] = _("Сегодня")
    context["venue_dashboard"] = {
        "alerts": alerts,
        "all_clear": watched and not alerts,
        "today": _today(request, partner_id),
        "actions": _actions(request),
        "setup": _setup(request, partner_id),
    }
    return context
