from __future__ import annotations

from django.urls import reverse

from apps.users.constants import AdminSection


def _can_view(section: str):
    def permission(request):
        user = request.user
        if not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
            return False
        if getattr(user, "is_superuser", False):
            return True
        try:
            profile = user.admin_access_profile
        except Exception:
            return False
        if not (profile.can_access_admin and profile.is_active):
            return False
        return profile.has_section_permission(section, "view")

    return permission


def _admin_link(name: str):
    return lambda _request: reverse(name)


def get_admin_sidebar_navigation(_request):
    return [
        {
            "title": "Платформа",
            "collapsible": True,
            "items": [
                {
                    "title": "Партнёры",
                    "icon": "storefront",
                    "link": _admin_link("admin:partners_partner_changelist"),
                    "permission": _can_view(AdminSection.PARTNER_PROFILE),
                },
                {
                    "title": "Боты",
                    "icon": "smart_toy",
                    "link": _admin_link("admin:partners_botinstance_changelist"),
                    "permission": _can_view(AdminSection.BOT_INSTANCES),
                },
                {
                    "title": "Контент бота",
                    "icon": "chat_bubble",
                    "link": _admin_link("admin:partners_partnerbotsettings_changelist"),
                    "permission": _can_view(AdminSection.BOT_CONTENT),
                },
                {
                    "title": "Доступ в админку",
                    "icon": "admin_panel_settings",
                    "link": _admin_link("admin:users_adminaccessprofile_changelist"),
                    "permission": _can_view(AdminSection.ADMIN_ACCESS),
                },
            ],
        },
        {
            "title": "Зал и каталог",
            "collapsible": True,
            "separator": True,
            "items": [
                {
                    "title": "Столы",
                    "icon": "table_restaurant",
                    "link": _admin_link("admin:tables_table_changelist"),
                    "permission": _can_view(AdminSection.TABLES),
                },
                {
                    "title": "Меню",
                    "icon": "menu_book",
                    "link": _admin_link("admin:menu_menuitem_changelist"),
                    "permission": _can_view(AdminSection.MENU),
                },
                {
                    "title": "Команда",
                    "icon": "badge",
                    "link": _admin_link("admin:employees_employeeprofile_changelist"),
                    "permission": _can_view(AdminSection.EMPLOYEES),
                },
            ],
        },
        {
            "title": "Операции",
            "collapsible": True,
            "separator": True,
            "items": [
                {
                    "title": "Заказы",
                    "icon": "receipt_long",
                    "link": _admin_link("admin:orders_order_changelist"),
                    "permission": _can_view(AdminSection.ORDERS),
                },
                {
                    "title": "Счета и оплаты",
                    "icon": "payments",
                    "link": _admin_link("admin:billing_bill_changelist"),
                    "permission": _can_view(AdminSection.BILLING),
                },
                {
                    "title": "Лояльность",
                    "icon": "workspace_premium",
                    "link": _admin_link("admin:users_guestprofile_changelist"),
                    "permission": _can_view(AdminSection.BONUSES),
                },
                {
                    "title": "Рассылки",
                    "icon": "campaign",
                    "link": _admin_link("admin:notifications_broadcastcampaign_changelist"),
                    "permission": _can_view(AdminSection.NOTIFICATIONS),
                },
            ],
        },
        {
            "title": "Аналитика",
            "collapsible": True,
            "separator": True,
            "items": [
                {
                    "title": "Дневные метрики",
                    "icon": "query_stats",
                    "link": _admin_link("admin:analytics_partnerdailymetric_changelist"),
                    "permission": _can_view(AdminSection.ANALYTICS),
                },
            ],
        },
    ]
