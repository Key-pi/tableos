from django import forms

from apps.billing.models import Payment


class PaymentAdminForm(forms.ModelForm):
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
        if amount <= 0:
            raise forms.ValidationError("Сумма оплаты должна быть больше нуля.")
        return amount
