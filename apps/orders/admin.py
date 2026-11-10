from django.contrib import admin, messages
from django.urls import reverse
from django.utils.html import format_html
from unfold.admin import TabularInline

from apps.billing import staff_actions as staff
from apps.billing.admin_actions import run_with_reason
from apps.billing.models import Bill, BillItem, Payment
from apps.billing.remote_orders import (
    RemoteOrderError,
    cancel_unpaid_remote_order,
    confirm_transfer,
    mark_order_shipped,
    record_payment_on_receipt,
    reject_transfer,
)
from apps.billing.services import BillingServiceError, cancel_order, create_bill_from_orders
from apps.orders.kitchen import issue_screen_link, missing_screen_warnings
from apps.orders.models import (
    Cart,
    CartItem,
    KitchenScreen,
    Order,
    OrderItem,
    OrderStatusHistory,
)
from apps.orders.services import OrderFlowError, transition_order_status
from apps.users.constants import AdminSection
from core.admin_mixins import (
    ObjectAction,
    ObjectActionsMixin,
    RecordsInlineMixin,
    ScopedAdminMixin,
)
from core.i18n import _
from core.i18n import gettext_lazy as _lazy


class CartItemInline(TabularInline):
    model = CartItem
    extra = 0
    can_delete = False
    readonly_fields = (
        "partner",
        "menu_item",
        "item_name",
        "unit_price",
        "quantity",
        "comment",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request, obj=None):
        return False


class OrderItemInline(RecordsInlineMixin, TabularInline):
    admin_section = AdminSection.ORDERS
    # What was chosen (variant, add-ons) is in the line's name and price.
    venue_hidden_fields = ("partner", "kind", "menu_item", "updated_at")
    model = OrderItem
    extra = 0
    can_delete = False
    fields = (
        "item_name",
        "unit_price",
        "quantity",
        "comment",
        "kind",
        "menu_item",
        "partner",
        "created_at",
        "updated_at",
    )
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


class OrderStatusHistoryInline(RecordsInlineMixin, TabularInline):
    admin_section = AdminSection.ORDERS
    model = OrderStatusHistory
    tab = True  # a page tab next to the order (admin step 4, PQ-050)
    extra = 0
    can_delete = False
    fields = ("created_at", "from_status", "to_status", "changed_by", "note")
    readonly_fields = fields


# Buttons of an order in Admin (admin step 3, PQ-050): the same actions as the
# bot's order card and the Mini App (``staff_order_actions``), with icons.
ORDER_ACTION_ICONS = {
    staff.TRANSFER_OK: "check_circle",
    staff.TRANSFER_NO: "money_off",
    staff.RECEIPT_CASH: "payments",
    staff.RECEIPT_CARD: "credit_card",
    staff.RECEIPT_COD: "local_post_office",
    staff.RECEIPT_NO: "cancel",
    staff.SHIP: "local_shipping",
    staff.STATUS + Order.Status.ACCEPTED: "play_arrow",
    staff.STATUS + Order.Status.PREPARING: "skillet",
    staff.STATUS + Order.Status.READY: "done",
    staff.STATUS + Order.Status.DELIVERING: "directions_run",
    staff.STATUS + Order.Status.COMPLETED: "task_alt",
    staff.STATUS + Order.Status.CANCELED: "close",
}
RECEIPT_METHODS = {
    staff.RECEIPT_CASH: Payment.Method.CASH,
    staff.RECEIPT_CARD: Payment.Method.TERMINAL,
    staff.RECEIPT_COD: Payment.Method.COD,
}


def _without_emoji(label: str) -> str:
    """Admin draws icons, so the bot's leading emoji is left out."""

    first, _space, rest = str(label).partition(" ")
    return rest if rest and not any(char.isalnum() for char in first) else str(label)


