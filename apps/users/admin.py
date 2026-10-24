from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.core.exceptions import ValidationError

from apps.users.constants import (
    ADMIN_ACCESS_PRESET_TEMPLATES,
    AdminAccessPreset,
    AdminSection,
    UserRole,
)
from apps.users.forms import AdminUserChangeForm, AdminUserCreationForm
from apps.users.models import (
    AdminAccessProfile,
    AdminSectionPermission,
    GuestProfile,
    TelegramAccount,
    User,
)
from core.admin_mixins import (
    ScopedAdminMixin,
    get_admin_access_profile,
    is_partner_owner_or_superuser,
)
from core.i18n import _
from core.i18n import gettext_lazy as _lazy

# AA-036 (PQ-035): owner records and rights are changed only by the owner or
# a superuser; anyone else with "Admin access" grants at most their own rights.
PERMISSION_FLAGS = ("can_view", "can_add", "can_change", "can_delete", "can_manage_sensitive")


def is_owner_user(user) -> bool:
    if user is None:
        return False
    return user.role == UserRole.OWNER or AdminAccessProfile.objects.filter(
        user=user, is_partner_owner=True
    ).exists()


def actor_rights(actor) -> dict[str, set[str]] | None:
    """Section → granted flags of a non-owner actor; None means unlimited."""

    if is_partner_owner_or_superuser(actor):
        return None
    profile = get_admin_access_profile(actor)
    if profile is None:
        return {}
    return {
        permission.section: {flag for flag in PERMISSION_FLAGS if getattr(permission, flag)}
        for permission in profile.section_permissions.all()
    }


def rights_above(actor, rights: dict) -> list[tuple[str, str]]:
    """The (section, flag) pairs in ``rights`` the actor does not have."""

    own = actor_rights(actor)
    if own is None:
        return []
    return [
        (section, flag)
        for section, flags in rights.items()
        for flag, value in flags.items()
        if value and flag not in own.get(section, set())
    ]


def _rights_error(excess: list[tuple[str, str]]) -> ValidationError:
    sections = ", ".join(sorted({str(AdminSection(section).label) for section, _flag in excess}))
    return ValidationError(
        _(
            "Нельзя выдать права больше своих (разделы: {sections}). Это может сделать владелец.",
            sections=sections,
        )
    )


class AdminSectionPermissionFormSet(forms.BaseInlineFormSet):
    actor = None

    def clean(self):
        super().clean()
        rights = {}
        for form in self.forms:
            data = getattr(form, "cleaned_data", None) or {}
            if not data or data.get("DELETE") or not data.get("section"):
                continue
            rights[data["section"]] = {flag: data.get(flag, False) for flag in PERMISSION_FLAGS}
        excess = rights_above(self.actor, rights)
        if excess:
            raise _rights_error(excess)


class AdminSectionPermissionInline(admin.TabularInline):
    model = AdminSectionPermission
    extra = 0
    fields = (
        "section",
        "can_view",
        "can_add",
        "can_change",
        "can_delete",
        "can_manage_sensitive",
    )

    formset = AdminSectionPermissionFormSet

    def get_formset(self, request, obj=None, **kwargs):
        formset_class = super().get_formset(request, obj, **kwargs)
        return type(
            "ActorBoundAdminSectionPermissionFormSet", (formset_class,), {"actor": request.user}
        )

    def has_add_permission(self, request, obj=None):
        if obj and obj.preset != "custom" and not request.user.is_superuser:
            return False
        return super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        if obj and obj.preset != "custom" and not request.user.is_superuser:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj and obj.preset != "custom" and not request.user.is_superuser:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(User)
