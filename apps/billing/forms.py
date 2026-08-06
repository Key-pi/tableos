from django import forms

from apps.billing.models import Payment


class PaymentAdminForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["method"].choices = [
            choice
            for choice in self.fields["method"].choices
            if choice[0] != Payment.Method.BONUSES
        ]

    class Meta:
        model = Payment
        fields = (
            "bill",
            "method",
            "amount",
            "comment",
            "provider_code",
            "external_payment_id",
        )

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        method = self.cleaned_data.get("method")
        if method == Payment.Method.BONUSES:
            raise forms.ValidationError(
                "Оплату бонусами нужно запускать из карточки счёта, не через Admin."
            )
        if amount <= 0:
            raise forms.ValidationError("Сумма оплаты должна быть больше нуля.")
        return amount
