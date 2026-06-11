from django.db import models


class UserRole(models.TextChoices):
    OWNER = "owner", "Owner"
    MANAGER = "manager", "Manager"
    CASHIER = "cashier", "Cashier"
    WAITER = "waiter", "Waiter"
    HOOKAH_MASTER = "hookah_master", "Hookah master"
    ADMIN = "admin", "Admin"


class AdminSection(models.TextChoices):
    PARTNER_PROFILE = "partner_profile", "Partner profile"
    BOT_INSTANCES = "bot_instances", "Bot instances"
    BOT_CONTENT = "bot_content", "Bot content"
    TABLES = "tables", "Tables"
    MENU = "menu", "Menu"
    ORDERS = "orders", "Orders"
    BONUSES = "bonuses", "Bonuses"
    EMPLOYEES = "employees", "Employees"
    ADMIN_ACCESS = "admin_access", "Admin access"
    NOTIFICATIONS = "notifications", "Notifications"
    ANALYTICS = "analytics", "Analytics"
    BILLING = "billing", "Billing"


class AdminAccessPreset(models.TextChoices):
    CUSTOM = "custom", "Custom"
    OWNER = "owner", "Owner"
    MANAGER = "manager", "Manager"
    CASHIER = "cashier", "Cashier"
    CONTENT_MANAGER = "content_manager", "Content manager"


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
