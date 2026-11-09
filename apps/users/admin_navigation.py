"""Admin navigation by the owner's tasks (admin step 1, owner 2026-11-09).

The menu on the left groups pages by what a venue does — orders and bills,
menu, guests and bonuses, marketing, staff, reports, settings — instead of
by Django apps and tables. A page shows only for whoever may open it (the
page's own Admin rights), and module pages only while the module is on.
Pages one can add to carry «+» (``add_link``, drawn by
``templates/unfold/helpers/app_list.html``).

Service tables (order lines, status history, bill lines, deliveries of
broadcasts…) are not in a venue's menu (``ScopedAdminMixin.service_table``):
they open as tabs of the page they belong to (``tabs``). The platform sees
them in its own group and in «All applications».

Unfold calls ``sidebar``, ``tabs`` and ``show_all_applications`` per
request (``UNFOLD`` in ``core/settings/base.py``). Pages are found by model
label in the Admin registry, so nothing here imports the apps' models.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.apps import apps
from django.contrib import admin
from django.urls import NoReverseMatch, reverse

from core.i18n import gettext_lazy as _lazy


@dataclass(frozen=True)
class Page:
    model: str  # "app_label.modelname"
    icon: str
    title: object = None  # the model's plural name by default
    add: bool = False  # «+» next to it
    module: str = ""  # PartnerBotSettings flag that must be on
    single: bool = False  # the venue's one record opens as a form
    needs_record: bool = False  # a venue sees it only when it has its record
    badge: str = ""  # dotted path of a counter (request -> int | "")


@dataclass(frozen=True)
class Link:
    url_name: str
    icon: str
    title: object
    model: str  # whose view right it needs
    active_names: tuple[str, ...] = ()  # URL names it is the open page for


GROUPS = (
    (_lazy("Заказы и счета"), (
        Page("orders.order", "receipt_long", badge="apps.users.admin_navigation.new_orders"),
        Page("billing.bill", "request_quote",
             badge="apps.users.admin_navigation.open_bill_requests"),
        Page("billing.payment", "payments"),
        Page("billing.refund", "currency_exchange"),
        Page("tables.table", "table_restaurant", add=True, module="module_tables_enabled"),
        Page("orders.kitchenscreen", "soup_kitchen", add=True,
             module="module_kitchen_screen_enabled"),
    )),
    (_lazy("Меню"), (
        Page("menu.menuitem", "restaurant_menu", add=True),
        Page("menu.menucategory", "category", add=True),
        Page("menu.modifiergroup", "tune", add=True, module="module_options_enabled"),
        Page("menu.menubadge", "sell", add=True, module="module_showcase_enabled"),
        Page("menu.stockmovement", "inventory_2", module="module_stock_enabled"),
    )),
    (_lazy("Гости и бонусы"), (
        Page("users.guestprofile", "group"),
        Page("bonuses.walkinsale", "point_of_sale", add=True,
             module="module_quick_sale_enabled"),
        Page("bonuses.bonustransaction", "history"),
        Page("bonuses.bonusprogram", "loyalty", add=True),
    )),
    (_lazy("Маркетинг"), (
        Page("notifications.broadcastcampaign", "campaign", add=True),
        Page("analytics.trafficsource", "link", add=True),
    )),
    (_lazy("Персонал"), (
        Page("employees.employeeprofile", "badge", add=True),
        Page("employees.staffinvite", "person_add", add=True),
        Page("users.user", "key", title=_lazy("Входы в Admin"), add=True),
        Page("users.adminaccessprofile", "admin_panel_settings"),
    )),
    (_lazy("Отчёты"), (
        Page("analytics.partnerdailymetric", "monitoring"),
        Page("analytics.sourcevisit", "ads_click"),
        Link("admin:partners_network_changelist", "insights", _lazy("Отчёт сети"),
             "partners.network",
             active_names=("partners_network_changelist", "partners_network_report")),
    )),
    (_lazy("Настройки"), (
        Page("partners.partner", "storefront", title=_lazy("Заведение"), single=True),
        Page("partners.partnerbotsettings", "settings", title=_lazy("Настройки бота"),
             single=True),
        Page("partners.bottext", "chat", add=True),
        Page("partners.openinghours", "schedule", add=True),
        Page("partners.botinstance", "smart_toy"),
        # The network's owner names it and picks its bot (PQ-047).
        Page("partners.network", "hub", title=_lazy("Сеть"), single=True, needs_record=True),
    )),
)

# Seen by the platform only: tables that belong to other pages.
PLATFORM_GROUP = (_lazy("Платформа"), (
    Page("users.telegramaccount", "alternate_email"),
    Page("auth.group", "groups"),
    Page("orders.cart", "shopping_cart"),
    Page("orders.cartitem", "list"),
    Page("orders.orderitem", "list_alt"),
    Page("orders.orderstatushistory", "timeline"),
    Page("billing.billorder", "link"),
    Page("billing.billitem", "list"),
    Page("billing.billadjustment", "edit_note"),
    Page("billing.billingrequest", "notifications_active"),
    Page("tables.tablesession", "event_seat"),
    Page("tables.tablevisit", "group_work"),
    Page("notifications.broadcastdelivery", "outgoing_mail"),
    Page("notifications.staffnotification", "notifications"),
    Page("notifications.notificationpreference", "tune"),
))

# Related pages as tabs over their lists (the service ones open only here).
TABS = (
    ("billing.bill", "billing.billingrequest", "billing.billitem", "billing.billadjustment"),
    ("billing.payment", "billing.refund"),
    ("tables.table", "tables.tablevisit", "tables.tablesession"),
    ("menu.menuitem", "menu.menucategory", "menu.modifiergroup", "menu.menubadge",
     "menu.stockmovement"),
    ("users.guestprofile", "bonuses.walkinsale", "bonuses.bonustransaction",
     "bonuses.bonusprogram", "notifications.notificationpreference"),
    ("notifications.broadcastcampaign", "notifications.broadcastdelivery"),
    ("employees.employeeprofile", "employees.staffinvite", "notifications.staffnotification"),
    ("users.user", "users.adminaccessprofile"),
    ("analytics.partnerdailymetric", "analytics.sourcevisit", "analytics.trafficsource"),
)

TAB_TITLES = {
    "billing.billingrequest": _lazy("Запросы счёта"),
    "billing.billitem": _lazy("Строки счетов"),
    "billing.billadjustment": _lazy("Изменения счетов"),
    "tables.tablevisit": _lazy("Визиты"),
    "tables.tablesession": _lazy("Сессии"),
    "notifications.broadcastdelivery": _lazy("Доставки"),
    "notifications.staffnotification": _lazy("Уведомления персонала"),
    "notifications.notificationpreference": _lazy("Сообщения гостям"),
    "users.user": _lazy("Входы в Admin"),
}

MODULE_OF = {
    page.model: page.module
    for _title, pages in GROUPS for page in pages
    if isinstance(page, Page) and page.module
}


def _model_admin(label: str):
    try:
        model = apps.get_model(label)
    except (LookupError, ValueError):
        return None
    return admin.site._registry.get(model)


def _url(model_admin, view: str, *args) -> str:
    opts = model_admin.model._meta
    return reverse(f"admin:{opts.app_label}_{opts.model_name}_{view}", args=args)


def _scope(request):
    from core.admin_mixins import current_admin_partner_id

    if request.user.is_superuser:
        return None
    return current_admin_partner_id(request)


def _modules_on(request) -> set[str] | None:
    """Module flags on for the venue; None for the platform (all shown)."""

    cached = getattr(request, "_admin_modules_on", False)
    if cached is not False:
        return cached
    partner_id = _scope(request)
    if partner_id is None:
        result = None if request.user.is_superuser else set()
    else:
        flags = sorted(set(MODULE_OF.values()))
        settings = apps.get_model("partners", "PartnerBotSettings")
        row = settings.objects.filter(partner_id=partner_id).values(*flags).first()
        result = {flag for flag, on in (row or {}).items() if on}
    request._admin_modules_on = result
    return result


def _shown(request, label: str, *, menu: bool) -> bool:
    model_admin = _model_admin(label)
    if model_admin is None:
        return False
    allowed = (
        model_admin.has_module_permission(request) if menu
        else model_admin.has_view_permission(request)
    )
    if not allowed:
        return False
    module = MODULE_OF.get(label)
    modules = _modules_on(request)
    return not module or modules is None or module in modules


def _single_link(request, model_admin, *, needs_record: bool) -> str | None:
    """The venue's own record when it has exactly one, else the list
    (None: the venue has no record and the page needs one)."""

    if _scope(request) is not None:
        pks = list(model_admin.get_queryset(request).values_list("pk", flat=True)[:2])
        if len(pks) == 1:
            return _url(model_admin, "change", pks[0])
        if needs_record and not pks:
            return None
    return _url(model_admin, "changelist")


def _item(request, page) -> dict | None:
    if isinstance(page, Link):
        model_admin = _model_admin(page.model)
        if model_admin is None or not model_admin.has_view_permission(request):
            return None
        try:
            link = reverse(page.url_name)
        except NoReverseMatch:
            return None
        return {"title": page.title, "icon": page.icon, "link": link}
    if not _shown(request, page.model, menu=True):
        return None
    model_admin = _model_admin(page.model)
    if page.single:
        link = _single_link(request, model_admin, needs_record=page.needs_record)
        if link is None:
            return None
    else:
        link = _url(model_admin, "changelist")
    item = {
        "title": page.title or model_admin.model._meta.verbose_name_plural,
        "icon": page.icon,
        "link": link,
    }
    if page.add and model_admin.has_add_permission(request):
        item["add_link"] = _url(model_admin, "add")
    if page.badge:
        item["badge"] = page.badge
    return item


def _current(request) -> tuple[str, str]:
    """("app_label.modelname", URL name) of the page being opened."""

    index = reverse("admin:index")
    path = request.path
    if not path.startswith(index):
        return "", ""
    parts = path[len(index):].split("/")
    label = f"{parts[0]}.{parts[1]}" if len(parts) > 2 and parts[1] else ""
    match = getattr(request, "resolver_match", None)
    return label, (match.url_name or "") if match else ""


def _home(label: str, in_menu: set[str]) -> str:
    """The menu item a page belongs to: itself, else the first item of its
    tabs (a service table lights up the page it is a tab of)."""

    if label in in_menu:
        return label
    for labels in TABS:
        if label in labels:
            return next((candidate for candidate in labels if candidate in in_menu), "")
    return ""


def sidebar(request) -> list[dict]:
    """``UNFOLD["SIDEBAR"]["navigation"]``.

    Each item says itself whether it is the open page: Unfold would light up
    every menu item of a tab group at once."""

    label, url_name = _current(request)
    groups = [{"items": [{
        "title": _lazy("Сегодня"), "icon": "dashboard", "link": reverse("admin:index"),
        "active": url_name == "index",
    }]}]
    chosen = list(GROUPS)
    if request.user.is_superuser:
        chosen.append(PLATFORM_GROUP)
    built = []
    for title, pages in chosen:
        items = [(page, item) for page in pages if (item := _item(request, page)) is not None]
        if items:
            built.append((title, items))

    on_link = next(
        (
            page for _name, items in built for page, _ignored in items
            if isinstance(page, Link) and url_name in page.active_names
        ),
        None,
    )
    in_menu = {page.model for _name, items in built for page, _ignored in items
               if isinstance(page, Page)}
    home = "" if on_link is not None else _home(label, in_menu)
    for title, items in built:
        for page, item in items:
            item["active"] = page is on_link if isinstance(page, Link) else page.model == home
        groups.append({
            "title": title, "items": [item for _page, item in items], "collapsible": False,
        })
    return groups


def _title(label: str) -> object:
    if label in TAB_TITLES:
        return TAB_TITLES[label]
    for _group, pages in (*GROUPS, PLATFORM_GROUP):
        for page in pages:
            if isinstance(page, Page) and page.model == label and page.title:
                return page.title
    return _model_admin(label).model._meta.verbose_name_plural


def tabs(request) -> list[dict]:
    """``UNFOLD["TABS"]``: related pages over each list."""

    result = []
    for labels in TABS:
        shown = [label for label in labels if _shown(request, label, menu=False)]
        if len(shown) < 2:
            continue
        result.append({
            "models": list(shown),
            "items": [
                {"title": _title(label), "link": _url(_model_admin(label), "changelist")}
                for label in shown
            ],
        })
    return result


def show_all_applications(request) -> bool:
    """Every table, by app — for the platform."""

    return request.user.is_superuser


# --- counters next to menu items --------------------------------------------


def new_orders(request):
    """Orders not accepted yet."""

    partner_id = _scope(request)
    if partner_id is None:
        return ""
    order = apps.get_model("orders", "Order")
    count = order.objects.filter(partner_id=partner_id, status=order.Status.NEW).count()
    return count or ""


def open_bill_requests(request):
    """Guests waiting for their bill."""

    partner_id = _scope(request)
    if partner_id is None:
        return ""
    request_model = apps.get_model("billing", "BillingRequest")
    count = request_model.objects.filter(
        partner_id=partner_id, status=request_model.Status.OPEN
    ).count()
    return count or ""
