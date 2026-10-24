from django.db import models


class UserRole(models.TextChoices):
    OWNER = "owner", "Владелец"
    MANAGER = "manager", "Менеджер"
    CASHIER = "cashier", "Кассир"
    WAITER = "waiter", "Официант"
    HOOKAH_MASTER = "hookah_master", "Кальянщик"
    ADMIN = "admin", "Администратор платформы"


class AdminSection(models.TextChoices):
    PARTNER_PROFILE = "partner_profile", "Профиль заведения"
    BOT_INSTANCES = "bot_instances", "Боты"
    BOT_CONTENT = "bot_content", "Настройки и тексты бота"
    TABLES = "tables", "Столы"
    MENU = "menu", "Меню"
    ORDERS = "orders", "Заказы"
    BONUSES = "bonuses", "Бонусы и гости"
    EMPLOYEES = "employees", "Сотрудники"
    ADMIN_ACCESS = "admin_access", "Доступ к Admin"
    NOTIFICATIONS = "notifications", "Уведомления и рассылки"
    ANALYTICS = "analytics", "Аналитика"
    BILLING = "billing", "Счета и оплаты"


class AdminAccessPreset(models.TextChoices):
    CUSTOM = "custom", "Свой набор"
    OWNER = "owner", "Владелец"
    MANAGER = "manager", "Менеджер"
    CASHIER = "cashier", "Кассир"
    CONTENT_MANAGER = "content_manager", "Контент-менеджер"


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
