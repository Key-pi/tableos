"""Admin actions that change bills through the owner services (PQ-028).

Each action first shows a confirmation page with a reason (preset code plus an
optional comment), then calls the billing command; the command checks the
owner-granted permission and records a BillAdjustment.
"""

from django import forms
from django.contrib import messages
from django.template.response import TemplateResponse

from apps.billing.models import BillAdjustment, Refund
from core.i18n import _
from core.i18n import gettext_lazy as _lazy


class AdjustmentReasonForm(forms.Form):
    reason_code = forms.ChoiceField(
        label=_lazy("Причина"),
        choices=BillAdjustment.ReasonCode.choices,
    )
    reason_text = forms.CharField(
        label=_lazy("Комментарий (необязательно)"),
        required=False,
        max_length=255,
    )


def run_with_reason(
    modeladmin,
    request,
    queryset,
    *,
    action_name: str,
    title: str,
    description: str,
    submit_label: str,
    object_label,
    perform,
):
    """Show the reason page, or apply ``perform(obj, reason_code, reason_text)``."""

    if request.POST.get("apply"):
        form = AdjustmentReasonForm(request.POST)
        if form.is_valid():
            done = 0
            for obj in queryset:
                try:
                    perform(
                        obj,
                        form.cleaned_data["reason_code"],
                        form.cleaned_data["reason_text"],
                    )
                except Exception as exc:  # noqa: BLE001 - shown to the admin user
                    modeladmin.message_user(
                        request,
                        f"{object_label(obj)}: {exc}",
                        level=messages.WARNING,
                    )
                    continue
                done += 1
            if done:
                modeladmin.message_user(request, f"{submit_label}: {done}.")
            return None
    else:
        form = AdjustmentReasonForm()

    return TemplateResponse(
        request,
        "admin/billing/adjustment_reason.html",
        {
            **modeladmin.admin_site.each_context(request),
            "title": title,
            "description": description,
            "submit_label": submit_label,
            "form": form,
            "object_labels": [object_label(obj) for obj in queryset],
            "selected_ids": [str(obj.pk) for obj in queryset],
            "action_name": action_name,
            "opts": modeladmin.model._meta,
        },
    )


class RefundForm(forms.Form):
    reason_code = forms.ChoiceField(label=_lazy("Причина"), choices=Refund.ReasonCode.choices)
    method = forms.ChoiceField(label=_lazy("Как вернули деньги"), choices=Refund.Method.choices)
    reason_text = forms.CharField(
        label=_lazy("Комментарий (необязательно)"), required=False, max_length=255
    )


def run_refund(modeladmin, request, queryset, *, action_name: str, perform):
    """Refund the selected bill lines, one refund per bill (PQ-037).

    ``perform(bill, items, reason_code, method, reason_text)`` calls the
    billing command, which checks the owner-granted right.
    """

    items = list(queryset.select_related("bill"))
    if request.POST.get("apply"):
        form = RefundForm(request.POST)
        if form.is_valid():
            by_bill: dict = {}
            for item in items:
                by_bill.setdefault(item.bill_id, (item.bill, []))[1].append(item)
            done = 0
            for bill, bill_items in by_bill.values():
                try:
                    refund = perform(
                        bill,
                        bill_items,
                        form.cleaned_data["reason_code"],
                        form.cleaned_data["method"],
                        form.cleaned_data["reason_text"],
                    )
                except Exception as exc:  # noqa: BLE001 - shown to the admin user
                    modeladmin.message_user(
                        request, _(
                            "Счёт #{public_id}: {exc}",
                            public_id=bill.public_id,
                            exc=exc,
                        ), level=messages.WARNING
                    )
                    continue
                done += 1
                modeladmin.message_user(
                    request,
                    _(
                        "Счёт #{public_id}: возвращено {money_amount} грн, бонусов "
                        "{bonus_returned}, снято кешбэка {cashback_revoked}.",
                        public_id=bill.public_id,
                        money_amount=refund.money_amount,
                        bonus_returned=refund.bonus_returned,
                        cashback_revoked=refund.cashback_revoked,
                    ),
                )
            return None
    else:
        form = RefundForm()
    return TemplateResponse(
        request,
        "admin/billing/adjustment_reason.html",
        {
            **modeladmin.admin_site.each_context(request),
            "title": _("Вернуть деньги за строки"),
            "description": (
                _(
                    "Только по оплаченному счёту. Вернётся доля оплаченных денег и списанных "
                    "бонусов за выбранные строки, кешбэк за них снимется. Деньги гостю передайте "
                    "сами — бот сообщит ему сумму. Заказ, у которого вернули все строки, "
                    "отменится (кроме завершённых).",
                )
            ),
            "submit_label": _("Вернуть"),
            "form": form,
            "object_labels": [
                _(
                    "#{public_id}: {item_name} × {quantity} — {line_total} грн",
                    public_id=item.bill.public_id,
                    item_name=item.item_name,
                    quantity=item.quantity,
                    line_total=item.line_total,
                )
                for item in items
            ],
            "selected_ids": [str(item.pk) for item in items],
            "action_name": action_name,
            "opts": modeladmin.model._meta,
        },
    )