def order_admin_actions(order: Order, *, row: bool = False) -> list[ObjectAction]:
    """``row``: for a list row, where refusals are not shown (no bill lookup)."""

    result = []
    for code in staff.staff_order_actions(order):
        text = staff.describe_order_action(order, code)
        action = ObjectAction(
            code=code,
            label=_without_emoji(text.label),
            icon=ORDER_ACTION_ICONS.get(code, ""),
            style=text.style or "default",
            confirm=text.confirm,
            field="tracking" if text.needs_tracking else "",
            field_label=_("Номер ТТН") if text.needs_tracking else "",
        )
        if row and action.style == "danger":
            continue
        if code == staff.STATUS + Order.Status.CANCELED and BillItem.objects.filter(
            order=order, is_active=True
        ).exists():
            # A billed order is cancelled with a reason, as a bill change (PQ-028).
            action = ObjectAction(
                code=code, label=action.label, icon=action.icon, style="danger",
                list_action="mark_as_canceled",
            )
        result.append(action)
    # The first step forward stands out.
    for index, action in enumerate(result):
        if action.style != "danger":
            result[index] = ObjectAction(**{**action.__dict__, "style": "primary"})
            break
    return result


@admin.register(Order)
class OrderAdmin(ObjectActionsMixin, ScopedAdminMixin):
    admin_section = AdminSection.ORDERS
    object_action_errors = (OrderFlowError, BillingServiceError, RemoteOrderError)
    list_before_template = "admin/botera/row_actions.html"
    allow_partner_add = False
    allow_partner_delete = False
    list_display = (
        "public_id",
        "partner",
        "table",
        "guest",
        "assigned_employee",
        "channel",
        "status",
        "payment_timing",
        "payment_method",
        "total_amount",
    )
    list_filter = ("partner", "channel", "status", "payment_timing", "payment_method")
    # Row labels read the partner of the table, guest and employee: one query
    # per page, not per row (AA-046).
    list_select_related = (
        "partner",
        "table__partner",
        "guest__partner",
        "guest__telegram_account",
        "assigned_employee__partner",
        "assigned_employee__user",
    )
    search_fields = (
        "public_id",
        "guest__telegram_account__username",
        "partner__name",
        "tracking_number",
    )
    readonly_fields = (
        "public_id",
        # AA-023: billing context, bonus target and session settlement depend
        # on these relations; re-pointing them is not an Admin edit.
        "guest",
        "table",
        "table_session",
        "channel",
        "requested_for",
        "delivery_address",
        "delivery_latitude",
        "delivery_longitude",
        "shipping_carrier",
        "shipping_destination",
        "shipping_cost",
        "tracking_number",
        "assigned_employee",
        "status",
        "payment_timing",
        "payment_method",
        "subtotal_amount",
        "bonus_spent",
        "discount_amount",
        "total_amount",
        "accepted_at",
        "received_at",
        "paid_at",
        "created_at",
        "updated_at",
    )
    inlines = [OrderItemInline, OrderStatusHistoryInline]
    # The page in short rows (admin step 4, PQ-050); an order shows only the
    # part of its own way to order (``get_fieldsets``).
    fieldsets = (
        (None, {"fields": (
            "partner",
            ("public_id", "status"),
            ("channel", "requested_for"),
            ("guest", "assigned_employee"),
            ("table", "table_session"),
            "comment",
        )}),
        (_lazy("Доставка"), {"classes": ["tab"], "fields": (
            "delivery_address",
            ("delivery_latitude", "delivery_longitude"),
        )}),
        (_lazy("Отправка почтой"), {"classes": ["tab"], "fields": (
            ("shipping_carrier", "shipping_cost"),
            "shipping_destination",
            "tracking_number",
        )}),
        (_lazy("Оплата"), {"classes": ["tab"], "fields": (
            ("payment_timing", "payment_method"),
            ("subtotal_amount", "discount_amount"),
            ("bonus_spent", "total_amount"),
            "paid_at",
        )}),
        (_lazy("Время"), {"classes": ["tab"], "fields": (
            ("created_at", "accepted_at"),
            ("received_at", "updated_at"),
        )}),
    )
    FIELD_CHANNELS = {
        **dict.fromkeys(("table", "table_session"), (Order.Channel.TABLE,)),
        "requested_for": (Order.Channel.PICKUP, Order.Channel.DELIVERY),
        **dict.fromkeys(
            ("delivery_address", "delivery_latitude", "delivery_longitude"),
            (Order.Channel.DELIVERY,),
        ),
        **dict.fromkeys(
            ("shipping_carrier", "shipping_cost", "shipping_destination", "tracking_number"),
            (Order.Channel.SHIPPING,),
        ),
    }
    actions = (
        "create_draft_bill",
        "mark_as_accepted",
        "mark_as_preparing",
        "mark_as_ready",
        "mark_as_delivering",
        "mark_as_completed",
        "mark_as_canceled",
    )

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        if obj is None:
            return fieldsets
        other = {
            name for name, channels in self.FIELD_CHANNELS.items() if obj.channel not in channels
        }
        result = []
        for title, options in fieldsets:
            fields = self._filter_fieldset_fields(options.get("fields", ()), other)
            if fields:
                result.append((title, {**options, "fields": fields}))
        return result

    def get_object_actions(self, request, obj):
        return order_admin_actions(obj)

    def run_object_action(self, request, obj, code, data):
        actor = request.user
        if code.startswith(staff.STATUS):
            status = code[len(staff.STATUS):]
            if status == Order.Status.CANCELED:
                cancel_order(order=obj, actor=actor, note="Cancelled in Admin")
            else:
                transition_order_status(
                    order=obj, to_status=status, actor_user=actor, note="Updated in Admin"
                )
        elif code == staff.TRANSFER_OK:
            confirm_transfer(order=obj, actor=actor)
        elif code == staff.TRANSFER_NO:
            reject_transfer(order=obj, actor=actor)
        elif code in RECEIPT_METHODS:
            record_payment_on_receipt(order=obj, actor=actor, method=RECEIPT_METHODS[code])
        elif code == staff.RECEIPT_NO:
            cancel_unpaid_remote_order(order=obj, actor=actor)
        elif code == staff.SHIP:
            mark_order_shipped(order=obj, actor=actor, tracking_number=data.get("tracking", ""))
        obj.refresh_from_db()
        return _(
            "Заказ #{public_id}: {status}.",
            public_id=obj.public_id, status=obj.get_status_display(),
        )

    def get_list_display(self, request):
        list_display = tuple(super().get_list_display(request))
        if not self.is_platform_admin(request):
            # A venue reads what matters first (admin step 4): how it was
            # ordered, where it stands, how much; the table — while tables are on.
            first = ("public_id", "channel", "status", "total_amount", "guest")
            rest = tuple(
                column for column in list_display
                if column not in first
                and (column != "table" or self.module_on(request, "module_tables_enabled"))
            )
            list_display = (*first, *rest)
        if self.has_change_permission(request):
            # Right after the number: on a tablet the end of a wide row is off screen.
            return (*list_display[:1], "next_step", *list_display[1:])
        return list_display

    @admin.display(description=_lazy("Следующий шаг"))
    def next_step(self, obj):
        """The first step forward as a button in the row (one that needs no
        typing and is not a refusal); the rest — on the order's page."""

        actions = order_admin_actions(obj, row=True)
        with_the_guest = obj.status in (Order.Status.DELIVERING, Order.Status.COMPLETED) or (
            obj.status == Order.Status.READY and obj.channel == Order.Channel.PICKUP
        )
        if not with_the_guest:
            # Money on receipt is taken when the guest has the order; before
            # that the next step is the order's own (take it, pack, send).
            actions.sort(key=lambda action: action.code in RECEIPT_METHODS)
        for action in actions:
            if action.field or action.list_action:
                return format_html(
                    '<a class="row-action" href="{}">{}</a>', self._change_url(obj), action.label
                )
            return self.object_action_button(obj, action)
        return ""

    @admin.action(description=_lazy("Создать черновик счёта из заказов"))
    def create_draft_bill(self, request, queryset):
        order_ids = [str(order.id) for order in queryset]
        if not order_ids:
            self.message_user(request, _("Выберите хотя бы один заказ."), level=messages.WARNING)
            return

        partner_ids = set(queryset.values_list("partner_id", flat=True))
        if len(partner_ids) != 1:
            self.message_user(
                request,
                _("Выберите заказы только одного заведения."),
                level=messages.WARNING,
            )
            return

        try:
            bill = create_bill_from_orders(
                partner_id=queryset[0].partner_id,
                order_ids=order_ids,
                kind=Bill.Kind.SHARED,
                label=_("Создан в Admin из заказов"),
            )
        except BillingServiceError as exc:
            self.message_user(request, str(exc), level=messages.WARNING)
            return

        self.message_user(
            request,
            _(
                "Создан draft bill #{public_id} на сумму {total_amount}.",
                public_id=bill.public_id,
                total_amount=bill.total_amount,
            ),
        )

    @admin.action(description=_lazy("Статус: принят"))
    def mark_as_accepted(self, request, queryset):
        self._transition_orders(request, queryset, Order.Status.ACCEPTED)

    @admin.action(description=_lazy("Статус: готовится"))
    def mark_as_preparing(self, request, queryset):
        self._transition_orders(request, queryset, Order.Status.PREPARING)

    @admin.action(description=_lazy("Статус: готов"))
    def mark_as_ready(self, request, queryset):
        self._transition_orders(request, queryset, Order.Status.READY)

    @admin.action(description=_lazy("Статус: в пути"))
    def mark_as_delivering(self, request, queryset):
        self._transition_orders(request, queryset, Order.Status.DELIVERING)

    @admin.action(description=_lazy("Статус: завершён"))
    def mark_as_completed(self, request, queryset):
        self._transition_orders(request, queryset, Order.Status.COMPLETED)

    @admin.action(description=_lazy("Отменить заказы"))
    def mark_as_canceled(self, request, queryset):
        billed = BillItem.objects.filter(order__in=queryset, is_active=True).exists()
        if not billed:
            for order in queryset:
                try:
                    cancel_order(order=order, actor=request.user)
                except (OrderFlowError, BillingServiceError) as exc:
                    self.message_user(
                        request,
                        _("Заказ #{public_id}: {exc}", public_id=order.public_id, exc=exc),
                        level=messages.WARNING,
                    )
            return None
        # Cancelling a billed order is a bill change: it needs a reason (PQ-028).
        return run_with_reason(
            self,
            request,
            queryset,
            action_name="mark_as_canceled",
            title=_("Отменить заказы"),
            description=(
                _(
                    "Часть заказов уже в счёте: их позиции будут убраны из счёта. Нужно право "
                    "изменять счета; заказ в оплаченном счёте отменить нельзя.",
                )
            ),
            submit_label=_("Отменено заказов"),
            object_label=lambda order: _("Заказ #{public_id}", public_id=order.public_id),
            perform=lambda order, code, text: cancel_order(
                order=order,
                actor=request.user,
                reason_code=code,
                reason_text=text,
            ),
        )

    def _transition_orders(self, request, queryset, to_status: str) -> None:
        success_count = 0
        for order in queryset.select_related("partner", "table", "guest"):
            try:
                transition_order_status(
                    order=order,
                    to_status=to_status,
                    actor_user=request.user,
                )
            except OrderFlowError as exc:
                self.message_user(
                    request,
                    _("Заказ #{public_id}: {exc}", public_id=order.public_id, exc=exc),
                    level=messages.WARNING,
                )
                continue
            success_count += 1

        if success_count:
            self.message_user(
                request,
                _("Обновлено заказов: {success_count}.", success_count=success_count),
            )


