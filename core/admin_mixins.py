from __future__ import annotations

import json
from dataclasses import dataclass

from django.contrib import messages
from django.contrib.admin import display
from django.contrib.admin.options import IS_POPUP_VAR
from django.contrib.admin.utils import quote, unquote
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseNotAllowed, HttpResponseRedirect
from django.urls import path, reverse
from django.utils.functional import lazy
from django.utils.html import format_html
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.safestring import SafeString, mark_safe
from unfold.admin import ModelAdmin as UnfoldModelAdmin
from unfold.admin import checkbox as unfold_checkbox

from apps.partners.models import Partner, PartnerBotSettings
from apps.partners.networks import admin_partner_ids
from apps.users.models import AdminAccessProfile, User
from core.i18n import _

# The venue an Admin user of a network works with now (PQ-047).
ADMIN_POINT_SESSION_KEY = "admin_partner_id"


def current_admin_partner_id(request):
    """The venue Admin shows: the one chosen in the header if this person
    may still open it, else their own venue. Checked on every request."""

    cached = getattr(request, "_admin_partner_scope", None)
    if cached is not None:
        return cached[0]
    points = admin_partner_ids(request.user)
    chosen = request.session.get(ADMIN_POINT_SESSION_KEY) if hasattr(request, "session") else None
    scope = next((point for point in points if str(point) == chosen), None)
    if scope is None and points:
        scope = points[0]
    request._admin_partner_scope = (scope,)
    return scope


def get_admin_access_profile(user) -> AdminAccessProfile | None:
    if not getattr(user, "is_authenticated", False) or user.is_superuser:
        return None
    try:
        profile = user.admin_access_profile
    except AdminAccessProfile.DoesNotExist:
        return None
    if not (profile.can_access_admin and profile.is_active):
        return None
    return profile


def has_admin_section_permission(user, section: str, action: str) -> bool:
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    if user.is_superuser:
        return True
    profile = get_admin_access_profile(user)
    if profile is None:
        return False
    return profile.has_section_permission(section, action)


def has_admin_sensitive_permission(user, section: str) -> bool:
    return has_admin_section_permission(user, section, "manage_sensitive")


def is_partner_owner_or_superuser(user) -> bool:
    """The venue owner (active owner admin profile) or a platform superuser."""

    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    if user.is_superuser:
        return True
    profile = get_admin_access_profile(user)
    return profile is not None and profile.is_partner_owner


def _toggle_all_checkbox() -> SafeString:
    return mark_safe(unfold_checkbox.render("action_toggle_all", 1))


class BoteraModelAdmin(UnfoldModelAdmin):
    """Every Admin page of Botera Shop: Unfold's ModelAdmin (admin step 1,
    2026-11-09) — its widgets, action buttons and filters, not only its page
    styles."""

    warn_unsaved_form = True
    list_filter_submit = True

    # Unfold renders the «select all» box once, at import, in that moment's
    # language; drawn per request it follows each user's language (PQ-040).
    @display(description=lazy(_toggle_all_checkbox, SafeString)())
    def action_checkbox(self, obj):
        return super().action_checkbox(obj)


