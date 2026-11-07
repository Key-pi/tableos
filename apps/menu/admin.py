from django.contrib import admin, messages

from apps.menu.models import MenuCategory, MenuItem
from apps.orders.kitchen import missing_screen_warnings
from apps.users.constants import AdminSection
from core.admin_mixins import ScopedAdminMixin


@admin.register(MenuCategory)
class MenuCategoryAdmin(ScopedAdminMixin):
    admin_section = AdminSection.MENU
    list_display = ("name", "partner", "sort_order", "station", "is_active")
    list_filter = ("partner", "station", "is_active")
    search_fields = ("name", "partner__name")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if obj.station:
            # Kitchen and bar screens (PQ-048): a place nobody shows.
            for warning in missing_screen_warnings(obj.partner_id):
                messages.warning(request, warning)


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
        "available_at_table",
        "available_for_pickup",
    )
    list_filter = (
        "partner",
        "item_type",
        "is_available",
        "available_at_table",
        "available_for_pickup",
    )
    search_fields = ("public_id", "name", "category__name", "partner__name")
