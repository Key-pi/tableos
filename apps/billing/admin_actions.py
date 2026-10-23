"""Admin actions that change bills through the owner services (PQ-028).

Each action first shows a confirmation page with a reason (preset code plus an
optional comment), then calls the billing command; the command checks the
owner-granted permission and records a BillAdjustment.
"""

from django import forms
from django.contrib import messages
from django.template.response import TemplateResponse

from apps.billing.models import BillAdjustment


class AdjustmentReasonForm(forms.Form):
    reason_code = forms.ChoiceField(
        label="Причина",
        choices=BillAdjustment.ReasonCode.choices,
    )
    reason_text = forms.CharField(
        label="Комментарий (необязательно)",
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
