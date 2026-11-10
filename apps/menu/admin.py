from django import forms
from django.contrib import admin, messages
from django.forms.models import BaseInlineFormSet
from unfold.admin import TabularInline
from unfold.widgets import (
    UnfoldAdminCheckboxSelectMultipleWidget,
    UnfoldAdminTextareaWidget,
    UnfoldAdminTextInputWidget,
)

from apps.menu import stock as stock_levels
from apps.menu.catalog import BUILTIN_BADGES, WEEKDAYS
from apps.menu.models import (
    MenuBadge,
    MenuCategory,
    MenuItem,
    MenuItemAttribute,
    MenuItemPhoto,
    MenuItemVariant,
    Modifier,
    ModifierGroup,
    StockMovement,
)
from apps.menu.options import options_on
from apps.menu.stock import count_stock
from apps.menu.translations import fields_of, set_translation, translation_languages
from apps.orders.kitchen import missing_screen_warnings
from apps.users.constants import AdminSection
from core.admin_mixins import ObjectAction, ObjectActionsMixin, ScopedAdminMixin, SectionInlineMixin
from core.i18n import LANGUAGE_NAMES, _
from core.i18n import gettext_lazy as _lazy


class ChoiceListField(forms.TypedMultipleChoiceField):
    """A JSON list of codes edited as ticks (weekdays, ready labels)."""

    def prepare_value(self, value):
        return value or []


def venue_form(form_class, partner_id):
    """The form knows the venue before it checks. The venue field is hidden
    from venue staff and was put in only on save, so «the parent is of this
    venue» refused every subcategory and «unique in the venue» (SKU, barcode)
    was skipped until the database refused it (owner, 2026-11-07)."""

    if partner_id is None:
        return form_class

    class VenueForm(form_class):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            if self.instance.partner_id is None:
                self.instance.partner_id = partner_id

        def _get_validation_exclusions(self):
            exclusions = super()._get_validation_exclusions()
            exclusions.discard("partner")
            return exclusions

    return VenueForm


def translation_field(field: str, language: str) -> str:
    return f"{field}_{language}"


class TranslationsFormMixin:
    """Form fields ``<field>_<language>`` kept in the instance's
    ``translations``; languages not on the page stay as they are."""

    translation_languages: tuple = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        stored = getattr(self.instance, "translations", None) or {}
        for language in self.translation_languages:
            for field in fields_of(self._meta.model):
                self.initial.setdefault(
                    translation_field(field, language),
                    (stored.get(language) or {}).get(field, ""),
                )

    def _post_clean(self):
        for language in self.translation_languages:
            for field in fields_of(self._meta.model):
                name = translation_field(field, language)
                if name in self.cleaned_data:
                    set_translation(self.instance, language, field, self.cleaned_data[name] or "")
        super()._post_clean()


def translated_form(form_class, model, languages):
    """``form_class`` with a field per text and language (catalog step 4)."""

    if not languages:
        return form_class
    attrs = {"translation_languages": tuple(languages)}
    for language in languages:
        for field in fields_of(model):
            model_field = model._meta.get_field(field)
            long_text = model_field.max_length is None
            attrs[translation_field(field, language)] = forms.CharField(
                label=f"{model_field.verbose_name} · {LANGUAGE_NAMES[language]}",
                required=False,
                max_length=model_field.max_length,
                widget=(
                    UnfoldAdminTextareaWidget(attrs={"rows": 2}) if long_text
                    else UnfoldAdminTextInputWidget
                ),
            )
    return type(form_class.__name__, (TranslationsFormMixin, form_class), attrs)


TRANSLATIONS_HELP = _lazy(
    "Гость видит перевод на своём языке; пусто — текст на основном языке. Языки — "
    "включённые для гостей в настройках бота, кроме основного. Персонал, экраны кухни и "
    "счета — на основном языке."
)


