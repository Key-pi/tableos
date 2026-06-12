from __future__ import annotations

from django import forms

from apps.notifications.models import BroadcastCampaign
from apps.notifications.services import (
    NotificationDeliveryError,
    ensure_broadcast_campaign_can_send,
)


class BroadcastCampaignAdminForm(forms.ModelForm):
    send_now = forms.BooleanField(
        required=False,
        label="Отправить сразу после сохранения",
        help_text=(
            "Если включено, кампания сразу будет поставлена в очередь после сохранения. "
            "Используйте для ручной отправки партнёром из админки."
        ),
    )

    class Meta:
        model = BroadcastCampaign
        fields = (
            "partner",
            "name",
            "message",
            "scheduled_at",
            "send_now",
        )

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop("request", None)
        super().__init__(*args, **kwargs)
        self.fields["partner"].required = False

    def clean_name(self) -> str:
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError("Название кампании не может быть пустым.")
        return name

    def clean_message(self) -> str:
        message = self.cleaned_data["message"].strip()
        if not message:
            raise forms.ValidationError("Текст рассылки не может быть пустым.")
        return message

    def clean(self):
        cleaned_data = super().clean()
        partner = (
            cleaned_data.get("partner")
            or getattr(self.instance, "partner", None)
            or getattr(getattr(self.request, "user", None), "partner", None)
        )
        send_now = cleaned_data.get("send_now", False)

        if partner is None:
            raise forms.ValidationError("Выберите заведение перед сохранением кампании.")

        cleaned_data["partner"] = partner

        if send_now:
            try:
                ensure_broadcast_campaign_can_send(partner_id=partner.id)
            except NotificationDeliveryError as exc:
                self.add_error("send_now", str(exc))
                raise forms.ValidationError("Кампания пока не готова к отправке.") from exc

        return cleaned_data
