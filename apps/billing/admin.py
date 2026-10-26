from django.contrib import admin, messages

from apps.billing.admin_actions import run_refund, run_with_reason
from apps.billing.forms import PaymentAdminForm
from apps.billing.models import (
    Bill,
    BillAdjustment,
    BillingRequest,
    BillItem,
    BillOrder,
    Payment,
    Refund,
    RefundLine,
)
from apps.billing.services import (
    BillingServiceError,
    get_bill_remaining_amount,
    issue_bill,
    record_payment,
    refund_bill_lines,
    remaining_amount_of_loaded_bill,
    remove_bill_lines,
    void_bill,
)
from apps.billing.table_bills import (
    cancel_billing_request,
    mark_billing_request_processed,
)
from apps.users.constants import AdminSection
from core.admin_mixins import ScopedAdminMixin
from core.i18n import _
from core.i18n import gettext_lazy as _lazy


class BillOrderInline(admin.TabularInline):
    model = BillOrder
    extra = 0
    can_delete = False
    readonly_fields = ("bill", "order", "partner", "created_at", "updated_at")

    def has_add_permission(self, request, obj=None):
        return False


class BillItemInline(admin.TabularInline):
    model = BillItem
    extra = 0
    can_delete = False
    readonly_fields = (
        "bill",
        "order",
        "order_item",
        "partner",
        "item_name",
        "unit_price",
        "quantity",
        "line_total",
        "comment",
        "is_active",
        "removed_at",
        "removed_by",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request, obj=None):
        return False


class BillAdjustmentInline(admin.TabularInline):
    model = BillAdjustment
    extra = 0
    can_delete = False
    fields = (
        "created_at",
        "kind",
        "actor",
        "reason_code",
        "reason_text",
        "total_before",
        "total_after",
    )
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class RefundInline(admin.TabularInline):
    """Refunds of the bill (PQ-037); made with the bill line action."""

    model = Refund
    extra = 0
    can_delete = False
    fields = (
        "created_at",
        "actor",
        "reason_code",
        "method",
        "money_amount",
        "bonus_returned",
        "cashback_revoked",
        "cashback_not_revoked",
        "is_full",
    )
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    can_delete = False
    fields = ("method", "status", "amount", "paid_at", "comment", "created_by")
    readonly_fields = ("method", "status", "amount", "paid_at", "comment", "created_by")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(BillingRequest)
class BillingRequestAdmin(ScopedAdminMixin):
    admin_section = AdminSection.BILLING
    allow_partner_add = False
    allow_partner_delete = False
    list_display = (
        "partner",
        "table",
        "guest",
        "request_type",
        "status",
        "bill",
        "created_at",
        "processed_at",
    )
    list_filter = ("partner", "request_type", "status")
    search_fields = (
        "guest__customer_code",
        "guest__telegram_account__username",
        "table__name",
        "table__number",
        "bill__public_id",
        "partner__name",
    )
    readonly_fields = (
        "partner",
        "table",
        "guest",
        "table_session",
        "request_type",
        "status",
        "bill",
        "note",
        "created_at",
        "updated_at",
        "processed_at",
    )
    actions = ("mark_processed", "mark_canceled")

    @admin.action(description=_lazy("Отметить запросы обработанными"))
    def mark_processed(self, request, queryset):
        updated = 0
        for billing_request in queryset:
            was_processed = billing_request.status == BillingRequest.Status.PROCESSED
            try:
                processed_request = mark_billing_request_processed(billing_request)
            except BillingServiceError as exc:
                self.message_user(request, str(exc), level=messages.WARNING)
                continue
            if not was_processed and processed_request.status == BillingRequest.Status.PROCESSED:
                updated += 1
        if updated:
            self.message_user(request, _("Обработано запросов счёта: {updated}.", updated=updated))

    @admin.action(description=_lazy("Отменить запросы"))
    def mark_canceled(self, request, queryset):
        updated = 0
        for billing_request in queryset:
            was_canceled = billing_request.status == BillingRequest.Status.CANCELED
            try:
                canceled_request = cancel_billing_request(billing_request)
            except BillingServiceError as exc:
                self.message_user(request, str(exc), level=messages.WARNING)
                continue
            if not was_canceled and canceled_request.status == BillingRequest.Status.CANCELED:
                updated += 1
        if updated:
            self.message_user(request, _("Отменено запросов счёта: {updated}.", updated=updated))