class TranslatedAdminMixin:
    """A page with the translations of its texts (catalog step 4)."""

    def translation_names(self, request, obj=None) -> list[str]:
        partner_id = obj.partner_id if obj is not None else self.get_partner_scope_id(request)
        return [
            translation_field(field, language)
            for language in translation_languages(partner_id)
            for field in fields_of(self.model)
        ]

    def get_form(self, request, obj=None, change=False, **kwargs):
        partner_id = obj.partner_id if obj is not None else self.get_partner_scope_id(request)
        kwargs.setdefault(
            "form", translated_form(self.form, self.model, translation_languages(partner_id))
        )
        return super().get_form(request, obj, change=change, **kwargs)

    def get_fieldsets(self, request, obj=None):
        fieldsets = list(super().get_fieldsets(request, obj))
        names = self.translation_names(request, obj)
        if names and self.fieldsets:
            tabbed = any("tab" in options.get("classes", ()) for _name, options in fieldsets)
            fieldsets.append((_lazy("Переводы для гостей"), {
                "description": TRANSLATIONS_HELP, "fields": names,
                **({"classes": ["tab"]} if tabbed else {}),
            }))
        return fieldsets

    def get_fields(self, request, obj=None):
        fields = list(super().get_fields(request, obj))
        if not self.fieldsets:
            fields += [name for name in self.translation_names(request, obj) if name not in fields]
        return fields


class TranslatedInlineMixin:
    """Rows with their translations: a column per text and language."""

    def _partner_id(self, request, obj):
        if obj is not None:
            return obj.partner_id
        parent = self.admin_site._registry.get(self.parent_model)
        return parent.get_partner_scope_id(request) if parent is not None else None

    def get_formset(self, request, obj=None, **kwargs):
        languages = translation_languages(self._partner_id(request, obj))
        kwargs.setdefault("form", translated_form(self.form, self.model, languages))
        return super().get_formset(request, obj, **kwargs)

    def get_fields(self, request, obj=None):
        fields = list(super().get_fields(request, obj))
        return fields + [
            translation_field(field, language)
            for language in translation_languages(self._partner_id(request, obj))
            for field in fields_of(self.model)
        ]


class VenueFormMixin:
    def get_form(self, request, obj=None, change=False, **kwargs):
        form_class = super().get_form(request, obj, change=change, **kwargs)
        return venue_form(form_class, self.get_partner_scope_id(request))


class MenuCategoryForm(forms.ModelForm):
    available_weekdays = ChoiceListField(
        choices=[(index, _lazy(day)) for index, day in enumerate(WEEKDAYS)],
        coerce=int, required=False, widget=UnfoldAdminCheckboxSelectMultipleWidget,
        label=_lazy("Дни продажи"), help_text=_lazy("Ничего не отмечено — каждый день."),
    )

    class Meta:
        model = MenuCategory
        fields = (
            "partner", "name", "parent", "description", "sort_order", "is_active", "station",
            "available_from", "available_until", "available_weekdays",
        )


@admin.register(MenuCategory)
class MenuCategoryAdmin(TranslatedAdminMixin, VenueFormMixin, ScopedAdminMixin):
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
            if not self.is_platform_admin(request):
                field.label_from_instance = lambda category: category.name
        return field

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if obj.station:
            # Kitchen and bar screens (PQ-048): a place nobody shows.
            for warning in missing_screen_warnings(obj.partner_id):
                messages.warning(request, warning)


@admin.register(MenuBadge)
class MenuBadgeAdmin(TranslatedAdminMixin, ScopedAdminMixin):
    """The owner's own labels for items; the ready ones are always there."""

    admin_section = AdminSection.MENU
    fields = ("partner", "name", "emoji", "sort_order")
    list_display = ("name", "emoji", "partner", "sort_order")
    list_filter = ("partner",)
    search_fields = ("name",)


class StockSeenMixin(forms.Form):
    """The level the page showed, so saving tells a count (a new number)
    from a level an order changed while the page was open (catalog step 3)."""

    stock_seen = forms.IntegerField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["stock_seen"].initial = self.instance.stock_quantity if self.instance.pk else 0

    def counted_level(self) -> int | None:
        """The new level when the owner typed one, else None."""

        if "stock_quantity" not in self.cleaned_data:
            return None
        level = self.cleaned_data["stock_quantity"] or 0
        seen = self.cleaned_data.get("stock_seen")
        if seen is None:
            seen = 0 if self.instance._state.adding else None
        if seen is None:
            return level if "stock_quantity" in self.changed_data else None
        return level if level != seen else None