class ScopedAdminMixin(BoteraModelAdmin):
    """Reusable admin guard that scopes partner data and section permissions."""

    admin_section: str = ""
    partner_filter: str | None = "partner_id"
    scope_partner_field: str | None = "partner"
    platform_only: bool = False
    platform_only_fields: tuple[str, ...] = ()
    sensitive_fields: tuple[str, ...] = ()
    # Hidden from everyone but the venue owner and superusers (AA-035).
    owner_only_fields: tuple[str, ...] = ()
    allow_partner_add = True
    allow_partner_delete = True
    # A table that belongs to another page (order lines, status history, bill
    # lines…): not in a venue's menu or index; it opens as a tab of that page
    # (apps/users/admin_navigation.py). Rights to open it stay the section's.
    service_table: bool = False
    # A venue has one record here (its card, its bot settings): its list
    # opens that record's form.
    single_record: bool = False
    # Columns that only tell venues apart: Admin of a venue shows one venue.
    partner_columns: tuple[str, ...] = ("partner",)

    def is_platform_admin(self, request) -> bool:
        return request.user.is_superuser

    def module_on(self, request, field: str) -> bool:
        """A module's part of a page shows while the venue's module is on
        (the platform sees it always). One read of the venue's settings per
        request: a page asks about several modules."""

        partner_id = self.get_partner_scope_id(request)
        if partner_id is None:
            return self.is_platform_admin(request)
        cached = getattr(request, "_venue_settings_row", None)
        if cached is None or cached[0] != partner_id:
            row = PartnerBotSettings.objects.filter(partner_id=partner_id).values().first()
            cached = request._venue_settings_row = (partner_id, row or {})
        return bool(cached[1].get(field))

    def get_partner_scope_id(self, request):
        if self.is_platform_admin(request):
            return None
        return current_admin_partner_id(request)

    def has_module_permission(self, request):
        if self.platform_only:
            return self.is_platform_admin(request)
        if self.service_table and not self.is_platform_admin(request):
            return False
        return has_admin_section_permission(request.user, self.admin_section, "view")

    def has_view_permission(self, request, obj=None):
        if self.platform_only:
            return self.is_platform_admin(request)
        allowed = has_admin_section_permission(request.user, self.admin_section, "view")
        if not allowed:
            return False
        if obj is None or self.is_platform_admin(request):
            return True
        return self.get_queryset(request).filter(pk=obj.pk).exists()

    def has_add_permission(self, request):
        if self.platform_only:
            return self.is_platform_admin(request)
        if not self.allow_partner_add and not self.is_platform_admin(request):
            return False
        return has_admin_section_permission(request.user, self.admin_section, "add")

    def has_change_permission(self, request, obj=None):
        if self.platform_only:
            return self.is_platform_admin(request)
        allowed = has_admin_section_permission(request.user, self.admin_section, "change")
        if not allowed:
            return False
        if obj is None or self.is_platform_admin(request):
            return True
        return self.get_queryset(request).filter(pk=obj.pk).exists()

    def has_delete_permission(self, request, obj=None):
        if self.platform_only:
            return self.is_platform_admin(request)
        if not self.allow_partner_delete and not self.is_platform_admin(request):
            return False
        allowed = has_admin_section_permission(request.user, self.admin_section, "delete")
        if not allowed:
            return False
        if obj is None or self.is_platform_admin(request):
            return True
        return self.get_queryset(request).filter(pk=obj.pk).exists()

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if self.is_platform_admin(request):
            return queryset
        if self.platform_only:
            # Platform-only data (e.g. the global Telegram identity) is never in
            # a venue's scope; a direct URL then answers 404 instead of failing.
            return queryset.none()
        if not self.partner_filter:
            return queryset
        partner_scope_id = self.get_partner_scope_id(request)
        if partner_scope_id is None:
            return queryset.none()
        return queryset.filter(**{self.partner_filter: partner_scope_id})

    def get_list_display(self, request):
        list_display = super().get_list_display(request)
        if self.is_platform_admin(request):
            return list_display
        return tuple(column for column in list_display if column not in self.partner_columns)

    def changelist_view(self, request, extra_context=None):
        if (
            self.single_record
            and request.method == "GET"
            and not self.is_platform_admin(request)
            and IS_POPUP_VAR not in request.GET
            and self.has_view_permission(request)
        ):
            pks = list(self.get_queryset(request).values_list("pk", flat=True)[:2])
            if len(pks) == 1:
                opts = self.model._meta
                return HttpResponseRedirect(
                    reverse(
                        f"{self.admin_site.name}:{opts.app_label}_{opts.model_name}_change",
                        args=(pks[0],),
                    )
                )
        return super().changelist_view(request, extra_context)

    def get_list_filter(self, request):
        list_filters = super().get_list_filter(request)
        if self.is_platform_admin(request):
            return list_filters
        return tuple(
            list_filter
            for list_filter in list_filters
            if not self._is_partner_list_filter(list_filter)
        )

    @staticmethod
    def _is_partner_list_filter(list_filter) -> bool:
        field_name = list_filter[0] if isinstance(list_filter, tuple) else list_filter
        return field_name in {"partner", "partner_id"}

    def get_exclude(self, request, obj=None):
        exclude = list(super().get_exclude(request, obj) or [])
        if not self.is_platform_admin(request):
            if self.scope_partner_field:
                field_names = {field.name for field in self.model._meta.fields}
                if (
                    self.scope_partner_field in field_names
                    and self.scope_partner_field not in exclude
                ):
                    exclude.append(self.scope_partner_field)
            for field_name in self.platform_only_fields:
                if field_name not in exclude:
                    exclude.append(field_name)
            if not has_admin_sensitive_permission(request.user, self.admin_section):
                for field_name in self.sensitive_fields:
                    if field_name not in exclude:
                        exclude.append(field_name)
            if not is_partner_owner_or_superuser(request.user):
                for field_name in self.owner_only_fields:
                    if field_name not in exclude:
                        exclude.append(field_name)
        return tuple(exclude) or None

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        if self.is_platform_admin(request):
            return fieldsets

        excluded_fields = set(self.get_exclude(request, obj) or ())
        if not excluded_fields:
            return fieldsets

        filtered_fieldsets = []
        for name, options in fieldsets:
            fields = options.get("fields", ())
            filtered_fields = self._filter_fieldset_fields(fields, excluded_fields)
            if not filtered_fields:
                continue
            filtered_options = {**options, "fields": filtered_fields}
            filtered_fieldsets.append((name, filtered_options))
        return tuple(filtered_fieldsets)

    def _filter_fieldset_fields(self, fields, excluded_fields: set[str]):
        filtered = []
        for field in fields:
            if isinstance(field, (list, tuple)):
                nested_fields = self._filter_fieldset_fields(field, excluded_fields)
                if nested_fields:
                    filtered.append(tuple(nested_fields))
                continue
            if field not in excluded_fields:
                filtered.append(field)
        return tuple(filtered)

    def save_model(self, request, obj, form, change):
        if not self.is_platform_admin(request) and self.scope_partner_field:
            partner_scope_id = self.get_partner_scope_id(request)
            if partner_scope_id is not None and hasattr(obj, f"{self.scope_partner_field}_id"):
                setattr(obj, f"{self.scope_partner_field}_id", partner_scope_id)
        super().save_model(request, obj, form, change)

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        partner_scope_id = self.get_partner_scope_id(request)

        if not self.is_platform_admin(request) and partner_scope_id is not None:
            for instance in [*instances, *formset.deleted_objects]:
                if not hasattr(instance, "partner_id"):
                    continue
                if instance.partner_id not in {None, partner_scope_id}:
                    raise PermissionDenied(_("Запись принадлежит другому заведению."))
                instance.partner_id = partner_scope_id

        for deleted_object in formset.deleted_objects:
            deleted_object.delete()

        for instance in instances:
            # A formset may save its rows its own way (the menu never writes
            # back a stock level an order changed meanwhile).
            save_instance = getattr(formset, "save_instance", None)
            if save_instance is not None:
                save_instance(instance)
            else:
                instance.save()

        formset.save_m2m()

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        kwargs["queryset"] = self._scoped_related_queryset(
            request=request,
            queryset=kwargs.get("queryset") or db_field.remote_field.model._default_manager.all(),
        )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        kwargs["queryset"] = self._scoped_related_queryset(
            request=request,
            queryset=kwargs.get("queryset") or db_field.remote_field.model._default_manager.all(),
        )
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    def _scoped_related_queryset(self, *, request, queryset):
        if self.is_platform_admin(request):
            return queryset

        partner_scope_id = self.get_partner_scope_id(request)
        if partner_scope_id is None:
            return queryset.none()

        model = queryset.model
        field_names = {field.name for field in model._meta.fields}
        if model is User:
            # Copies of the network staff are not venue accounts (PQ-047).
            return queryset.filter(partner_id=partner_scope_id, is_superuser=False).exclude(
                employee_profile__mirrored_from__isnull=False
            )
        if "partner" in field_names:
            return queryset.filter(partner_id=partner_scope_id)
        if model is Partner:
            return queryset.filter(id=partner_scope_id)
        return queryset


