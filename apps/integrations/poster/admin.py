"""Poster pages of Admin (PQ-058, step 1): read-only.

The venue sees its connection («Настройки» → «Poster») and the log of
orders sent to Poster (a tab there); references and the webhook inbox are
the platform's. Connecting, disconnecting and «Обновить меню» come in
steps 2–3 as buttons that call the integration's services. The access token
is on no page.
"""

from django.contrib import admin

from apps.integrations.poster.models import (
    PosterConnection,
    PosterOrderPush,
    PosterRef,
    PosterWebhookEvent,
)
from apps.users.constants import AdminSection
from core.admin_mixins import ScopedAdminMixin


class _ReadOnlyLog:
    """Rows written by the integration only: nobody adds, edits or deletes
    them in Admin."""

    allow_partner_add = False
    allow_partner_delete = False

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        if obj is not None:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PosterConnection)
class PosterConnectionAdmin(_ReadOnlyLog, ScopedAdminMixin):
    admin_section = AdminSection.PARTNER_PROFILE
    single_record = True
    list_display = ("account", "spot_id", "status", "last_synced_at", "partner")
    list_filter = ("partner", "status")
    exclude = ("access_token_encrypted",)
    fields = (
        "partner",
        "status",
        "account",
        "account_number",
        "spot_id",
        "tariff",
        "token_expires_at",
        "connected_by",
        "last_synced_at",
        "last_error",
        "last_error_at",
        "created_at",
        "updated_at",
    )
    readonly_fields = fields


@admin.register(PosterOrderPush)
class PosterOrderPushAdmin(_ReadOnlyLog, ScopedAdminMixin):
    admin_section = AdminSection.ORDERS
    service_table = True
    list_display = ("order", "status", "attempts", "sent_at", "incoming_order_id", "partner")
    list_filter = ("partner", "status")
    search_fields = ("order__public_id",)
    fields = (
        "partner",
        "order",
        "status",
        "attempts",
        "claimed_at",
        "sent_at",
        "incoming_order_id",
        "transaction_id",
        "poster_sum",
        "poster_payed_sum",
        "last_error",
        "created_at",
        "updated_at",
    )
    readonly_fields = fields


@admin.register(PosterRef)
class PosterRefAdmin(_ReadOnlyLog, ScopedAdminMixin):
    platform_only = True
    list_display = ("kind", "poster_id", "object_id", "removed_in_poster", "partner")
    list_filter = ("partner", "kind", "removed_in_poster")
    search_fields = ("poster_id",)
    fields = ("partner", "kind", "poster_id", "object_id", "removed_in_poster",
              "created_at", "updated_at")
    readonly_fields = fields


@admin.register(PosterWebhookEvent)
class PosterWebhookEventAdmin(_ReadOnlyLog, ScopedAdminMixin):
    platform_only = True
    list_display = ("created_at", "account", "entity", "action", "entity_id", "status", "partner")
    list_filter = ("status", "entity", "action")
    search_fields = ("account", "entity_id")
    fields = ("partner", "account", "entity", "entity_id", "action", "event_time", "status",
              "processed_at", "error", "payload", "dedupe_key", "created_at", "updated_at")
    readonly_fields = fields
