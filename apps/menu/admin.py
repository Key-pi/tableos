from django.contrib import admin

from apps.menu.models import MenuCategory, MenuItem
from apps.users.constants import AdminSection
from core.admin_mixins import ScopedAdminMixin


@admin.register(MenuCategory)
class MenuCategoryAdmin(ScopedAdminMixin):
    admin_section = AdminSection.MENU
    list_display = ("name", "partner", "sort_order", "is_active")
    list_filter = ("partner", "is_active")
    search_fields = ("name", "partner__name")


@admin.register(MenuItem)
class MenuItemAdmin(ScopedAdminMixin):
    admin_section = AdminSection.MENU
    list_display = (
        "public_id",
        "name",
        "partner",
        "category",
        "price",
        "item_type",
        "is_available",
    )
    list_filter = ("partner", "item_type", "is_available")
    search_fields = ("public_id", "name", "category__name", "partner__name")