@admin.register(Bill)
class BillAdmin(ScopedAdminMixin):
    admin_section = AdminSection.BILLING
    list_display = (
        "public_id",
        "partner",
        "table",
        "primary_guest",
        "kind",
        "status",
        "total_amount",
        "paid_amount",
        "remaining_amount",
    )
    list_filter = ("partner", "kind", "status", "source")
    # One query per page, not per row (AA-046): the row labels read the
    # partner of the table and of the guest.
    list_select_related = (
        "partner",
        "table__partner",
        "primary_guest__partner",
        "primary_guest__telegram_account",
    )
    search_fields = ("public_id", "partner__name", "table__name", "table__number", "label")
    readonly_fields = (
        "public_id",
        "table",
        "visit",
        "primary_guest",
        "status",
        "kind",
        "source",
        "discount_amount",
        "bonus_spent_amount",
        "subtotal_amount",
        "total_amount",
        "paid_amount",
        "refunded_amount",
        "remaining_amount",
        "issued_at",
        "closed_at",
        "created_at",
        "updated_at",
    )
    inlines = [
        BillOrderInline,
        BillItemInline,
        PaymentInline,
        RefundInline,
        BillAdjustmentInline,
    ]
    actions = (
        "mark_as_issued",
        "record_remaining_cash_payment",
        "record_remaining_terminal_payment",
        "void_selected_bills",
    )

    @admin.action(description=_lazy("Аннулировать счета (право от владельца)"))
    def void_selected_bills(self, request, queryset):
        return run_with_reason(
            self,
            request,
            queryset,
            action_name="void_selected_bills",
            title=_("Аннулировать счета"),
            description=(
                _(
                    "Счёт аннулируется, его заказы снова можно выставить в счёт. Списанные "
                    "бонусы вернутся гостю. Счёт с денежной оплатой аннулировать нельзя.",
                )
            ),
            submit_label=_("Аннулировано счетов"),
            object_label=lambda bill: _("Счёт #{public_id}", public_id=bill.public_id),
            perform=lambda bill, code, text: void_bill(
                bill=bill,
                actor=request.user,
                reason_code=code,
                reason_text=text,
            ),
        )

    @admin.display(description=_lazy("Осталось оплатить"))
    def remaining_amount(self, obj: Bill):
        if getattr(obj, "_prefetched_objects_cache", {}).keys() >= {"items", "payments"}:
            return remaining_amount_of_loaded_bill(obj)
        return get_bill_remaining_amount(obj)

    def get_changelist(self, request, **kwargs):
        base = super().get_changelist(request, **kwargs)

        class BillChangeList(base):
            # Lines and payments of the whole page in two queries (AA-046).
            def get_queryset(self, request, exclude_parameters=None):
                return (
                    super()
                    .get_queryset(request, exclude_parameters)
                    .prefetch_related("items", "payments")
                )

        return BillChangeList

    @admin.action(description=_lazy("Выставить счета"))
    def mark_as_issued(self, request, queryset):
        issued_count = 0
        for bill in queryset:
            try:
                issue_bill(bill)
            except BillingServiceError as exc:
                self.message_user(
                    request,
                    _("Счёт #{public_id}: {exc}", public_id=bill.public_id, exc=exc),
                    level=messages.WARNING,
                )
                continue
            issued_count += 1
        if issued_count:
            self.message_user(request, _(
                "Выдано счетов: {issued_count}.",
                issued_count=issued_count,
            ))

    @admin.action(description=_lazy("Принять остаток наличными"))
    def record_remaining_cash_payment(self, request, queryset):
        self._record_remaining_payment(request, queryset, Payment.Method.CASH)

    @admin.action(description=_lazy("Принять остаток картой"))
    def record_remaining_terminal_payment(self, request, queryset):
        self._record_remaining_payment(request, queryset, Payment.Method.TERMINAL)

    def _record_remaining_payment(self, request, queryset, method: str) -> None:
        processed_count = 0
        for bill in queryset:
            try:
                remaining_amount = get_bill_remaining_amount(bill)
                if remaining_amount <= 0:
                    raise BillingServiceError(_("У счёта нет остатка к оплате."))
                record_payment(
                    bill=bill,
                    amount=remaining_amount,
                    method=method,
                    created_by=request.user,
                    comment=_("Проведено в Admin ({method}).", method=method),
                )
            except BillingServiceError as exc:
                self.message_user(
                    request,
                    _("Счёт #{public_id}: {exc}", public_id=bill.public_id, exc=exc),
                    level=messages.WARNING,
                )
                continue
            processed_count += 1
        if processed_count:
            self.message_user(request, _(
                "Принято оплат по счетам: {processed_count}.",
                processed_count=processed_count,
            ))


@admin.register(BillOrder)
class BillOrderAdmin(ScopedAdminMixin):
    admin_section = AdminSection.BILLING
    allow_partner_add = False
    allow_partner_delete = False
    list_display = ("bill", "order", "partner", "created_at")
    list_filter = ("partner",)
    search_fields = ("bill__public_id", "order__public_id", "partner__name")
    readonly_fields = ("bill", "order", "partner", "created_at", "updated_at")