class SectionInlineMixin:
    """An inline editable by the rights of its Admin section, like the page
    it sits on (Django's own model permissions are not used for venues)."""

    admin_section: str = ""

    def _allowed(self, request, action: str) -> bool:
        return has_admin_section_permission(request.user, self.admin_section, action)

    def has_view_permission(self, request, obj=None):
        return self._allowed(request, "view")

    def has_add_permission(self, request, obj=None):
        return self._allowed(request, "change")

    def has_change_permission(self, request, obj=None):
        return self._allowed(request, "change")

    def has_delete_permission(self, request, obj=None):
        return self._allowed(request, "change")


class RecordsInlineMixin:
    """Rows the system wrote — lines of an order, payments of a bill, history:
    shown on the page to whoever may view its section, changed here by no one
    of a venue (admin step 4, PQ-050). Without it a venue did not see them at
    all: Django's own model permissions are not used for venues. The platform
    keeps what it had."""

    admin_section: str = ""
    # Columns that say nothing inside the page of one venue's record.
    venue_hidden_fields: tuple[str, ...] = ("partner", "updated_at")

    def has_view_permission(self, request, obj=None):
        if request.user.is_superuser:
            return super().has_view_permission(request, obj)
        return has_admin_section_permission(request.user, self.admin_section, "view")

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser and super().has_delete_permission(request, obj)

    def has_add_permission(self, request, obj=None):
        return False

    def get_fields(self, request, obj=None):
        fields = super().get_fields(request, obj)
        if request.user.is_superuser:
            return fields
        return [name for name in fields if name not in self.venue_hidden_fields]


