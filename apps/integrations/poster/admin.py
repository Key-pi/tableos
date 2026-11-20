"""Poster pages of Admin (PQ-058).

The venue sees «Настройки» → «Poster»: its connection and the log of orders
sent to Poster (a tab there); references and the webhook inbox are the
platform's. Nothing is edited in a form: «Подключить Poster», the choice of
the spot and «Отключить Poster» are POST buttons that call
``apps/integrations/poster/services.py`` (step 2); «Обновить меню» comes in
step 3. The access token is on no page.
"""

from django.contrib import admin, messages
from django.http import HttpResponseNotAllowed, HttpResponseRedirect
from django.urls import path

from apps.integrations.poster.models import (
    PosterConnection,
    PosterOrderPush,
    PosterRef,
    PosterWebhookEvent,
)
from apps.integrations.poster.services import (
    PosterConnectError,
    choose_spot,
    disconnect,
    may_manage_poster,
    poster_configured,
    start_connection,
)
from apps.users.constants import AdminSection
from core.admin_mixins import ObjectAction, ObjectActionsMixin, ScopedAdminMixin
from core.i18n import _


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
class PosterConnectionAdmin(ObjectActionsMixin, ScopedAdminMixin):
    admin_section = AdminSection.PARTNER_PROFILE
    single_record = True
    allow_partner_add = False
    allow_partner_delete = False
    object_action_errors = (PosterConnectError,)
    change_form_outer_before_template = "admin/poster/connection_actions.html"
    list_before_template = "admin/poster/connect.html"
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
        "available_spots",
        "token_expires_at",
        "connected_by",
        "last_synced_at",
        "last_error",
        "last_error_at",
        "created_at",
        "updated_at",
    )
    readonly_fields = fields

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        """The form shows the record only; it never writes it."""

    # --- «Подключить Poster» ------------------------------------------------

    def get_urls(self):
        opts = self.model._meta
        return [
            path(
                "connect/",
                self.admin_site.admin_view(self.connect_view),
                name=f"{opts.app_label}_{opts.model_name}_connect",
            ),
            *super().get_urls(),
        ]

    def connect_view(self, request):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        back = HttpResponseRedirect(self._admin_url("changelist"))
        partner_id = self.get_partner_scope_id(request)
        if partner_id is None or not self.has_view_permission(request):
            messages.warning(request, _("Подключить Poster можно из Admin заведения."))
            return back
        try:
            address = start_connection(
                session=request.session, user=request.user, partner_id=partner_id,
            )
        except PosterConnectError as exc:
            messages.warning(request, str(exc))
            return back
        return HttpResponseRedirect(address)

    def _connect_context(self, request, connection=None) -> dict:
        partner_id = self.get_partner_scope_id(request)
        if partner_id is None or not may_manage_poster(request.user, partner_id):
            return {}
        if not poster_configured():
            return {"poster_not_configured": True}
        working = connection is not None and (
            connection.status != PosterConnection.Status.DISCONNECTED
        )
        return {
            "poster_connect_url": self._admin_url("connect"),
            "poster_connect_label": _("Подключить заново") if working else _("Подключить Poster"),
        }

    def changelist_view(self, request, extra_context=None):
        extra_context = {**(extra_context or {}), **self._connect_context(request)}
        return super().changelist_view(request, extra_context)

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        connection = self.get_object(request, object_id) if object_id else None
        extra_context = {
            **(extra_context or {}),
            **self._connect_context(request, connection),
            "show_save": False,
            "show_save_and_continue": False,
            "show_save_and_add_another": False,
            "show_delete": False,
        }
        return super().changeform_view(request, object_id, form_url, extra_context)

    # --- choosing the spot, disconnecting -------------------------------------

    def get_object_actions(self, request, obj):
        if not may_manage_poster(request.user, obj.partner_id):
            return []
        actions = []
        if obj.status == PosterConnection.Status.PENDING_SPOT and obj.access_token_encrypted:
            actions.extend(
                ObjectAction(
                    code=f"spot:{spot['spot_id']}",
                    label=_("Это точка «{spot}»", spot=spot.get("name") or spot["spot_id"]),
                    icon="storefront",
                    style="primary",
                )
                for spot in obj.available_spots or []
            )
        if obj.status != PosterConnection.Status.DISCONNECTED:
            actions.append(ObjectAction(
                code="disconnect",
                label=_("Отключить Poster"),
                icon="link_off",
                style="danger",
                confirm=_(
                    "Заказы перестанут уходить в Poster, меню в Botera останется. Отключить?"
                ),
            ))
        return actions

    def run_object_action(self, request, obj, code, data):
        if code == "disconnect":
            disconnect(connection_id=obj.pk, user=request.user)
            return _("Poster отключён.")
        spot_id = code.removeprefix("spot:")
        connection = choose_spot(connection_id=obj.pk, user=request.user, spot_id=spot_id)
        spot = next(
            (item.get("name") for item in connection.available_spots
             if item.get("spot_id") == connection.spot_id),
            connection.spot_id,
        )
        return _("Заведение работает с точкой Poster «{spot}».", spot=spot)


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