@admin.register(OrderItem)
class OrderItemAdmin(ScopedAdminMixin):
    admin_section = AdminSection.ORDERS
    service_table = True
    allow_partner_add = False
    allow_partner_delete = False
    list_display = ("item_name", "partner", "order", "quantity", "unit_price")
    list_filter = ("partner",)
    search_fields = ("item_name", "order__id", "partner__name")
    readonly_fields = (
        "partner",
        "order",
        "kind",
        "menu_item",
        "item_name",
        "unit_price",
        "quantity",
        "comment",
        "created_at",
        "updated_at",
    )


@admin.register(Cart)
class CartAdmin(ScopedAdminMixin):
    admin_section = AdminSection.ORDERS
    service_table = True
    allow_partner_add = False
    allow_partner_delete = False
    list_display = (
        "partner",
        "guest",
        "status",
        "subtotal_amount",
        "total_amount",
        "updated_at",
    )
    list_filter = ("partner", "status")
    search_fields = ("guest__telegram_account__username", "partner__name")
    readonly_fields = (
        "partner",
        "guest",
        "table_session",
        "status",
        "subtotal_amount",
        "total_amount",
        "checked_out_at",
        "created_at",
        "updated_at",
    )
    inlines = [CartItemInline]


@admin.register(CartItem)
class CartItemAdmin(ScopedAdminMixin):
    admin_section = AdminSection.ORDERS
    service_table = True
    allow_partner_add = False
    allow_partner_delete = False
    list_display = ("item_name", "partner", "cart", "quantity", "unit_price")
    list_filter = ("partner",)
    search_fields = ("item_name", "partner__name")
    readonly_fields = (
        "partner",
        "cart",
        "menu_item",
        "item_name",
        "unit_price",
        "quantity",
        "comment",
        "created_at",
        "updated_at",
    )


