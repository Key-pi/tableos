from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _

from apps.billing.models import Bill
from apps.billing.services import BillingServiceError, create_bill_from_orders
from apps.orders.models import Cart, CartItem, Order, OrderItem, OrderStatusHistory
from apps.orders.services import OrderFlowError, transition_order_status
from apps.users.constants import AdminSection
from core.admin_mixins import ScopedAdminMixin


class CartItemInline(admin.TabularInline):
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


class OrderItemInline(admin.TabularInline):
    model = OrderItem
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


class OrderStatusHistoryInline(admin.TabularInline):
    model = OrderStatusHistory
    extra = 0
    can_delete = False
    fields = ("created_at", "from_status", "to_status", "changed_by", "note")
    readonly_fields = fields


@admin.register(Order)
class OrderAdmin(ScopedAdminMixin):
    admin_section = AdminSection.ORDERS
    allow_partner_add = False
    allow_partner_delete = False
    list_display = (
        "public_id",
        "partner",
        "table",
        "guest",
        "assigned_employee",
        "status",
        "payment_method",
        "total_amount",
    )
    list_filter = ("partner", "status", "payment_method")
    search_fields = ("public_id", "guest__telegram_account__username", "partner__name")
    readonly_fields = (
        "public_id",
        "assigned_employee",
        "status",
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
    actions = (
        "create_draft_bill",
        "mark_as_accepted",
        "mark_as_preparing",
        "mark_as_ready",
        "mark_as_delivering",
        "mark_as_completed",
        "mark_as_canceled",
    )

    @admin.action(description=_("Create draft bill from selected orders"))
    def create_draft_bill(self, request, queryset):
        order_ids = [str(order.id) for order in queryset]
        if not order_ids:
            self.message_user(request, "Выберите хотя бы один заказ.", level=messages.WARNING)
            return

        partner_ids = set(queryset.values_list("partner_id", flat=True))
        if len(partner_ids) != 1:
            self.message_user(
                request,
                "Выберите заказы только одного заведения.",
                level=messages.WARNING,
            )
            return

        try:
            bill = create_bill_from_orders(
                partner_id=queryset[0].partner_id,
                order_ids=order_ids,
                kind=Bill.Kind.SHARED,
                label="Created from order admin",
            )
        except BillingServiceError as exc:
            self.message_user(request, str(exc), level=messages.WARNING)
            return

        self.message_user(
            request,
            f"Создан draft bill #{bill.public_id} на сумму {bill.total_amount}.",
        )

    @admin.action(description=_("Change selected orders to accepted"))
    def mark_as_accepted(self, request, queryset):
        self._transition_orders(request, queryset, Order.Status.ACCEPTED)

    @admin.action(description=_("Change selected orders to preparing"))
    def mark_as_preparing(self, request, queryset):
        self._transition_orders(request, queryset, Order.Status.PREPARING)

    @admin.action(description=_("Change selected orders to ready"))
    def mark_as_ready(self, request, queryset):
        self._transition_orders(request, queryset, Order.Status.READY)

    @admin.action(description=_("Change selected orders to delivering"))
    def mark_as_delivering(self, request, queryset):
        self._transition_orders(request, queryset, Order.Status.DELIVERING)

    @admin.action(description=_("Change selected orders to completed"))
    def mark_as_completed(self, request, queryset):
        self._transition_orders(request, queryset, Order.Status.COMPLETED)

    @admin.action(description=_("Change selected orders to canceled"))
    def mark_as_canceled(self, request, queryset):
        self._transition_orders(request, queryset, Order.Status.CANCELED)

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
                    f"Заказ #{order.public_id}: {exc}",
                    level=messages.WARNING,
                )
                continue
            success_count += 1

        if success_count:
            self.message_user(
                request,
                f"Обновлено заказов: {success_count}.",
            )


@admin.register(OrderItem)
class OrderItemAdmin(ScopedAdminMixin):
    admin_section = AdminSection.ORDERS
    allow_partner_add = False
    allow_partner_delete = False
    list_display = ("item_name", "partner", "order", "quantity", "unit_price")
    list_filter = ("partner",)
    search_fields = ("item_name", "order__id", "partner__name")
    readonly_fields = (
        "partner",
        "order",
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