# --- buttons on a record's page (admin step 3, PQ-050) -------------------------


@dataclass(frozen=True)
class ObjectAction:
    """One button: what may be done with the record now.

    Pressed, it posts ``code`` (and ``field`` when it asks for one value,
    e.g. a tracking number); ``url`` makes it a link to another page
    instead. ``confirm`` is asked before a press that cannot be undone."""

    code: str
    label: str
    icon: str = ""
    style: str = "default"  # "primary" | "danger" | "default"
    confirm: str = ""
    field: str = ""  # name of the one value to type
    field_label: str = ""
    url: str = ""
    # Posted to Admin's own action of the list (a reason form, PQ-028).
    list_action: str = ""


class ObjectActionsMixin:
    """What may be done with a record now, as buttons over its page and, for
    the first one, in its row of the list.

    ``get_object_actions`` says which buttons; a press posts to
    ``<id>/act/``, is checked again (the page may be out of date: someone
    else may have moved the order) and runs ``run_object_action`` — the
    owner module's service, as the bot does. A refused press says why."""

    change_form_outer_before_template = "admin/botera/object_actions.html"
    object_action_errors: tuple[type[Exception], ...] = ()

    def get_object_actions(self, request, obj) -> list[ObjectAction]:
        return []

    def run_object_action(self, request, obj, code: str, data) -> str:
        raise NotImplementedError

    def _admin_url(self, view: str, *args) -> str:
        opts = self.model._meta
        return reverse(
            f"{self.admin_site.name}:{opts.app_label}_{opts.model_name}_{view}", args=args
        )

    def get_urls(self):
        opts = self.model._meta
        return [
            path(
                "<path:object_id>/act/",
                self.admin_site.admin_view(self.object_action_view),
                name=f"{opts.app_label}_{opts.model_name}_act",
            ),
            *super().get_urls(),
        ]

    def _change_url(self, obj) -> str:
        return self._admin_url("change", quote(obj.pk))

    def object_action_view(self, request, object_id):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        obj = self.get_object(request, unquote(object_id))
        if obj is None:
            raise Http404
        if not self.has_change_permission(request, obj):
            raise PermissionDenied
        code = request.POST.get("object_action", "")
        offered = {action.code for action in self.get_object_actions(request, obj)
                   if not action.url and not action.list_action}
        if code not in offered:
            messages.warning(request, _(
                "Это действие уже недоступно — страница устарела. Посмотрите, что изменилось."
            ))
        else:
            try:
                notice = self.run_object_action(request, obj, code, request.POST)
            except self.object_action_errors as exc:
                messages.warning(request, str(exc))
            else:
                messages.success(request, notice)
        if request.GET.get("from") == "list":
            # Back to the list as it was (its filters and page).
            back = request.META.get("HTTP_REFERER", "")
            if not url_has_allowed_host_and_scheme(
                back, allowed_hosts={request.get_host()}, require_https=request.is_secure()
            ):
                back = self._admin_url("changelist")
            return HttpResponseRedirect(back)
        return HttpResponseRedirect(self._change_url(obj))

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        if object_id:
            obj = self.get_object(request, unquote(object_id))
            if obj is not None and self.has_change_permission(request, obj):
                extra_context = {
                    **(extra_context or {}),
                    "object_actions": self.get_object_actions(request, obj),
                    "object_action_url": self._admin_url("act", quote(obj.pk)),
                    "object_changelist_url": self._admin_url("changelist"),
                }
        return super().changeform_view(request, object_id, form_url, extra_context)

    def object_action_button(self, obj, action: ObjectAction) -> SafeString:
        """The button of a list row: it posts through the list's own form
        (which carries the CSRF token) and comes back to the list."""

        url = self._admin_url("act", quote(obj.pk)) + "?from=list"
        confirm = (
            format_html(' onclick="return confirm({})"', json.dumps(str(action.confirm)))
            if action.confirm else ""
        )
        return format_html(
            '<button type="submit" form="changelist-form" formaction="{}" formnovalidate '
            'name="object_action" value="{}" class="row-action row-action-{}"{}>{}</button>',
            url, action.code, action.style, confirm, action.label,
        )
