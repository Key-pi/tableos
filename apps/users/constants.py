from django.db import models

from core.i18n import gettext_lazy as _lazy


class UserRole(models.TextChoices):
    OWNER = "owner", _lazy("Владелец")
    MANAGER = "manager", _lazy("Менеджер")
    CASHIER = "cashier", _lazy("Кассир")
    WAITER = "waiter", _lazy("Официант")
    HOOKAH_MASTER = "hookah_master", _lazy("Кальянщик")
    ADMIN = "admin", _lazy("Администратор платформы")


class AdminSection(models.TextChoices):
    PARTNER_PROFILE = "partner_profile", _lazy("Профиль заведения")
    BOT_INSTANCES = "bot_instances", _lazy("Боты")
    BOT_CONTENT = "bot_content", _lazy("Настройки и тексты бота")
    TABLES = "tables", _lazy("Столы")
    MENU = "menu", _lazy("Меню")
    ORDERS = "orders", _lazy("Заказы")
    BONUSES = "bonuses", _lazy("Бонусы и гости")
    EMPLOYEES = "employees", _lazy("Сотрудники")
    ADMIN_ACCESS = "admin_access", _lazy("Доступ к Admin")
    NOTIFICATIONS = "notifications", _lazy("Уведомления и рассылки")
    ANALYTICS = "analytics", _lazy("Аналитика")
    BILLING = "billing", _lazy("Счета и оплаты")


class AdminAccessPreset(models.TextChoices):
    CUSTOM = "custom", _lazy("Свой набор")
    OWNER = "owner", _lazy("Владелец")
    MANAGER = "manager", _lazy("Менеджер")
    CASHIER = "cashier", _lazy("Кассир")
    CONTENT_MANAGER = "content_manager", _lazy("Контент-менеджер")


FULL_CRUD = {
    "can_view": True,
    "can_add": True,
    "can_change": True,
    "can_delete": True,
    "can_manage_sensitive": True,
}

VIEW_ONLY = {
    "can_view": True,
    "can_add": False,
    "can_change": False,
    "can_delete": False,
    "can_manage_sensitive": False,
}


ADMIN_ACCESS_PRESET_TEMPLATES: dict[str, dict[str, dict[str, bool]]] = {
    AdminAccessPreset.OWNER: {
        section: FULL_CRUD
        for section in AdminSection.values
    },
    AdminAccessPreset.MANAGER: {
        AdminSection.PARTNER_PROFILE: VIEW_ONLY,
        AdminSection.BOT_CONTENT: {
            "can_view": True,
            "can_add": False,
            "can_change": True,
            "can_delete": False,
            "can_manage_sensitive": False,
        },
        AdminSection.TABLES: {
            "can_view": True,
            "can_add": True,
            "can_change": True,
            "can_delete": False,
            "can_manage_sensitive": False,
        },
        AdminSection.MENU: {
            "can_view": True,
            "can_add": True,
            "can_change": True,
            "can_delete": True,
            "can_manage_sensitive": False,
        },
        AdminSection.ORDERS: {
            "can_view": True,
            "can_add": False,
            "can_change": True,
            "can_delete": False,
            "can_manage_sensitive": False,
        },
        AdminSection.BONUSES: {
            "can_view": True,
            "can_add": True,
            "can_change": True,
            "can_delete": False,
            "can_manage_sensitive": False,
        },
        AdminSection.EMPLOYEES: {
            "can_view": True,
            "can_add": True,
            "can_change": True,
            "can_delete": False,
            "can_manage_sensitive": False,
        },
        AdminSection.NOTIFICATIONS: {
            "can_view": True,
            "can_add": True,
            "can_change": True,
            "can_delete": False,
            "can_manage_sensitive": False,
        },
        AdminSection.ANALYTICS: VIEW_ONLY,
        AdminSection.BILLING: {
            "can_view": True,
            "can_add": True,
            "can_change": True,
            "can_delete": False,
            "can_manage_sensitive": False,
        },
    },
    AdminAccessPreset.CASHIER: {
        AdminSection.ORDERS: {
            "can_view": True,
            "can_add": False,
            "can_change": True,
            "can_delete": False,
            "can_manage_sensitive": False,
        },
        AdminSection.BONUSES: {
            "can_view": True,
            "can_add": True,
            "can_change": True,
            "can_delete": False,
            "can_manage_sensitive": False,
        },
        AdminSection.BILLING: {
            "can_view": True,
            "can_add": True,
            "can_change": True,
            "can_delete": False,
            "can_manage_sensitive": False,
        },
    },
    AdminAccessPreset.CONTENT_MANAGER: {
        AdminSection.BOT_CONTENT: {
            "can_view": True,
            "can_add": False,
            "can_change": True,
            "can_delete": False,
            "can_manage_sensitive": False,
        },
        AdminSection.MENU: {
            "can_view": True,
            "can_add": True,
            "can_change": True,
            "can_delete": True,
            "can_manage_sensitive": False,
        },
        AdminSection.NOTIFICATIONS: {
            "can_view": True,
            "can_add": True,
            "can_change": True,
            "can_delete": False,
            "can_manage_sensitive": False,
        },
    },
}
