from datetime import timedelta

from django import forms
from django.contrib import admin
from django.db.models import Count, Q
from django.utils import timezone

from apps.analytics.models import PartnerDailyMetric, SourceVisit, TrafficSource
from apps.partners.models import BotInstance
from apps.users.constants import AdminSection
from core.admin_mixins import ScopedAdminMixin
from core.i18n import _
from core.i18n import gettext_lazy as _lazy


@admin.register(PartnerDailyMetric)
class PartnerDailyMetricAdmin(ScopedAdminMixin):
    admin_section = AdminSection.ANALYTICS
    allow_partner_add = False
    allow_partner_delete = False
    list_display = (
        "partner",
        "bucket_date",
        "revenue",
        "orders_count",
        "average_check",
        "active_guests",
    )
    list_filter = ("partner", "bucket_date")
    search_fields = ("partner__name",)


class SourcePeriodFilter(admin.SimpleListFilter):
    """Period of the click and new-guest counts in the source list."""

    title = _lazy("Период")
    parameter_name = "period"

    def lookups(self, request, model_admin):
        return (
            ("1", _("Сегодня и вчера")),
            ("7", _("7 дней")),
            ("30", _("30 дней")),
            ("90", _("90 дней")),
        )

    def queryset(self, request, queryset):
        # Called for every changelist request (also with no period chosen): it
        # adds the counts that the list columns show.
        visit_filter = Q()
        if self.value() in {"1", "7", "30", "90"}:
            since = timezone.now() - timedelta(days=int(self.value()))
            visit_filter = Q(visits__created_at__gte=since)
        return queryset.annotate(
            clicks=Count("visits", filter=visit_filter),
            new_guests=Count("visits", filter=visit_filter & Q(visits__is_new_guest=True)),
        )


class TrafficSourceForm(forms.ModelForm):
    class Meta:
        model = TrafficSource
        fields = ("partner", "name", "code", "is_active", "note")

    def clean_code(self):
        code = (self.cleaned_data.get("code") or "").strip().lower()
        # A venue admin has no partner field (the scope sets it); a platform
        # admin picks the partner in the form.
        partner_id = getattr(self, "scope_partner_id", None)
        if partner_id is None and self.cleaned_data.get("partner") is not None:
            partner_id = self.cleaned_data["partner"].id
        if code and partner_id:
            duplicate = TrafficSource.objects.filter(partner_id=partner_id, code=code)
            if self.instance.pk:
                duplicate = duplicate.exclude(pk=self.instance.pk)
            if duplicate.exists():
                raise forms.ValidationError(_("Источник с таким кодом уже есть."))
        return code


@admin.register(TrafficSource)
class TrafficSourceAdmin(ScopedAdminMixin):
    """Traffic sources: the owner makes a link per channel and compares them."""

    admin_section = AdminSection.ANALYTICS
    form = TrafficSourceForm
    list_display = ("name", "link", "clicks_column", "new_guests_column", "is_active")
    list_filter = (SourcePeriodFilter, "is_active", "partner")
    search_fields = ("name", "code")

    def get_readonly_fields(self, request, obj=None):
        # The code is printed on flyers and QR codes: it never changes.
        return ("code", "link") if obj is not None else ("link",)

    def get_form(self, request, obj=None, change=False, **kwargs):
        form_class = super().get_form(request, obj, change=change, **kwargs)
        scope_partner_id = self.get_partner_scope_id(request)

        class ScopedForm(form_class):
            pass

        ScopedForm.scope_partner_id = scope_partner_id
        return ScopedForm

    @admin.display(description=_lazy("Ссылка (её же можно превратить в QR-код)"))
    def link(self, obj):
        if obj is None or not obj.pk:
            return _("появится после сохранения")
        bot = (
            BotInstance.objects.filter(partner_id=obj.partner_id, is_active=True)
            .order_by("created_at")
            .first()
        )
        if bot is None:
            return _(
                "/start {deep_link_payload} (у заведения нет активного бота)",
                deep_link_payload=obj.deep_link_payload,
            )
        return obj.telegram_start_url(bot.username)

    @admin.display(description=_lazy("Переходы"), ordering="clicks")
    def clicks_column(self, obj):
        return getattr(obj, "clicks", "—")

    @admin.display(description=_lazy("Новые гости"), ordering="new_guests")
    def new_guests_column(self, obj):
        return getattr(obj, "new_guests", "—")


@admin.register(SourceVisit)
class SourceVisitAdmin(ScopedAdminMixin):
    """Every /start through a source link; read-only."""

    admin_section = AdminSection.ANALYTICS
    allow_partner_add = False
    allow_partner_delete = False
    list_display = ("created_at", "source", "guest", "is_new_guest")
    list_filter = ("source", "is_new_guest", "partner")
    readonly_fields = ("partner", "source", "guest", "is_new_guest", "created_at")
    list_select_related = ("source", "guest__telegram_account")

    def has_change_permission(self, request, obj=None):
        # Viewing only: the log is written by the bot.
        return False

    def get_list_filter(self, request):
        filters = super().get_list_filter(request)
        partner_scope_id = self.get_partner_scope_id(request)
        if partner_scope_id is None:
            return filters
        return tuple(("source", ScopedSourceFilter) if f == "source" else f for f in filters)


class ScopedSourceFilter(admin.RelatedFieldListFilter):
    """Source choices of the admin's own venue only."""

    def field_choices(self, field, request, model_admin):
        partner_scope_id = model_admin.get_partner_scope_id(request)
        return [
            (source.pk, str(source))
            for source in TrafficSource.objects.filter(partner_id=partner_scope_id)
        ]
