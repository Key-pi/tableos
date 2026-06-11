from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from apps.users.constants import AdminSection
from apps.users.forms import AdminUserChangeForm, AdminUserCreationForm
from apps.users.models import (
    AdminAccessProfile,
    AdminSectionPermission,
    GuestProfile,
    TelegramAccount,
    User,
)
from core.admin_mixins import ScopedAdminMixin


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
        ("Personal info", {"fields": ("first_name", "last_name", "email")}),
        ("Partner", {"fields": ("partner", "role")}),
        (
            "Permissions",
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
        ("Important dates", {"fields": ("last_login", "date_joined")}),
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

    def get_fieldsets(self, request, obj=None):
        if request.user.is_superuser:
            return self.fieldsets
        return (
            (None, {"fields": ("username", "password")}),
            ("Personal info", {"fields": ("first_name", "last_name", "email", "role")}),
            ("Status", {"fields": ("is_active",)}),
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

    @admin.display(description="Preset permissions")
    def permission_summary(self, obj):
        if not obj.pk:
            return "Save the profile first to preview generated section permissions."
        permissions = obj.section_permissions.order_by("section")
        if not permissions:
            return "No explicit section permissions yet."
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