class UserAdmin(ScopedAdminMixin, DjangoUserAdmin):
    admin_section = AdminSection.ADMIN_ACCESS
    partner_filter = "partner_id"
    scope_partner_field = "partner"
    form = AdminUserChangeForm
    add_form = AdminUserCreationForm
    list_display = ("username", "email", "role", "partner", "is_active", "is_staff")
    list_filter = ("role", "is_active", "partner")
    search_fields = ("username", "email", "first_name", "last_name")
    ordering = ("username",)
    platform_only_fields = ("is_staff", "is_superuser", "groups", "user_permissions")

    fieldsets = (
        (None, {"fields": ("username", "password")}),
        (_lazy("Личные данные"), {"fields": ("first_name", "last_name", "email")}),
        (_lazy("Заведение"), {"fields": ("partner", "role")}),
        (
            _lazy("Права платформы"),
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        (_lazy("Даты"), {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "username",
                    "email",
                    "first_name",
                    "last_name",
                    "partner",
                    "role",
                    "is_active",
                    "password1",
                    "password2",
                ),
            },
        ),
    )

    def has_change_permission(self, request, obj=None):
        if obj is not None and not self._may_touch(request, obj):
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and not self._may_touch(request, obj):
            return False
        return super().has_delete_permission(request, obj)

    @staticmethod
    def _may_touch(request, user) -> bool:
        # AA-036: the owner's account (and password) is the owner's own.
        return is_partner_owner_or_superuser(request.user) or not is_owner_user(user)

    def get_form(self, request, obj=None, **kwargs):
        form_class = super().get_form(request, obj, **kwargs)
        if is_partner_owner_or_superuser(request.user) or "role" not in form_class.base_fields:
            return form_class

        class NoOwnerRoleForm(form_class):
            def __init__(self, *args, **form_kwargs):
                super().__init__(*args, **form_kwargs)
                field = self.fields["role"]
                field.choices = [
                    choice for choice in field.choices if choice[0] != UserRole.OWNER
                ]

        return NoOwnerRoleForm

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if self.is_platform_admin(request):
            return queryset
        # AA-026: platform accounts are never part of a venue's user list,
        # even when a superuser happens to carry the venue's partner.
        return queryset.filter(is_superuser=False)

    def get_fieldsets(self, request, obj=None):
        # On the add page Django must use the add_fieldsets (password1/password2),
        # otherwise the creation form renders against change fields and fails
        # validation with no visible error.
        if obj is None:
            return self.get_add_fieldsets(request)
        if request.user.is_superuser:
            return self.fieldsets
        return (
            (None, {"fields": ("username", "password")}),
            (_("Личные данные"), {"fields": ("first_name", "last_name", "email", "role")}),
            (_("Статус"), {"fields": ("is_active",)}),
        )

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(super().get_readonly_fields(request, obj))
        if request.user.is_superuser:
            return readonly_fields
        readonly_fields.extend(["last_login", "date_joined"])
        return tuple(dict.fromkeys(readonly_fields))

    def get_add_fieldsets(self, request):
        if request.user.is_superuser:
            return self.add_fieldsets
        return (
            (
                None,
                {
                    "classes": ("wide",),
                    "fields": (
                        "username",
                        "email",
                        "first_name",
                        "last_name",
                        "role",
                        "is_active",
                        "password1",
                        "password2",
                    ),
                },
            ),
        )


@admin.register(TelegramAccount)
class TelegramAccountAdmin(ScopedAdminMixin):
    platform_only = True
    list_display = ("telegram_id", "username", "first_name", "last_name", "is_blocked")
    search_fields = ("telegram_id", "username", "first_name", "last_name")
    ordering = ("telegram_id",)


@admin.register(GuestProfile)
class GuestProfileAdmin(ScopedAdminMixin):
    admin_section = AdminSection.BONUSES
    list_display = (
        "customer_code",
        "telegram_account",
        "partner",
        "loyalty_balance",
        "is_subscribed",
        "updated_at",
    )
    list_filter = ("partner", "is_subscribed")
    search_fields = (
        "customer_code",
        "telegram_account__username",
        "telegram_account__telegram_id",
        "partner__name",
    )
    allow_partner_delete = False
    readonly_fields = (
        "partner",
        "telegram_account",
        "customer_code",
        "first_visit_at",
        "last_visit_at",
        "loyalty_balance",
        "created_at",
        "updated_at",
    )


