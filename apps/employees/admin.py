from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied

from apps.employees.forms import EmployeeProfileAdminForm
from apps.employees.invites import (
    StaffInviteError,
    create_staff_invite,
    decide_staff_invite,
    invite_link,
    revoke_staff_invite,
    roles_invitable_by,
)
from apps.employees.models import EmployeeProfile, StaffInvite
from apps.partners.networks import bot_for_partner
from apps.users.admin import is_owner_user
from apps.users.constants import AdminSection
from apps.users.models import User
from core.admin_mixins import ScopedAdminMixin, is_partner_owner_or_superuser
from core.i18n import _
from core.i18n import gettext_lazy as _lazy


@admin.register(EmployeeProfile)
class EmployeeProfileAdmin(ScopedAdminMixin):
    admin_section = AdminSection.EMPLOYEES
    form = EmployeeProfileAdminForm
    list_display = (
        "user",
        "partner",
        "telegram_binding",
        "title",
        "hourly_rate",
        "commission_rate",
        "bot_notifications_enabled",
        "notify_on_order_created",
        "notify_on_order_status_changed",
        "notify_on_guest_calls",
        "notify_on_billing_requests",
        "can_adjust_bills",
        "is_active",
    )
    list_filter = ("partner", "is_active")
    search_fields = (
        "user__username",
        "user__first_name",
        "user__last_name",
        "partner__name",
        "telegram_account__username",
        "telegram_account__telegram_id",
    )
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "partner",
                    "user",
                    "telegram_id",
                    "telegram_username",
                    "title",
                    "is_active",
                )
            },
        ),
        (
            _lazy("Права в счетах"),
            {
                "fields": ("can_adjust_bills",),
                "description": (
                    _lazy("Изменение и аннулирование счетов. Выдаёт только владелец заведения.")
                ),
            },
        ),
        (
            _lazy("Оплата труда"),
            {
                "fields": (
                    "hourly_rate",
                    "commission_rate",
                )
            },
        ),
        (
            _lazy("Уведомления в боте"),
            {
                "fields": (
                    "bot_notifications_enabled",
                    "notify_on_order_created",
                    "notify_on_order_status_changed",
                    "notify_on_guest_calls",
                    "notify_on_billing_requests",
                    (
                        "notify_channel_table",
                        "notify_channel_pickup",
                        "notify_channel_delivery",
                        "notify_channel_shipping",
                    ),
                ),
                "description": (
                    _lazy("Какие рабочие уведомления сотрудник получает в Telegram-боте.")
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
    def _may_touch(request, employee) -> bool:
        # AA-036: only the owner changes the owner's staff profile (it carries
        # the owner's bot access and security alerts).
        return is_partner_owner_or_superuser(request.user) or not is_owner_user(employee.user)

    def get_form(self, request, obj=None, change=False, **kwargs):
        form_class = super().get_form(request, obj, change, **kwargs)

        class RequestBoundEmployeeProfileAdminForm(form_class):
            def __init__(self, *args, **form_kwargs):
                form_kwargs["actor_user"] = request.user
                super().__init__(*args, **form_kwargs)

        return RequestBoundEmployeeProfileAdminForm

    @admin.display(description="Telegram")
    def telegram_binding(self, obj: EmployeeProfile) -> str:
        if obj.telegram_account_id is None:
            return _("Не привязан")
        username = f"@{obj.telegram_account.username}" if obj.telegram_account.username else "-"
        return f"{obj.telegram_account.telegram_id} / {username}"


class StaffInviteForm(forms.ModelForm):
    """Only the role: the link and everything else come from the service."""

    class Meta:
        model = StaffInvite
        fields = ("role",)


@admin.register(StaffInvite)
class StaffInviteAdmin(ScopedAdminMixin):
    """Staff invite links (PQ-046): issued here or in the bot; every change
    goes through apps/employees/invites.py."""

    admin_section = AdminSection.EMPLOYEES
    form = StaffInviteForm
    list_display = ("role_label", "status", "person", "created_by", "created_at", "expires_at")
    list_filter = ("status",)
    actions = ("confirm_invites", "reject_invites", "revoke_invites")
    readonly_fields = (
        "link",
        "role_label",
        "status",
        "person",
        "created_by",
        "expires_at",
        "decided_by",
        "decided_at",
        "employee",
    )

    def has_add_permission(self, request):
        # A link belongs to one venue: issued by its people, not by the platform.
        return super().has_add_permission(request) and not self.is_platform_admin(request)

    def has_delete_permission(self, request, obj=None):
        return False  # the history stays; «Отменить» stops a link

    def get_fields(self, request, obj=None):
        if obj is None:
            return ("role",)
        return self.readonly_fields

    def get_form(self, request, obj=None, change=False, **kwargs):
        form_class = super().get_form(request, obj, change=change, **kwargs)
        if obj is not None:
            return form_class
        roles = roles_invitable_by(request.user, self.get_partner_scope_id(request))

        class RoleForm(form_class):
            def __init__(self, *args, **form_kwargs):
                super().__init__(*args, **form_kwargs)
                self.fields["role"] = forms.ChoiceField(
                    label=_lazy("Роль"),
                    choices=[(role, User.Role(role).label) for role in roles],
                )

        return RoleForm

    def save_model(self, request, obj, form, change):
        if change:
            return
        try:
            invite = create_staff_invite(
                partner_id=self.get_partner_scope_id(request),
                actor=request.user,
                role=form.cleaned_data["role"],
            )
        except StaffInviteError as exc:
            raise PermissionDenied(str(exc)) from exc
        # The admin's "added" message and redirect use this object.
        obj.__dict__.update({k: v for k, v in invite.__dict__.items() if k != "_state"})
        obj._state.adding = False

    @admin.display(description=_lazy("Роль"))
    def role_label(self, obj: StaffInvite) -> str:
        return User.Role(obj.role).label

    @admin.display(description=_lazy("Кто нажал"))
    def person(self, obj: StaffInvite) -> str:
        account = obj.claimed_by
        if account is None:
            return "—"
        name = " ".join(part for part in (account.first_name, account.last_name) if part)
        return f"{name} @{account.username}".strip() if account.username else name or "—"

    @admin.display(description=_lazy("Ссылка для сотрудника"))
    def link(self, obj: StaffInvite) -> str:
        if obj.status != StaffInvite.Status.PENDING:
            return "—"
        bot = bot_for_partner(obj.partner_id)  # own, else the network's (PQ-047)
        if bot is None or not bot.username:
            return _("Сначала добавьте бота в «Боты».")
        return invite_link(obj.code, bot.username)

    def _decide(self, request, queryset, action):
        done = 0
        for invite in queryset:
            try:
                action(invite)
            except StaffInviteError as exc:
                self.message_user(request, str(exc), level=messages.WARNING)
            else:
                done += 1
        if done:
            self.message_user(request, _("Готово: {count}.", count=done))

    @admin.action(description=_lazy("Подтвердить сотрудника"))
    def confirm_invites(self, request, queryset):
        self._decide(request, queryset, lambda invite: decide_staff_invite(
            partner_id=invite.partner_id, invite_id=invite.id, actor=request.user, accept=True
        ))

    @admin.action(description=_lazy("Отклонить"))
    def reject_invites(self, request, queryset):
        self._decide(request, queryset, lambda invite: decide_staff_invite(
            partner_id=invite.partner_id, invite_id=invite.id, actor=request.user, accept=False
        ))

    @admin.action(description=_lazy("Отменить ссылку"))
    def revoke_invites(self, request, queryset):
        self._decide(request, queryset, lambda invite: revoke_staff_invite(
            partner_id=invite.partner_id, invite_id=invite.id, actor=request.user
        ))
