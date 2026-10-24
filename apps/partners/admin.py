from urllib.parse import urlencode

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.template.response import TemplateResponse
from django.urls import path, reverse

from apps.notifications.services import notify_owners_about_transfer_details_change
from apps.partners.bot_texts import GROUP_LABELS, editable_texts
from apps.partners.forms import BotInstanceAdminForm, BotTextForm, _short
from apps.partners.languages import enabled_languages, text_overrides
from apps.partners.models import (
    BotInstance,
    BotText,
    OpeningHours,
    Partner,
    PartnerBotSettings,
    text_key,
)
from apps.users.constants import AdminSection
from core.admin_mixins import ScopedAdminMixin
from core.i18n import LANGUAGE_NAMES, _, texts


class OpeningHoursInline(admin.TabularInline):
    """Venue hours in the venue timezone; closing before opening = past midnight.

    No rows means always open. Pickup orders are accepted only within hours.
    """

    model = OpeningHours
    extra = 0
    fields = ("scope", "weekday", "opens_at", "closes_at")
    verbose_name = "Интервал работы"
    verbose_name_plural = (
        "Часы работы (время заведения; закрытие раньше открытия — после полуночи; "
        "пусто — всегда открыто)"
    )


class PartnerBotSettingsInline(admin.StackedInline):
    model = PartnerBotSettings
    extra = 0
    max_num = 1
    fieldsets = (
        (
            "Модули · Базовые",
            {
                "description": (
                    "Профиль гостя и бонусный баланс считаются базовой частью "
                    "продукта. Меню нужно для заказов, корзины и быстрых продаж."
                ),
                "fields": (
                    "module_menu_enabled",
                ),
            },
        ),
        (
            "Модули · Сценарии заказа",
            {
                "description": (
                    "Столы, самовывоз, доставка и отправка почтой — независимые "
                    "сценарии. Можно включить любой набор; для заказов нужен хотя бы "
                    "один. У каждого удалённого сценария включите способы оплаты: "
                    "переводом заранее (на реквизиты ниже) и/или при получении; если "
                    "включены оба, гость выбирает. У отправки так же включаются службы "
                    "и варианты оплаты пересылки — гость выбирает из включённых. "
                    "Часы работы (для самовывоза и доставки) — в карточке партнёра."
                ),
                "fields": (
                    "module_tables_enabled",
                    "module_pickup_enabled",
                    ("pickup_pay_transfer", "pickup_pay_on_receipt"),
                    "module_delivery_enabled",
                    ("delivery_pay_transfer", "delivery_pay_on_receipt"),
                    "transfer_payment_details",
                    ("remote_unpaid_orders_limit", "transfer_wait_minutes"),
                    "delivery_fee",
                    "delivery_free_from",
                    "delivery_min_order",
                    "module_shipping_enabled",
                    ("shipping_nova_poshta", "shipping_ukrposhta", "shipping_meest"),
                    ("shipping_pay_transfer", "shipping_pay_on_receipt"),
                    ("shipping_cost_carrier", "shipping_cost_fixed"),
                    "shipping_fee",
                    "shipping_free_from",
                    "shipping_min_order",
                ),
            },
        ),
        (
            "Модули · Заказы и оплата",
            {
                "description": (
                    "Корзина требует меню и заказы. Счета требуют заказы; "
                    "запрос счёта гостем работает только при включённых столах."
                ),
                "fields": (
                    "module_orders_enabled",
                    "module_cart_enabled",
                    "module_billing_enabled",
                ),
            },
        ),
        (
            "Модули · Операции и отчёты",
            {
                "description": (
                    "Вызов персонала требует столы. Быстрые продажи требуют меню."
                ),
                "fields": (
                    "module_staff_call_enabled",
                    "module_quick_sale_enabled",
                    "module_reports_enabled",
                ),
            },
        ),
        (
            "Поведение бота",
            {
                "fields": (
                    "auto_close_table_session_after_payment",
                    "max_menu_items_per_category_message",
                    "duplicate_request_cooldown_seconds",
                    "allow_menu_without_session",
                    "staff_call_waiter_enabled",
                    "staff_call_bartender_enabled",
                    "staff_call_hookah_enabled",
                    "extra_config",
                )
            },
        ),
        (
            "Языки",
            {
                "description": (
                    "Гость видит язык своего Telegram, если он включён, иначе основной, "
                    "и может переключить язык в «Мой профиль». Кнопки и сообщения бота "
                    "на каждом языке меняются в разделе «Тексты бота»."
                ),
                "fields": (
                    "default_language",
                    ("language_uk", "language_ru", "language_en"),
                ),
            },
        ),
    )