class MenuItemForm(StockSeenMixin, forms.ModelForm):
    badges = ChoiceListField(
        choices=[
            (code, f"{emoji} {_lazy(name)}") for code, (emoji, name) in BUILTIN_BADGES.items()
        ],
        required=False, widget=UnfoldAdminCheckboxSelectMultipleWidget, label=_lazy("Метки"),
    )

    class Meta:
        model = MenuItem
        fields = (
            "partner", "category", "name", "description", "price", "image", "image_url",
            "item_type", "is_available", "sort_order", "available_at_table",
            "available_for_pickup", "available_for_delivery", "available_for_shipping",
            "portion", "portion_unit", "old_price", "badges", "custom_badges", "sku", "barcode",
            "track_stock", "stock_quantity",
        )


def _without_stock(row) -> list[str]:
    """Every field but the level: an Admin page never writes back a level an
    order changed while it was open (catalog step 3)."""

    return [
        field.name for field in row._meta.concrete_fields
        if not field.primary_key and field.name != "stock_quantity"
    ]


class StockVariantFormSet(BaseInlineFormSet):
    """Variants with their level: a new number is a count (``count_stock``,
    after the page is saved), the rest saves without the level."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.counts: list = []
        self.keep_level: set = set()

    def save_existing(self, form, obj, commit=True):
        if not self.instance.track_stock:
            return super().save_existing(form, obj, commit=commit)
        level = form.counted_level()
        obj = form.save(commit=False)
        if level is not None:
            self.counts.append((obj, level))
        self.keep_level.add(obj.pk)
        if commit:
            self.save_instance(obj)
        return obj

    def save_instance(self, obj) -> None:
        """How ``ScopedAdminMixin.save_formset`` saves a row."""

        if obj.pk in self.keep_level:
            obj.save(update_fields=_without_stock(obj))
        else:
            obj.save()

    def save_new(self, form, commit=True):
        level = form.counted_level()
        if not self.instance.track_stock or not level:
            return super().save_new(form, commit=commit)
        form.instance.stock_quantity = 0
        obj = super().save_new(form, commit=commit)
        self.counts.append((obj, level))
        return obj


class StockVariantForm(StockSeenMixin, forms.ModelForm):
    class Meta:
        model = MenuItemVariant
        fields = ("name", "price", "is_available", "sort_order", "stock_quantity")


class MenuItemPhotoInline(SectionInlineMixin, TabularInline):
    admin_section = AdminSection.MENU
    tab = True  # a page tab next to the item (admin step 4, PQ-050)
    model = MenuItemPhoto
    fields = ("image", "sort_order")
    extra = 0


class MenuItemAttributeInline(TranslatedInlineMixin, SectionInlineMixin, TabularInline):
    admin_section = AdminSection.MENU
    tab = True  # a page tab next to the item (admin step 4, PQ-050)
    model = MenuItemAttribute
    fields = ("name", "value", "sort_order")
    extra = 0


class MenuItemVariantInline(TranslatedInlineMixin, SectionInlineMixin, TabularInline):
    """Variants with their own price (module «Варианты и добавки»)."""

    admin_section = AdminSection.MENU
    model = MenuItemVariant
    fields = ("name", "price", "is_available", "sort_order")
    extra = 0


class MenuItemVariantStockInline(MenuItemVariantInline):
    """The same with each variant's level (module «Остатки»)."""

    fields = ("name", "price", "is_available", "sort_order", "stock_quantity", "stock_seen")
    form = StockVariantForm
    formset = StockVariantFormSet


SHOWCASE_FIELDS = (
    "portion", "portion_unit", "old_price", "badges", "custom_badges", "sku", "barcode",
)
STOCK_FIELDS = ("track_stock", "stock_quantity", "stock_seen")


def module_on_for(admin_page, request, field: str) -> bool:
    """A module's part of a page while the venue's module is on (platform
    admins see it always)."""

    return admin_page.module_on(request, field)


class StockStateFilter(admin.SimpleListFilter):
    """«Остаток» on the item list while stock is on; the start page links
    here (admin step 2, PQ-050)."""

    title = _lazy("Остаток")
    parameter_name = "stock"

    def __init__(self, request, params, model, model_admin):
        self.model_admin = model_admin
        super().__init__(request, params, model, model_admin)

    def lookups(self, request, model_admin):
        return (
            (stock_levels.OUT, _lazy("Нет в наличии")),
            (stock_levels.LOW, _lazy("Осталось мало")),
            (stock_levels.BELOW_ZERO, _lazy("Ниже нуля")),
        )

    def queryset(self, request, queryset):
        if self.value() is None:
            return queryset
        partner_id = self.model_admin.get_partner_scope_id(request)
        variants = partner_id is None or options_on(partner_id)
        return stock_levels.items_in_stock_state(queryset, self.value(), variants=variants)


