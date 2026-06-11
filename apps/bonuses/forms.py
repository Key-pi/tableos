from django import forms

from apps.bonuses.models import WalkInSale
from apps.users.models import GuestProfile
from apps.users.selectors import get_guest_profile_by_customer_code


class WalkInSaleAdminForm(forms.ModelForm):
    customer_code_input = forms.CharField(
        label="Customer code",
        required=True,
        help_text=(
            "Enter the guest loyalty code. Quick sale is registered only for an "
            "identified customer profile."
        ),
    )

    class Meta:
        model = WalkInSale
        fields = (
            "partner",
            "customer_code_input",
            "amount",
            "comment",
        )

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop("request", None)
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.customer_code_snapshot and not self.initial.get(
            "customer_code_input"
        ):
            self.initial["customer_code_input"] = self.instance.customer_code_snapshot

    def clean(self):
        cleaned_data = super().clean()
        partner = (
            cleaned_data.get("partner")
            or getattr(self.instance, "partner", None)
            or getattr(getattr(self.request, "user", None), "partner", None)
        )
        customer_code = (cleaned_data.get("customer_code_input") or "").strip().upper()
        guest = None

        if partner is None:
            raise forms.ValidationError("Choose the venue before registering a walk-in sale.")

        if customer_code:
            try:
                guest = get_guest_profile_by_customer_code(partner.id, customer_code)
            except GuestProfile.DoesNotExist as exc:
                self.add_error(
                    "customer_code_input",
                    "No guest with this customer code was found in the selected venue.",
                )
                raise forms.ValidationError("Invalid customer code.") from exc

        cleaned_data["partner"] = partner
        cleaned_data["resolved_guest"] = guest
        cleaned_data["normalized_customer_code"] = customer_code
        return cleaned_data