@admin.register(Partner)
class PartnerAdmin(ScopedAdminMixin):
    admin_section = AdminSection.PARTNER_PROFILE
    partner_filter = "id"
    scope_partner_field = None
    allow_partner_add = False
    allow_partner_delete = False
    platform_only_fields = ("slug", "status")
    list_display = ("name", "slug", "status", "timezone", "created_at")
    list_filter = ("status", "timezone")
    search_fields = ("name", "slug", "contact_phone")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [PartnerBotSettingsInline, OpeningHoursInline]

    def get_list_display(self, request):
        if request.user.is_superuser:
            return self.list_display
        return ("name", "timezone", "contact_phone", "created_at")

    def get_list_filter(self, request):
        if request.user.is_superuser:
            return self.list_filter
        return ("timezone",)

    def get_search_fields(self, request):
        if request.user.is_superuser:
            return self.search_fields
        return ("name", "contact_phone")

    def get_prepopulated_fields(self, request, obj=None):
        if request.user.is_superuser:
            return self.prepopulated_fields
        return {}

    def get_fieldsets(self, request, obj=None):
        if request.user.is_superuser:
            return super().get_fieldsets(request, obj)
        return (
            (
                None,
                {
                    "fields": (
                        "name",
                        "timezone",
                        "contact_phone",
                    )
                },
            ),
        )

    def get_inline_instances(self, request, obj=None):
        if request.user.is_superuser:
            return super().get_inline_instances(request, obj)
        return []

    def save_formset(self, request, form, formset, change):
        super().save_formset(request, form, formset, change)
        if formset.model is PartnerBotSettings:
            for inline_form in formset.forms:
                if inline_form.instance.pk is not None:
                    _alert_owners_if_transfer_details_changed(
                        inline_form, inline_form.instance, request.user
                    )


@admin.register(BotInstance)
class BotInstanceAdmin(ScopedAdminMixin):
    admin_section = AdminSection.BOT_INSTANCES
    platform_only_fields = ("mode", "webhook_url")
    form = BotInstanceAdminForm
    list_display = (
        "display_name",
        "username",
        "partner",
        "mode",
        "token_status",
        "is_active",
        "created_at",
    )
    list_filter = ("mode", "is_active")
    search_fields = ("display_name", "username", "partner__name")
    readonly_fields = ("created_at", "updated_at")

    def get_list_display(self, request):
        if request.user.is_superuser:
            return self.list_display
        return ("display_name", "username", "token_status", "is_active", "created_at")

    def get_list_filter(self, request):
        if request.user.is_superuser:
            return self.list_filter
        return ("is_active",)

    def get_search_fields(self, request):
        if request.user.is_superuser:
            return self.search_fields
        return ("display_name", "username")

    @admin.display(description="Токен")
    def token_status(self, obj: BotInstance) -> str:
        return obj.token_status_label


def _alert_owners_if_transfer_details_changed(form, obj, actor) -> None:
    # AA-035: the owner is told whenever the payment details change.
    if "transfer_payment_details" in getattr(form, "changed_data", ()):
        notify_owners_about_transfer_details_change(settings=obj, actor=actor)


@admin.register(PartnerBotSettings)
class PartnerBotSettingsAdmin(ScopedAdminMixin):
    admin_section = AdminSection.BOT_CONTENT
    # Guests pay to these details: only the owner sees and changes them.
    owner_only_fields = ("transfer_payment_details",)
    platform_only_fields = ("extra_config",)
    list_display = (
        "partner",
        "module_menu_enabled",
        "module_delivery_enabled",
        "module_pickup_enabled",
        "module_shipping_enabled",
        "module_orders_enabled",
        "module_billing_enabled",
        "allow_menu_without_session",
        "updated_at",
    )
    list_filter = (
        "module_menu_enabled",
        "module_delivery_enabled",
        "module_pickup_enabled",
        "module_shipping_enabled",
        "module_orders_enabled",
        "module_billing_enabled",
        "module_staff_call_enabled",
        "allow_menu_without_session",
    )
    search_fields = ("partner__name", "partner__slug")
    fieldsets = PartnerBotSettingsInline.fieldsets

    def get_search_fields(self, request):
        if request.user.is_superuser:
            return self.search_fields
        return ("partner__name",)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        _alert_owners_if_transfer_details_changed(form, obj, request.user)


