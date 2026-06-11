import secrets
from copy import deepcopy

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models

from apps.users.constants import (
    ADMIN_ACCESS_PRESET_TEMPLATES,
    AdminAccessPreset,
    AdminSection,
)
from core.database.models import PartnerBoundModel, TimeStampedModel


class User(AbstractUser):
    """Back-office or staff user with an optional partner scope and business role."""

    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        MANAGER = "manager", "Manager"
        CASHIER = "cashier", "Cashier"
        WAITER = "waiter", "Waiter"
        HOOKAH_MASTER = "hookah_master", "Hookah master"
        ADMIN = "admin", "Admin"

    partner = models.ForeignKey(
        "partners.Partner",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="staff_users",
    )
    role = models.CharField(max_length=32, choices=Role.choices, default=Role.MANAGER)

    def __str__(self) -> str:
        return self.get_full_name() or self.username


class TelegramAccount(TimeStampedModel):
    """Normalized Telegram identity reused across partners and guest profiles."""

    telegram_id = models.BigIntegerField(unique=True)
    username = models.CharField(max_length=255, blank=True)
    first_name = models.CharField(max_length=255, blank=True)
    last_name = models.CharField(max_length=255, blank=True)
    language_code = models.CharField(max_length=16, blank=True)
    is_blocked = models.BooleanField(default=False)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return self.username or str(self.telegram_id)


class GuestProfile(PartnerBoundModel):
    """Partner-specific customer profile linked to a shared Telegram account."""

    telegram_account = models.ForeignKey(
        TelegramAccount,
        on_delete=models.CASCADE,
        related_name="guest_profiles",
    )
    customer_code = models.CharField(max_length=12, editable=False)
    first_visit_at = models.DateTimeField(null=True, blank=True)
    last_visit_at = models.DateTimeField(null=True, blank=True)
    loyalty_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_subscribed = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["partner", "telegram_account"],
                name="unique_guest_profile_per_partner",
            ),
            models.UniqueConstraint(
                fields=["partner", "customer_code"],
                name="unique_guest_customer_code_per_partner",
            ),
        ]
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        identity = self.telegram_account.username or self.telegram_account.telegram_id
        return f"{self.partner.name} / {identity}"

    def save(self, *args, **kwargs):
        if not self.customer_code:
            self.customer_code = self._generate_customer_code()
        return super().save(*args, **kwargs)

    def _generate_customer_code(self) -> str:
        while True:
            candidate = secrets.token_hex(3).upper()
            if not self.partner_id:
                return candidate
            exists = type(self).objects.filter(
                partner_id=self.partner_id,
                customer_code=candidate,
            ).exists()
            if not exists:
                return candidate


class AdminAccessProfile(TimeStampedModel):
    """Tenant-scoped admin membership that grants access to Django admin for one partner."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="admin_access_profile",
    )
    partner = models.ForeignKey(
        "partners.Partner",
        on_delete=models.CASCADE,
        related_name="admin_access_profiles",
    )
    preset = models.CharField(
        max_length=32,
        choices=AdminAccessPreset.choices,
        default=AdminAccessPreset.CUSTOM,
    )
    is_partner_owner = models.BooleanField(default=False)
    can_access_admin = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["partner__name", "user__username"]

    def __str__(self) -> str:
        return f"{self.partner.name} / {self.user.username} / admin access"

    def save(self, *args, **kwargs):
        if self.is_partner_owner and self.preset == AdminAccessPreset.CUSTOM:
            self.preset = AdminAccessPreset.OWNER
        if self.user.partner_id != self.partner_id:
            self.user.partner = self.partner
            self.user.save(update_fields=["partner"])

        should_be_staff = self.can_access_admin and self.is_active
        if not self.user.is_superuser and self.user.is_staff != should_be_staff:
            self.user.is_staff = should_be_staff
            self.user.save(update_fields=["is_staff"])
        result = super().save(*args, **kwargs)
        self.sync_section_permissions()
        return result

    def has_section_permission(self, section: str, action: str) -> bool:
        if not (self.can_access_admin and self.is_active):
            return False
        if self.is_partner_owner:
            return True

        permission = self.section_permissions.filter(section=section).first()
        if permission is None:
            return False
        return getattr(permission, f"can_{action}", False)

    def sync_section_permissions(self) -> None:
        if self.preset == AdminAccessPreset.CUSTOM:
            return

        template = deepcopy(ADMIN_ACCESS_PRESET_TEMPLATES.get(self.preset, {}))
        existing_permissions = {
            permission.section: permission
            for permission in self.section_permissions.all()
        }
        retained_sections = set(template.keys())

        for section, permission_flags in template.items():
            permission = existing_permissions.get(section)
            if permission is None:
                permission = AdminSectionPermission(profile=self, section=section)
            for field_name, field_value in permission_flags.items():
                setattr(permission, field_name, field_value)
            permission.save()

        self.section_permissions.exclude(section__in=retained_sections).delete()


class AdminSectionPermission(TimeStampedModel):
    """Granular CRUD and sensitive-action permission for one admin section."""

    profile = models.ForeignKey(
        AdminAccessProfile,
        on_delete=models.CASCADE,
        related_name="section_permissions",
    )
    section = models.CharField(max_length=32, choices=AdminSection.choices)
    can_view = models.BooleanField(default=False)
    can_add = models.BooleanField(default=False)
    can_change = models.BooleanField(default=False)
    can_delete = models.BooleanField(default=False)
    can_manage_sensitive = models.BooleanField(default=False)

    class Meta:
        ordering = ["section"]
        constraints = [
            models.UniqueConstraint(
                fields=["profile", "section"],
                name="unique_admin_section_permission_per_profile",
            )
        ]

    def __str__(self) -> str:
        return f"{self.profile.user.username} / {self.section}"
