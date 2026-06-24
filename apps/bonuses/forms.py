from django import forms

from apps.bonuses.models import BonusProgram
from apps.bonuses.strategies import (
    BonusServiceError,
    get_bonus_logic_option,
    infer_bonus_logic_code,
    list_bonus_logic_choices,
    validate_bonus_logic_config,
)
from apps.bonuses.models import WalkInSale
from apps.users.models import GuestProfile
from apps.users.selectors import get_guest_profile_by_customer_code


class BonusProgramAdminForm(forms.ModelForm):
    bonus_logic = forms.ChoiceField(
        label="Bonus logic",
        choices=(),
        help_text=(
            "Choose one of the built-in loyalty mechanics or a custom strategy that "
            "has already been registered in code."
        ),
    )

    class Meta:
        model = BonusProgram
        fields = (
            "partner",
            "name",
            "trigger_event",
            "bonus_logic",
            "program_type",
            "strategy_code",
            "percent",
            "fixed_amount",
            "min_order_total",
            "milestone_order_count",
            "max_redeem_share",
            "expires_in_days",
            "config",
            "is_active",
        )
        widgets = {
            "program_type": forms.HiddenInput(),
            "strategy_code": forms.HiddenInput(),
            "config": forms.Textarea(attrs={"rows": 6}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["bonus_logic"].choices = list_bonus_logic_choices()
        if self.instance.pk:
            self.fields["bonus_logic"].initial = infer_bonus_logic_code(self.instance)
        else:
            self.fields["bonus_logic"].initial = "cashback_percent"
            default_option = get_bonus_logic_option("cashback_percent")
            self.initial.setdefault("percent", default_option.default_percent)
            self.initial.setdefault("max_redeem_share", default_option.default_max_redeem_share)
            self.initial.setdefault("expires_in_days", default_option.default_expires_in_days)

        self.fields["config"].required = False
        self.fields["strategy_code"].required = False
        self.fields["program_type"].required = False

    def clean(self):
        cleaned_data = super().clean()
        logic_code = cleaned_data.get("bonus_logic")
        if not logic_code:
            return cleaned_data

        option = get_bonus_logic_option(logic_code)
        trigger_event = cleaned_data.get("trigger_event")
        if trigger_event and option.allowed_trigger_events:
            if trigger_event not in option.allowed_trigger_events:
                allowed = ", ".join(option.allowed_trigger_events)
                self.add_error(
                    "trigger_event",
                    f"This bonus logic supports only these triggers: {allowed}.",
                )

        cleaned_data["program_type"] = option.program_type
        cleaned_data["strategy_code"] = option.strategy_code

        if option.default_percent > 0 and not cleaned_data.get("percent"):
            cleaned_data["percent"] = option.default_percent
        if option.default_fixed_amount > 0 and not cleaned_data.get("fixed_amount"):
            cleaned_data["fixed_amount"] = option.default_fixed_amount
        if option.default_milestone_order_count > 0 and not cleaned_data.get(
            "milestone_order_count"
        ):
            cleaned_data["milestone_order_count"] = option.default_milestone_order_count
        if not cleaned_data.get("max_redeem_share"):
            cleaned_data["max_redeem_share"] = option.default_max_redeem_share
        if not cleaned_data.get("expires_in_days"):
            cleaned_data["expires_in_days"] = option.default_expires_in_days

        config = cleaned_data.get("config") or {}
        if not config and option.default_config is not None:
            config = option.default_config.copy()
        try:
            cleaned_data["config"] = validate_bonus_logic_config(logic_code, config)
        except BonusServiceError as exc:
            self.add_error("config", str(exc))

        if logic_code in {"cashback_percent", "tiered_cashback_by_total"}:
            percent = cleaned_data.get("percent")
            if logic_code == "cashback_percent" and (percent is None or percent <= 0):
                self.add_error("percent", "Cashback percent must be greater than zero.")
        if logic_code in {"visit_fixed_bonus", "milestone_every_n_orders"}:
            fixed_amount = cleaned_data.get("fixed_amount")
            if fixed_amount is None or fixed_amount <= 0:
                self.add_error("fixed_amount", "Fixed bonus amount must be greater than zero.")
        if logic_code == "milestone_every_n_orders":
            order_count = cleaned_data.get("milestone_order_count")
            if order_count is None or order_count <= 0:
                self.add_error(
                    "milestone_order_count",
                    "Milestone bonus requires a positive N value.",
                )

        return cleaned_data

    def save(self, commit: bool = True) -> BonusProgram:
        instance = super().save(commit=False)
        instance.program_type = self.cleaned_data["program_type"]
        instance.strategy_code = self.cleaned_data["strategy_code"]
        instance.config = self.cleaned_data.get("config") or {}
        if commit:
            instance.save()
            self.save_m2m()
        return instance


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