@admin.register(OpeningHours)
class OpeningHoursAdmin(ScopedAdminMixin):
    """Opening hours the owner edits: common ones and per module (pickup,
    delivery). A module without its own hours uses the common ones; without
    any hours it is always open."""

    admin_section = AdminSection.PARTNER_PROFILE
    list_display = ("scope", "weekday", "opens_at", "closes_at", "partner")
    list_filter = ("partner", "scope", "weekday")
    ordering = ("partner", "scope", "weekday", "opens_at")
    fields = ("partner", "scope", "weekday", "opens_at", "closes_at")


@admin.register(BotText)
class BotTextAdmin(ScopedAdminMixin):
    """The owner's own bot texts per language (PQ-040).

    «Каталог текстов» lists every text the bot can say with the current wording
    in each language; a link opens this text's own variant for editing.
    """

    admin_section = AdminSection.BOT_CONTENT
    form = BotTextForm
    list_display = ("language", "catalog_text", "own_text", "updated_at")
    list_filter = ("language",)
    search_fields = ("msgid", "text")
    change_list_template = "admin/partners/bottext/change_list.html"

    @admin.display(description="Текст в каталоге")
    def catalog_text(self, obj: BotText) -> str:
        return _short(obj.msgid)

    @admin.display(description="Свой текст")
    def own_text(self, obj: BotText) -> str:
        return _short(obj.text)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        return type("ScopedBotTextForm", (form,), {
            "scope_partner_id": self.get_partner_scope_id(request)
        })

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        key = request.GET.get("key", "")
        for msgid in editable_texts():
            if text_key(msgid) == key:
                initial["msgid"] = msgid
                break
        return initial

    def get_urls(self):
        urls = super().get_urls()
        catalog = path(
            "catalog/",
            self.admin_site.admin_view(self.catalog_view),
            name="partners_bottext_catalog",
        )
        return [catalog, *urls]

    def catalog_view(self, request):
        if not self.has_view_permission(request):
            raise PermissionDenied
        partner_id = self.get_partner_scope_id(request) or request.GET.get("partner") or None
        partners = Partner.objects.order_by("name") if self.is_platform_admin(request) else None
        if partner_id is None and partners is not None:
            first = partners.first()
            partner_id = first.id if first else None
        settings = PartnerBotSettings.objects.filter(partner_id=partner_id).first()
        languages = enabled_languages(settings)
        own = {
            (language, msgid): text_id
            for language, msgid, text_id in BotText.objects.filter(
                partner_id=partner_id
            ).values_list("language", "msgid", "id")
        }
        overrides = text_overrides(partner_id) if partner_id else {}
        catalog = sorted(editable_texts().items(), key=lambda item: (item[1], item[0]))
        current = {}
        for language in languages:
            with texts(language, overrides):
                current[language] = {msgid: _(msgid) for msgid, _group in catalog}
        query = request.GET.get("q", "").strip().lower()
        group = request.GET.get("group", "")
        rows = []
        for msgid, msgid_group in catalog:
            if group and msgid_group != group:
                continue
            cells = []
            for language in languages:
                text_id = own.get((language, msgid))
                if text_id:
                    url = reverse("admin:partners_bottext_change", args=[text_id])
                else:
                    params = {"key": text_key(msgid), "language": language}
                    if self.is_platform_admin(request) and partner_id:
                        params["partner"] = partner_id
                    url = reverse("admin:partners_bottext_add") + "?" + urlencode(params)
                cells.append(
                    {"text": current[language][msgid], "own": bool(text_id), "url": url}
                )
            haystack = " ".join([msgid, *(cell["text"] for cell in cells)]).lower()
            if query and query not in haystack:
                continue
            rows.append(
                {"msgid": msgid, "group": _(GROUP_LABELS[msgid_group]), "cells": cells}
            )
        page = Paginator(rows, 50).get_page(request.GET.get("page"))
        context = {
            **self.admin_site.each_context(request),
            "title": "Каталог текстов бота",
            "opts": self.model._meta,
            "page": page,
            "languages": [LANGUAGE_NAMES[code] for code in languages],
            "groups": [(key, _(label)) for key, label in GROUP_LABELS.items()],
            "group": group,
            "query": request.GET.get("q", ""),
            "partners": partners,
            "partner_id": str(partner_id or ""),
        }
        return TemplateResponse(request, "admin/partners/bottext/catalog.html", context)
