from django.contrib import admin, messages
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.billing.forms import PaymentAdminForm
from apps.billing.models import (
    Bill,
    BillingRequest,
    BillItem,
    BillOrder,
    FiscalReceipt,
    Payment,
)
from apps.billing.services import (
    BillingServiceError,
    get_bill_remaining_amount,
    issue_bill,
    record_payment,
)
from apps.users.constants import AdminSection
from core.admin_mixins import ScopedAdminMixin


class BillOrderInline(admin.TabularInline):
    model = BillOrder
    extra = 0


class BillItemInline(admin.TabularInline):
    model = BillItem
    extra = 0
    readonly_fields = ("line_total",)


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    can_delete = False
    fields = ("method", "status", "amount", "paid_at", "comment", "created_by")
    readonly_fields = ("method", "status", "amount", "paid_at", "comment", "created_by")

    def has_add_permission(self, request, obj=None):
        return False


class FiscalReceiptInline(admin.TabularInline):
    model = FiscalReceipt
    extra = 0


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
    readonly_fields = ("created_at", "updated_at", "processed_at")
    actions = ("mark_processed", "mark_canceled")

    @admin.action(description=_("Mark selected billing requests as processed"))
    def mark_processed(self, request, queryset):
        updated = queryset.exclude(status=BillingRequest.Status.PROCESSED).update(
            status=BillingRequest.Status.PROCESSED,
            processed_at=timezone.now(),
        )
        if updated:
            self.message_user(request, f"Обработано запросов счёта: {updated}.")

    @admin.action(description=_("Mark selected billing requests as canceled"))
    def mark_canceled(self, request, queryset):
        updated = queryset.exclude(status=BillingRequest.Status.CANCELED).update(
            status=BillingRequest.Status.CANCELED,
            processed_at=timezone.now(),
        )
        if updated:
            self.message_user(request, f"Отменено запросов счёта: {updated}.")


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
    search_fields = ("public_id", "partner__name", "table__name", "table__number", "label")
    readonly_fields = (
        "public_id",
        "subtotal_amount",
        "total_amount",
        "paid_amount",
        "remaining_amount",
        "issued_at",
        "closed_at",
        "created_at",
        "updated_at",
    )
    inlines = [BillOrderInline, BillItemInline, PaymentInline, FiscalReceiptInline]
    actions = (
        "mark_as_issued",
        "record_remaining_cash_payment",
        "record_remaining_terminal_payment",
    )

    @admin.display(description="Remaining")
    def remaining_amount(self, obj: Bill):
        return get_bill_remaining_amount(obj)

    @admin.action(description=_("Issue selected bills"))
    def mark_as_issued(self, request, queryset):
        issued_count = 0
        for bill in queryset:
            try:
                issue_bill(bill)
            except BillingServiceError as exc:
                self.message_user(
                    request,
                    f"Счёт #{bill.public_id}: {exc}",
                    level=messages.WARNING,
                )
                continue
            issued_count += 1
        if issued_count:
            self.message_user(request, f"Выдано счетов: {issued_count}.")

    @admin.action(description=_("Record remaining cash payment for selected bills"))
    def record_remaining_cash_payment(self, request, queryset):
        self._record_remaining_payment(request, queryset, Payment.Method.CASH)

    @admin.action(description=_("Record remaining terminal payment for selected bills"))
    def record_remaining_terminal_payment(self, request, queryset):
        self._record_remaining_payment(request, queryset, Payment.Method.TERMINAL)

    def _record_remaining_payment(self, request, queryset, method: str) -> None:
        processed_count = 0
        for bill in queryset:
            try:
                remaining_amount = get_bill_remaining_amount(bill)
                if remaining_amount <= 0:
                    raise BillingServiceError("У счёта нет остатка к оплате.")
                record_payment(
                    bill=bill,
                    amount=remaining_amount,
                    method=method,
                    created_by=request.user,
                    comment=f"Recorded via admin action ({method}).",
                )
            except BillingServiceError as exc:
                self.message_user(
                    request,
                    f"Счёт #{bill.public_id}: {exc}",
                    level=messages.WARNING,
                )
                continue
            processed_count += 1
        if processed_count:
            self.message_user(request, f"Принято оплат по счетам: {processed_count}.")


@admin.register(BillOrder)
class BillOrderAdmin(ScopedAdminMixin):
    admin_section = AdminSection.BILLING
    allow_partner_add = False
    allow_partner_delete = False
    list_display = ("bill", "order", "partner", "created_at")
    list_filter = ("partner",)
    search_fields = ("bill__public_id", "order__public_id", "partner__name")


@admin.register(BillItem)
class BillItemAdmin(ScopedAdminMixin):
    admin_section = AdminSection.BILLING
    allow_partner_add = False
    allow_partner_delete = False
    list_display = ("bill", "item_name", "quantity", "unit_price", "line_total", "partner")
    list_filter = ("partner",)
    search_fields = ("bill__public_id", "item_name", "order__public_id", "partner__name")


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


@admin.register(FiscalReceipt)
class FiscalReceiptAdmin(ScopedAdminMixin):
    admin_section = AdminSection.BILLING
    allow_partner_add = False
    allow_partner_delete = False
    list_display = ("bill", "provider", "status", "external_receipt_id", "partner", "processed_at")
    list_filter = ("partner", "provider", "status")
    search_fields = ("bill__public_id", "external_receipt_id", "fiscal_number", "partner__name")
