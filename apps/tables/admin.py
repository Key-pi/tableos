from django.contrib import admin, messages
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.utils.http import urlencode

from apps.billing.services import (
    BillingServiceError,
    create_personal_bills_for_table,
    create_shared_bill_for_table,
)
from apps.orders.models import Order
from apps.tables.models import Table, TableSession
from apps.tables.services import TableSessionError, close_table_session_if_settled
from apps.users.constants import AdminSection
from core.admin_mixins import ScopedAdminMixin


@admin.register(Table)
class TableAdmin(ScopedAdminMixin):
    admin_section = AdminSection.TABLES
    list_display = (
        "number",
        "partner",
        "seats",
        "is_active",
        "qr_source_url_short",
        "updated_at",
    )
    list_filter = ("partner", "is_active")
    search_fields = ("name", "partner__name", "qr_token")
    readonly_fields = (
        "qr_token",
        "deep_link_payload",
        "qr_source_status",
        "telegram_start_url_primary",
        "telegram_start_links",
        "telegram_start_urls_text",
        "operations_links",
        "active_sessions_summary",
        "orders_summary",
        "bills_summary",
    )
    actions = ("create_shared_bill_action", "create_personal_bills_action")
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "partner",
                    "number",
                    "name",
                    "seats",
                    "is_active",
                )
            },
        ),
        (
            "QR / Telegram",
            {
                "fields": (
                    "qr_token",
                    "deep_link_payload",
                    "qr_source_status",
                    "telegram_start_url_primary",
                    "telegram_start_urls_text",
                    "telegram_start_links",
                )
            },
        ),
        (
            "Table Operations",
            {
                "fields": (
                    "operations_links",
                    "active_sessions_summary",
                    "orders_summary",
                    "bills_summary",
                )
            },
        ),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("partner__bot_instances")

    def _bot_usernames(self, obj: Table) -> list[str]:
        active_usernames = [
            instance.username
            for instance in obj.partner.bot_instances.all()
            if instance.is_active and instance.username
        ]
        if active_usernames:
            return active_usernames

        # Fallback to any configured username so admin still shows the final
        # URL shape even if the bot was temporarily marked inactive.
        return [
            instance.username
            for instance in obj.partner.bot_instances.all()
            if instance.username
        ]

    def _telegram_start_urls(self, obj: Table) -> list[str]:
        return [obj.telegram_start_url(username) for username in self._bot_usernames(obj)]

    def _orders_changelist_url(self, obj: Table) -> str:
        return "{}?{}".format(
            reverse("admin:orders_order_changelist"),
            urlencode({"table__id__exact": str(obj.id)}),
        )

    def _bills_changelist_url(self, obj: Table) -> str:
        return "{}?{}".format(
            reverse("admin:billing_bill_changelist"),
            urlencode({"table__id__exact": str(obj.id)}),
        )

    @admin.display(description="Operational links")
    def operations_links(self, obj: Table) -> str:
        return format_html(
            '<a href="{}">Открыть заказы стола</a><br><a href="{}">Открыть счета стола</a>',
            self._orders_changelist_url(obj),
            self._bills_changelist_url(obj),
        )

    @admin.display(description="Active sessions")
    def active_sessions_summary(self, obj: Table) -> str:
        sessions = list(
            obj.sessions.select_related("guest__telegram_account")
            .filter(status=TableSession.Status.ACTIVE)
            .order_by("-started_at")[:10]
        )
        if not sessions:
            return "Нет активных сессий."
        return format_html_join(
            "<br>",
            "Гость: {} • код {} • с {}",
            (
                (
                    session.guest.telegram_account.username
                    or session.guest.telegram_account.telegram_id,
                    session.guest.customer_code,
                    session.started_at.strftime("%d.%m %H:%M"),
                )
                for session in sessions
            ),
        )

    @admin.display(description="Orders on table")
    def orders_summary(self, obj: Table) -> str:
        orders = list(
            obj.orders.select_related("guest__telegram_account")
            .exclude(status=Order.Status.CANCELED)
            .order_by("-created_at")[:15]
        )
        if not orders:
            return "Заказов пока нет."
        return format_html_join(
            "<br>",
            "#{} • {} • {} грн • {}",
            (
                (
                    order.public_id,
                    order.guest.telegram_account.username or order.guest.customer_code,
                    order.total_amount,
                    order.get_status_display(),
                )
                for order in orders
            ),
        )

    @admin.display(description="Bills on table")
    def bills_summary(self, obj: Table) -> str:
        bills = list(
            obj.bills.select_related("primary_guest__telegram_account")
            .order_by("-created_at")[:15]
        )
        if not bills:
            return "Счетов пока нет."
        return format_html_join(
            "<br>",
            "#{} • {} • {} грн • оплачено {} грн",
            (
                (
                    bill.public_id,
                    bill.get_status_display(),
                    bill.total_amount,
                    bill.paid_amount,
                )
                for bill in bills
            ),
        )

    @admin.display(description="QR source status")
    def qr_source_status(self, obj: Table) -> str:
        usernames = self._bot_usernames(obj)
        if usernames:
            return f"Using BotInstance username: @{usernames[0]}"
        return (
            "Bot username is missing for this partner. "
            "Open Admin -> Bot instances and fill the `username` field."
        )

    @admin.display(description="QR source URL")
    def qr_source_url_short(self, obj: Table) -> str:
        urls = self._telegram_start_urls(obj)
        if urls:
            return urls[0]
        return f"https://t.me/<bot_username>?start={obj.deep_link_payload}"

    @admin.display(description="Primary Telegram start URL")
    def telegram_start_url_primary(self, obj: Table) -> str:
        urls = self._telegram_start_urls(obj)
        value = urls[0] if urls else f"https://t.me/<bot_username>?start={obj.deep_link_payload}"
        return format_html(
            "<textarea readonly rows='3' style='width: 100%;'>{}</textarea>",
            value,
        )

    @admin.display(description="Telegram start links")
    def telegram_start_links(self, obj: Table) -> str:
        urls = self._telegram_start_urls(obj)
        if not urls:
            return "Add a BotInstance username to generate Telegram deep links."

        return format_html_join(
            "<br>",
            '<a href="{}" target="_blank" rel="noreferrer">{}</a>',
            ((url, url) for url in urls),
        )

    @admin.display(description="Telegram start URL (copy)")
    def telegram_start_urls_text(self, obj: Table) -> str:
        urls = self._telegram_start_urls(obj)
        if not urls:
            urls = [f"https://t.me/<bot_username>?start={obj.deep_link_payload}"]

        text_value = "\n".join(urls)
        rows = max(3, len(urls) + 1)
        # Keep a raw textarea in admin so managers can copy the exact QR source
        # string without opening the link or inspecting HTML elements.
        return format_html(
            "<textarea readonly rows='{}' style='width: 100%;'>{}</textarea>",
            rows,
            text_value,
        )

    @admin.action(description="Create shared bill from selected tables")
    def create_shared_bill_action(self, request, queryset):
        created_count = 0
        for table in queryset:
            try:
                bill = create_shared_bill_for_table(
                    partner_id=table.partner_id,
                    table_id=table.id,
                )
            except BillingServiceError as exc:
                self.message_user(
                    request,
                    f"Стол #{table.number}: {exc}",
                    level=messages.WARNING,
                )
                continue
            created_count += 1
            self.message_user(
                request,
                f"Стол #{table.number}: создан общий счёт #{bill.public_id}.",
                level=messages.INFO,
            )
        if created_count:
            self.message_user(
                request,
                f"Создано общих счетов: {created_count}.",
                level=messages.SUCCESS,
            )

    @admin.action(description="Create personal split bills by guest")
    def create_personal_bills_action(self, request, queryset):
        created_count = 0
        for table in queryset:
            try:
                bills = create_personal_bills_for_table(
                    partner_id=table.partner_id,
                    table_id=table.id,
                )
            except BillingServiceError as exc:
                self.message_user(
                    request,
                    f"Стол #{table.number}: {exc}",
                    level=messages.WARNING,
                )
                continue
            created_count += len(bills)
            self.message_user(
                request,
                f"Стол #{table.number}: создано personal split bills: {len(bills)}.",
                level=messages.INFO,
            )
        if created_count:
            self.message_user(
                request,
                f"Всего создано personal bills: {created_count}.",
                level=messages.SUCCESS,
            )


@admin.register(TableSession)
class TableSessionAdmin(ScopedAdminMixin):
    admin_section = AdminSection.TABLES
    allow_partner_add = False
    allow_partner_delete = False
    list_display = ("table", "guest", "partner", "status", "started_at", "closed_at")
    list_filter = ("partner", "status")
    search_fields = ("table__number", "guest__telegram_account__username", "partner__name")
    readonly_fields = (
        "partner",
        "guest",
        "table",
        "started_at",
        "expires_at",
        "closed_at",
        "status",
        "created_at",
        "updated_at",
    )
    actions = ("close_settled_sessions",)

    @admin.action(description="Close selected settled sessions")
    def close_settled_sessions(self, request, queryset):
        closed_count = 0
        for session in queryset:
            was_active = session.status == TableSession.Status.ACTIVE
            try:
                closed_session = close_table_session_if_settled(session)
            except TableSessionError as exc:
                self.message_user(
                    request,
                    f"Сессия стола #{session.table.number}: {exc}",
                    level=messages.WARNING,
                )
                continue
            if was_active and closed_session.status == TableSession.Status.CLOSED:
                closed_count += 1
        if closed_count:
            self.message_user(request, f"Закрыто сессий стола: {closed_count}.")