@admin.register(OrderStatusHistory)
class OrderStatusHistoryAdmin(ScopedAdminMixin):
    admin_section = AdminSection.ORDERS
    service_table = True
    allow_partner_add = False
    allow_partner_delete = False
    list_display = ("order", "partner", "from_status", "to_status", "changed_by", "created_at")
    list_filter = ("partner", "from_status", "to_status")
    search_fields = ("order__public_id", "partner__name", "changed_by__username", "note")
    readonly_fields = (
        "order",
        "partner",
        "from_status",
        "to_status",
        "changed_by",
        "note",
        "created_at",
    )


@admin.register(KitchenScreen)
class KitchenScreenAdmin(ScopedAdminMixin):
    """Kitchen and bar screens (PQ-048, stage 3). The link works like a
    password: it is shown once, when the screen is made or a new one is
    issued, and a new link switches the old one off."""

    admin_section = AdminSection.TABLES
    list_display = ("name", "partner", "shows", "is_active", "link_issued_at", "last_seen_at")
    list_filter = ("partner", "shows", "is_active")
    search_fields = ("name", "partner__name")
    fields = (
        "partner", "name", "shows", "language", "is_active",
        "link_help", "link_issued_at", "last_seen_at",
    )
    readonly_fields = ("link_help", "link_issued_at", "last_seen_at")
    actions = ("issue_link_action",)

    @admin.display(description=_lazy("Ссылка"))
    def link_help(self, obj):
        return _(
            "Ссылку видно один раз: после создания экрана или по действию «Выдать новую "
            "ссылку» в списке экранов. Новая ссылка отключает старую — так и поступайте, "
            "если планшет потерялся."
        )

    def _show_link(self, request, screen: KitchenScreen) -> None:
        token = issue_screen_link(screen)
        url = f"{request.build_absolute_uri(reverse('webapp:kitchen'))}#{token}"
        messages.success(
            request,
            format_html(
                _(
                    "Экран «{}»: откройте эту ссылку на планшете (её видно только сейчас, "
                    "прежняя больше не работает): <a href=\"{}\">{}</a>"
                ),
                screen.name, url, url,
            ),
        )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not change:
            self._show_link(request, obj)

    def changelist_view(self, request, extra_context=None):
        if request.method == "GET":
            for warning in missing_screen_warnings(self.get_partner_scope_id(request)):
                messages.warning(request, warning)
        return super().changelist_view(request, extra_context)

    @admin.action(description=_lazy("Выдать новую ссылку"), permissions=["change"])
    def issue_link_action(self, request, queryset):
        for screen in queryset:
            self._show_link(request, screen)