@admin.register(BillItem)
class BillItemAdmin(ScopedAdminMixin):
    admin_section = AdminSection.BILLING
    allow_partner_add = False
    allow_partner_delete = False
    list_display = (
        "bill",
        "item_name",
        "quantity",
        "unit_price",
        "line_total",
        "is_active",
        "partner",
    )
    list_filter = ("partner", "is_active")
    actions = ("remove_selected_lines", "refund_selected_lines")

    @admin.action(description=_lazy("Вернуть деньги за строки (право от владельца)"))
    def refund_selected_lines(self, request, queryset):
        return run_refund(
            self,
            request,
            queryset.filter(is_active=True),
            action_name="refund_selected_lines",
            perform=lambda bill, items, code, method, text: refund_bill_lines(
                bill=bill,
                bill_item_ids=[item.id for item in items],
                actor=request.user,
                reason_code=code,
                method=method,
                reason_text=text,
            ),
        )

    @admin.action(description=_lazy("Убрать строки из счетов (право от владельца)"))
    def remove_selected_lines(self, request, queryset):
        return run_with_reason(
            self,
            request,
            queryset.filter(is_active=True).select_related("bill"),
            action_name="remove_selected_lines",
            title=_("Убрать позиции из счёта"),
            description=(
                _(
                    "Позиции остаются в истории счёта, сумма пересчитывается. Нельзя уменьшить "
                    "счёт ниже уже оплаченной суммы и менять счёт с применёнными бонусами.",
                )
            ),
            submit_label=_("Убрано позиций"),
            object_label=lambda item: f"#{item.bill.public_id}: {item.item_name} x{item.quantity}",
            perform=lambda item, code, text: remove_bill_lines(
                bill=item.bill,
                order_item_ids=[item.order_item_id],
                actor=request.user,
                reason_code=code,
                reason_text=text,
            ),
        )
    search_fields = ("bill__public_id", "item_name", "order__public_id", "partner__name")
    readonly_fields = (
        "bill",
        "order",
        "order_item",
        "partner",
        "item_name",
        "unit_price",
        "quantity",
        "line_total",
        "comment",
        "is_active",
        "removed_at",
        "removed_by",
        "created_at",
        "updated_at",
    )


@admin.register(BillAdjustment)
class BillAdjustmentAdmin(ScopedAdminMixin):
    admin_section = AdminSection.BILLING
    allow_partner_add = False
    allow_partner_delete = False
    list_display = (
        "bill",
        "kind",
        "reason_code",
        "actor",
        "total_before",
        "total_after",
        "created_at",
    )
    list_filter = ("partner", "kind", "reason_code")
    search_fields = ("bill__public_id", "reason_text", "actor__username")
    readonly_fields = (
        "partner",
        "bill",
        "kind",
        "actor",
        "reason_code",
        "reason_text",
        "total_before",
        "total_after",
        "payload",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Payment)
class PaymentAdmin(ScopedAdminMixin):
    admin_section = AdminSection.BILLING
    form = PaymentAdminForm
    allow_partner_delete = False
    list_display = ("bill", "method", "status", "amount", "partner", "paid_at")
    list_filter = ("partner", "method", "status")
    search_fields = ("bill__public_id", "external_payment_id", "provider_code", "partner__name")
    readonly_fields = ("status", "paid_at", "created_by", "created_at", "updated_at")

    def has_change_permission(self, request, obj=None):
        if obj is not None:
            return False
        return super().has_change_permission(request, obj)

    def save_model(self, request, obj, form, change):
        if change:
            super().save_model(request, obj, form, change)
            return

        payment = record_payment(
            bill=form.cleaned_data["bill"],
            amount=form.cleaned_data["amount"],
            method=form.cleaned_data["method"],
            comment=form.cleaned_data.get("comment", ""),
            provider_code=form.cleaned_data.get("provider_code", ""),
            external_payment_id=form.cleaned_data.get("external_payment_id", ""),
            created_by=request.user,
        )
        obj.pk = payment.pk
        obj.partner = payment.partner
        obj.status = payment.status
        obj.amount = payment.amount
        obj.paid_at = payment.paid_at
        obj.created_by = payment.created_by


class RefundLineInline(admin.TabularInline):
    model = RefundLine
    extra = 0
    can_delete = False
    fields = ("bill_item", "money_amount", "bonus_amount")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Refund)
class RefundAdmin(ScopedAdminMixin):
    """Read-only list of refunds; a refund is made from the bill lines."""

    admin_section = AdminSection.BILLING
    allow_partner_add = False
    allow_partner_delete = False
    list_display = (
        "created_at",
        "bill",
        "money_amount",
        "bonus_returned",
        "cashback_revoked",
        "method",
        "reason_code",
        "actor",
        "is_full",
        "partner",
    )
    list_filter = ("partner", "reason_code", "method", "is_full")
    search_fields = ("bill__public_id", "partner__name", "reason_text")
    inlines = [RefundLineInline]
    readonly_fields = (
        "bill",
        "actor",
        "reason_code",
        "reason_text",
        "method",
        "is_full",
        "money_amount",
        "bonus_returned",
        "cashback_revoked",
        "cashback_not_revoked",
        "created_at",
    )

    def has_change_permission(self, request, obj=None):
        return False