AVAILABILITY_TOGGLE = "availability"
# «Где продаётся»: a way to order the venue has not switched on is not asked
# about (admin step 4, PQ-050); its mark keeps its value.
CHANNEL_MODULES = {
    "available_at_table": "module_tables_enabled",
    "available_for_pickup": "module_pickup_enabled",
    "available_for_delivery": "module_delivery_enabled",
    "available_for_shipping": "module_shipping_enabled",
}


@admin.register(MenuItem)
class MenuItemAdmin(ObjectActionsMixin, TranslatedAdminMixin, VenueFormMixin, ScopedAdminMixin):
    admin_section = AdminSection.MENU
    form = MenuItemForm
    list_before_template = "admin/botera/row_actions.html"
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
        (None, {"fields": ("partner", "category", "name", "description",
                           ("price", "item_type"), "image", "image_url",
                           ("is_available", "sort_order"))}),
        (_lazy("Где продаётся"), {"classes": ["tab"], "fields": (
            "available_at_table", "available_for_pickup", "available_for_delivery",
            "available_for_shipping",
        )}),
        (_lazy("Витрина"), {
            "classes": ["tab"],
            "description": _lazy(
                "Порция, старая цена, метки, артикул; ниже — ещё фото и характеристики."
            ),
            "fields": SHOWCASE_FIELDS,
        }),
        (_lazy("Остатки"), {
            "classes": ["tab"],
            "description": _lazy(
                "Остаток уменьшается, когда гость оформляет заказ и при быстрой продаже; "
                "отмена заказа возвращает товар. Новое число здесь — пересчёт, он попадает "
                "в «Движение остатков». У позиции с вариантами остаток — у каждого варианта."
            ),
            "fields": STOCK_FIELDS,
        }),
    )

    def showcase_on_for(self, request) -> bool:
        return module_on_for(self, request, "module_showcase_enabled")

    # The stop list in one press (admin step 3, PQ-050): the item leaves the
    # guests' menu («В наличии» off) and comes back the same way.
    @staticmethod
    def _availability_action(obj) -> ObjectAction:
        if obj.is_available:
            return ObjectAction(AVAILABILITY_TOGGLE, _("В стоп-лист"), "block")
        return ObjectAction(AVAILABILITY_TOGGLE, _("Вернуть в меню"), "undo", style="primary")

    def get_object_actions(self, request, obj):
        return [self._availability_action(obj)]

    def run_object_action(self, request, obj, code, data):
        obj.is_available = not obj.is_available
        obj.save(update_fields=["is_available", "updated_at"])
        if obj.is_available:
            return _("«{name}» снова в меню гостей.", name=obj.name)
        return _("«{name}» убрана из меню гостей.", name=obj.name)

    def _channels_off(self, request) -> tuple[str, ...]:
        return tuple(
            field for field, module in CHANNEL_MODULES.items()
            if not module_on_for(self, request, module)
        )

    def get_list_display(self, request):
        off = self._channels_off(request)
        list_display = tuple(
            column for column in super().get_list_display(request) if column not in off
        )
        if self.has_change_permission(request):
            # Next to the name: on a tablet the end of a wide row is off screen.
            at = list_display.index("name") + 1 if "name" in list_display else len(list_display)
            return (*list_display[:at], "availability_button", *list_display[at:])
        return list_display

    @admin.display(description=_lazy("Стоп-лист"))
    def availability_button(self, obj):
        return self.object_action_button(obj, self._availability_action(obj))

    def stock_on_for(self, request) -> bool:
        return module_on_for(self, request, "module_stock_enabled")

    def get_list_filter(self, request):
        off = self._channels_off(request)
        list_filter = tuple(
            entry for entry in super().get_list_filter(request) if entry not in off
        )
        if not self.stock_on_for(request):
            return list_filter
        return (*list_filter, StockStateFilter)

    def _parts_off(self, request) -> list[tuple]:
        """The modules' parts of the page that are off for this venue."""

        off = []
        if not self.showcase_on_for(request):
            off.append(SHOWCASE_FIELDS)
        if not self.stock_on_for(request):
            off.append(STOCK_FIELDS)
        return off

    def get_exclude(self, request, obj=None):
        exclude = list(super().get_exclude(request, obj) or ())
        for fields in (*self._parts_off(request), self._channels_off(request)):
            exclude += [name for name in fields if name not in exclude]
        return tuple(exclude) or None

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        if self.is_platform_admin(request):
            off = self._parts_off(request)
            return [(name, options) for name, options in fieldsets
                    if tuple(options.get("fields", ())) not in off]
        return [(name, options) for name, options in fieldsets if options.get("fields")]

    def get_inlines(self, request, obj):
        inlines = []
        if module_on_for(self, request, "module_options_enabled"):
            inlines.append(
                MenuItemVariantStockInline if self.stock_on_for(request) else MenuItemVariantInline
            )
        if self.showcase_on_for(request):
            inlines += [MenuItemPhotoInline, MenuItemAttributeInline]
        return inlines

    def save_model(self, request, obj, form, change):
        # The level is changed by a count only (catalog step 3): the rest of
        # the page saves without it, a new number is counted after.
        level = form.counted_level() if obj.track_stock else None
        counted = level is not None
        if counted or (change and obj.track_stock):
            if change:
                obj.save(update_fields=_without_stock(obj))
            else:
                obj.stock_quantity = 0
                super().save_model(request, obj, form, change)
        else:
            super().save_model(request, obj, form, change)
        if counted:
            count_stock(item=obj, variant=None, quantity=level, actor=request.user)

    def save_related(self, request, form, formsets, change):
        if form.instance.track_stock:
            # Items, then variants in id order, as orders lock them.
            list(
                MenuItemVariant.objects.select_for_update(no_key=True)
                .filter(item=form.instance).order_by("id").values_list("id", flat=True)
            )
        super().save_related(request, form, formsets, change)
        for formset in formsets:
            for variant, level in getattr(formset, "counts", ()):
                count_stock(item=form.instance, variant=variant, quantity=level,
                            actor=request.user)


