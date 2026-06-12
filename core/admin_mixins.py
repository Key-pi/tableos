from __future__ import annotations

from django.contrib import admin

from apps.partners.models import Partner
from apps.users.models import AdminAccessProfile, User


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


class ScopedAdminMixin(admin.ModelAdmin):
    """Reusable admin guard that scopes partner data and section permissions."""

    admin_section: str = ""
    save_on_top = True
    list_per_page = 25
    partner_filter: str | None = "partner_id"
    scope_partner_field: str | None = "partner"
    platform_only: bool = False
    platform_only_fields: tuple[str, ...] = ()
    sensitive_fields: tuple[str, ...] = ()
    allow_partner_add = True
    allow_partner_delete = True

    def is_platform_admin(self, request) -> bool:
        return request.user.is_superuser

    def get_partner_scope_id(self, request):
        if self.is_platform_admin(request):
            return None
        profile = get_admin_access_profile(request.user)
        return profile.partner_id if profile is not None else None

    def has_module_permission(self, request):
        if self.platform_only:
            return self.is_platform_admin(request)
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
        if self.is_platform_admin(request) or not self.partner_filter:
            return queryset
        partner_scope_id = self.get_partner_scope_id(request)
        if partner_scope_id is None:
            return queryset.none()
        return queryset.filter(**{self.partner_filter: partner_scope_id})

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

        for deleted_object in formset.deleted_objects:
            deleted_object.delete()

        for instance in instances:
            if (
                not self.is_platform_admin(request)
                and partner_scope_id is not None
                and hasattr(instance, "partner_id")
                and not instance.partner_id
            ):
                instance.partner_id = partner_scope_id
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
        if "partner" in field_names:
            return queryset.filter(partner_id=partner_scope_id)
        if model is Partner:
            return queryset.filter(id=partner_scope_id)
        if model is User:
            return queryset.filter(partner_id=partner_scope_id, is_superuser=False)
        return queryset
