from django import forms
from django.contrib import admin, messages

from apps.menu.catalog import BUILTIN_BADGES, WEEKDAYS
from apps.menu.models import (
    MenuBadge,
    MenuCategory,
    MenuItem,
    MenuItemAttribute,
    MenuItemPhoto,
)
from apps.orders.kitchen import missing_screen_warnings
from apps.partners.models import PartnerBotSettings
from apps.users.constants import AdminSection
from core.admin_mixins import ScopedAdminMixin, SectionInlineMixin
from core.i18n import gettext_lazy as _lazy


class ChoiceListField(forms.TypedMultipleChoiceField):
    """A JSON list of codes edited as ticks (weekdays, ready labels)."""

    def prepare_value(self, value):
        return value or []


class MenuCategoryForm(forms.ModelForm):
    available_weekdays = ChoiceListField(
        choices=[(index, _lazy(day)) for index, day in enumerate(WEEKDAYS)],
        coerce=int, required=False, widget=forms.CheckboxSelectMultiple,
        label=_lazy("Дни продажи"), help_text=_lazy("Ничего не отмечено — каждый день."),
    )

    class Meta:
        model = MenuCategory
        fields = (
            "partner", "name", "parent", "description", "sort_order", "is_active", "station",
            "available_from", "available_until", "available_weekdays",
        )


@admin.register(MenuCategory)
class MenuCategoryAdmin(ScopedAdminMixin):
    admin_section = AdminSection.MENU
    form = MenuCategoryForm
    list_display = ("name", "parent", "partner", "sort_order", "station", "is_active")
    list_filter = ("partner", "station", "is_active")
    search_fields = ("name", "partner__name")
    fieldsets = (
        (None, {"fields": ("partner", "name", "parent", "description", "sort_order",
                           "is_active", "station")}),
        (_lazy("Расписание"), {
            "description": _lazy(
                "Вне этого времени категория видна гостям с пометкой, но не продаётся. "
                "Подкатегория продаётся, только пока продаётся и её верхняя категория.",
            ),
            "fields": ("available_from", "available_until", "available_weekdays"),
        }),
    )

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        field = super().formfield_for_foreignkey(db_field, request, **kwargs)
        if db_field.name == "parent":
            # Two levels: only a top category can hold others.
            field.queryset = field.queryset.filter(parent__isnull=True)
        return field

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if obj.station:
            # Kitchen and bar screens (PQ-048): a place nobody shows.
            for warning in missing_screen_warnings(obj.partner_id):
                messages.warning(request, warning)


@admin.register(MenuBadge)
class MenuBadgeAdmin(ScopedAdminMixin):
    """The owner's own labels for items; the ready ones are always there."""

    admin_section = AdminSection.MENU
    list_display = ("name", "emoji", "partner", "sort_order")
    list_filter = ("partner",)
    search_fields = ("name",)


class MenuItemForm(forms.ModelForm):
    badges = ChoiceListField(
        choices=[
            (code, f"{emoji} {_lazy(name)}") for code, (emoji, name) in BUILTIN_BADGES.items()
        ],
        required=False, widget=forms.CheckboxSelectMultiple, label=_lazy("Метки"),
    )

    class Meta:
        model = MenuItem
        fields = (
            "partner", "category", "name", "description", "price", "image", "image_url",
            "item_type", "is_available", "sort_order", "available_at_table",
            "available_for_pickup", "available_for_delivery", "available_for_shipping",
            "portion", "portion_unit", "old_price", "badges", "custom_badges", "sku", "barcode",
        )


class MenuItemPhotoInline(SectionInlineMixin, admin.TabularInline):
    admin_section = AdminSection.MENU
    model = MenuItemPhoto
    fields = ("image", "sort_order")
    extra = 0


class MenuItemAttributeInline(SectionInlineMixin, admin.TabularInline):
    admin_section = AdminSection.MENU
    model = MenuItemAttribute
    fields = ("name", "value", "sort_order")
    extra = 0


SHOWCASE_FIELDS = (
    "portion", "portion_unit", "old_price", "badges", "custom_badges", "sku", "barcode",
)


@admin.register(MenuItem)
class MenuItemAdmin(ScopedAdminMixin):
    admin_section = AdminSection.MENU
    form = MenuItemForm
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
    search_fields = ("public_id", "name", "sku", "barcode", "category__name", "partner__name")
    filter_horizontal = ()
    fieldsets = (
        (None, {"fields": ("partner", "category", "name", "description", "price", "image",
                           "image_url", "item_type", "is_available", "sort_order")}),
        (_lazy("Где продаётся"), {"fields": (
            "available_at_table", "available_for_pickup", "available_for_delivery",
            "available_for_shipping",
        )}),
        (_lazy("Витрина"), {
            "description": _lazy(
                "Порция, старая цена, метки, артикул; ниже — ещё фото и характеристики."
            ),
            "fields": SHOWCASE_FIELDS,
        }),
    )

    def showcase_on_for(self, request) -> bool:
        """The showcase fields while the venue's module is on (platform
        admins see them always)."""

        partner_id = self.get_partner_scope_id(request)
        if partner_id is None:
            return self.is_platform_admin(request)
        return PartnerBotSettings.objects.filter(
            partner_id=partner_id, module_showcase_enabled=True
        ).exists()

    def get_exclude(self, request, obj=None):
        exclude = list(super().get_exclude(request, obj) or ())
        if not self.showcase_on_for(request):
            exclude += [name for name in SHOWCASE_FIELDS if name not in exclude]
        return tuple(exclude) or None

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        if self.is_platform_admin(request) and not self.showcase_on_for(request):
            return [(name, options) for name, options in fieldsets
                    if tuple(options.get("fields", ())) != SHOWCASE_FIELDS]
        return [(name, options) for name, options in fieldsets if options.get("fields")]

    def get_inlines(self, request, obj):
        if self.showcase_on_for(request):
            return [MenuItemPhotoInline, MenuItemAttributeInline]
        return []