@admin.register(StockMovement)
class StockMovementAdmin(ScopedAdminMixin):
    """«Движение остатков»: what took or gave back stock, read only (catalog
    step 3). A count is made on the item's page."""

    admin_section = AdminSection.MENU
    allow_partner_add = False
    allow_partner_delete = False
    list_display = (
        "created_at", "item", "variant", "delta", "balance_after", "reason", "source_label",
        "actor", "partner",
    )
    list_filter = ("partner", "reason")
    search_fields = ("item__name", "variant__name", "source_id")
    readonly_fields = (
        "item", "variant", "delta", "balance_after", "reason", "source_type", "source_id",
        "actor", "created_at",
    )
    fields = readonly_fields

    @admin.display(description=_lazy("Источник"))
    def source_label(self, obj):
        return f"{obj.source_type} {obj.source_id}".strip()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ModifierInline(TranslatedInlineMixin, SectionInlineMixin, TabularInline):
    admin_section = AdminSection.MENU
    model = Modifier
    fields = ("name", "price", "is_available", "sort_order")
    extra = 1


@admin.register(ModifierGroup)
class ModifierGroupAdmin(TranslatedAdminMixin, VenueFormMixin, ScopedAdminMixin):
    """Add-ons set once and attached to items or whole categories (module
    «Варианты и добавки»; guests see them while it is on)."""

    admin_section = AdminSection.MENU
    list_display = ("name", "partner", "min_choices", "max_choices", "is_active", "sort_order")
    list_filter = ("partner", "is_active")
    search_fields = ("name",)
    filter_horizontal = ("categories", "items")
    inlines = [ModifierInline]
    fieldsets = (
        (None, {
            "description": _lazy(
                "Гость выбирает добавки, пока включён модуль «Варианты и добавки». "
                "Группа без добавок в наличии не показывается."
            ),
            "fields": ("partner", "name", "min_choices", "max_choices", "is_active",
                       "sort_order"),
        }),
        (_lazy("К чему предлагать"), {
            "description": _lazy(
                "Категория — для всех её позиций и подкатегорий; позиции — по одной."
            ),
            "fields": ("categories", "items"),
        }),
    )

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        field = super().formfield_for_manytomany(db_field, request, **kwargs)
        if not self.is_platform_admin(request):
            # One venue: its name before every row says nothing.
            if db_field.name == "categories":
                field.label_from_instance = lambda category: category.full_name
            elif db_field.name == "items":
                field.label_from_instance = (
                    lambda item: f"{item.name} · {item.category.full_name}"
                )
                field.queryset = field.queryset.select_related("category__parent")
        return field