@admin.register(AdminAccessProfile)
class AdminAccessProfileAdmin(ScopedAdminMixin):
    admin_section = AdminSection.ADMIN_ACCESS
    list_display = (
        "user",
        "partner",
        "preset",
        "is_partner_owner",
        "can_access_admin",
        "is_active",
    )
    list_filter = ("partner", "preset", "is_partner_owner", "can_access_admin", "is_active")
    search_fields = ("user__username", "user__email", "partner__name")
    inlines = [AdminSectionPermissionInline]
    readonly_fields = ("permission_summary",)
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "user",
                    "partner",
                    "preset",
                    "is_partner_owner",
                    "can_access_admin",
                    "is_active",
                    "permission_summary",
                )
            },
        ),
    )

    def has_change_permission(self, request, obj=None):
        if obj is not None and not self._may_touch(request, obj):
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and not self._may_touch(request, obj):
            return False
        return super().has_delete_permission(request, obj)

    @staticmethod
    def _may_touch(request, profile) -> bool:
        # AA-036: a non-owner changes neither the owner's access nor their own.
        if is_partner_owner_or_superuser(request.user):
            return True
        if (
            profile.is_partner_owner
            or profile.user_id == request.user.id
            or is_owner_user(profile.user)
        ):
            return False
        # Nor a profile that already has more rights than the actor.
        target_rights = {
            permission.section: {flag: getattr(permission, flag) for flag in PERMISSION_FLAGS}
            for permission in profile.section_permissions.all()
        }
        return not rights_above(request.user, target_rights)

    def get_exclude(self, request, obj=None):
        exclude = list(super().get_exclude(request, obj) or [])
        if not is_partner_owner_or_superuser(request.user):
            exclude.append("is_partner_owner")
        return tuple(dict.fromkeys(exclude)) or None

    def rights_above_actor(self, request, rights: dict) -> list[tuple[str, str]]:
        return rights_above(request.user, rights)

    def check_rights_not_above_actor(self, request, profile, rights: dict) -> None:
        excess = self.rights_above_actor(request, rights)
        if excess:
            raise _rights_error(excess)

    def get_form(self, request, obj=None, **kwargs):
        form_class = super().get_form(request, obj, **kwargs)
        if is_partner_owner_or_superuser(request.user):
            return form_class
        actor = request.user
        allowed_presets = {
            preset
            for preset in AdminAccessPreset.values
            if preset != AdminAccessPreset.OWNER
            and not rights_above(actor, ADMIN_ACCESS_PRESET_TEMPLATES.get(preset, {}))
        }

        class LimitedAccessProfileForm(form_class):
            def __init__(self, *args, **form_kwargs):
                super().__init__(*args, **form_kwargs)
                if "preset" in self.fields:
                    self.fields["preset"].choices = [
                        choice
                        for choice in self.fields["preset"].choices
                        if choice[0] in allowed_presets
                    ]
                if "user" in self.fields:
                    self.fields["user"].queryset = (
                        self.fields["user"]
                        .queryset.exclude(id=actor.id)
                        .exclude(role=UserRole.OWNER)
                        .exclude(admin_access_profile__is_partner_owner=True)
                    )

            def clean(self):
                cleaned = super().clean()
                preset = cleaned.get("preset")
                if preset == AdminAccessPreset.OWNER or (
                    preset and preset not in allowed_presets
                ):
                    raise ValidationError(_("Этот набор прав может выдать только владелец."))
                return cleaned

        return LimitedAccessProfileForm

    @admin.display(description=_lazy("Права по набору"))
    def permission_summary(self, obj):
        if not obj.pk:
            return _("Сохраните профиль, чтобы увидеть права набора.")
        permissions = obj.section_permissions.order_by("section")
        if not permissions:
            return _("Прав на разделы пока нет.")
        return "\n".join(
            (
                f"{permission.get_section_display()}: "
                f"view={permission.can_view}, add={permission.can_add}, "
                f"change={permission.can_change}, delete={permission.can_delete}, "
                f"sensitive={permission.can_manage_sensitive}"
            )
            for permission in permissions
        )

    def save_model(self, request, obj, form, change):
        if (
            not request.user.is_superuser
            and obj.user.partner_id
            and obj.user.partner_id != obj.partner_id
        ):
            obj.user.partner_id = obj.partner_id
        super().save_model(request, obj, form, change)
