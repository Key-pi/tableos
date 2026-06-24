from django.contrib import admin

from apps.bonuses.forms import BonusProgramAdminForm, WalkInSaleAdminForm
from apps.bonuses.models import BonusProgram, BonusTransaction, WalkInSale, WalkInSaleItem
from apps.bonuses.strategies import infer_bonus_logic_code, get_bonus_logic_option
from apps.bonuses.services import register_walk_in_sale_by_customer_code
from apps.users.constants import AdminSection
from core.admin_mixins import ScopedAdminMixin


class WalkInSaleItemInline(admin.TabularInline):
    model = WalkInSaleItem
    extra = 0
    can_delete = False
    fields = ("item_name", "unit_price", "quantity")
    readonly_fields = ("item_name", "unit_price", "quantity")


@admin.register(BonusProgram)
class BonusProgramAdmin(ScopedAdminMixin):
    admin_section = AdminSection.BONUSES
    form = BonusProgramAdminForm
    list_display = (
        "name",
        "partner",
        "trigger_event",
        "bonus_logic_label",
        "percent",
        "fixed_amount",
        "milestone_order_count",
        "is_active",
        "config_preview",
        "updated_at",
    )
    list_filter = ("partner", "trigger_event", "program_type", "is_active")
    search_fields = ("name", "partner__name", "strategy_code")
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "partner",
                    "name",
                    "trigger_event",
                    "bonus_logic",
                    "is_active",
                )
            },
        ),
        (
            "Amounts and limits",
            {
                "fields": (
                    "percent",
                    "fixed_amount",
                    "min_order_total",
                    "milestone_order_count",
                    "max_redeem_share",
                    "expires_in_days",
                )
            },
        ),
        (
            "Advanced strategy config",
            {
                "fields": (
                    "config",
                    "program_type",
                    "strategy_code",
                ),
                "description": (
                    "Built-in logics can usually keep config empty. "
                    "Custom strategies may require JSON config."
                ),
            },
        ),
    )

    @admin.display(description="Bonus logic")
    def bonus_logic_label(self, obj: BonusProgram) -> str:
        logic_code = infer_bonus_logic_code(obj)
        return get_bonus_logic_option(logic_code).label

    @admin.display(description="Config")
    def config_preview(self, obj: BonusProgram) -> str:
        if not obj.config:
            return "—"
        if obj.strategy_code == "tiered_cashback_by_total":
            tiers = obj.config.get("tiers", [])
            parts = [
                f"{tier.get('min_total', '0')}+ => {tier.get('percent', '0')}%"
                for tier in tiers
            ]
            return "; ".join(parts) or "—"
        return str(obj.config)


@admin.register(BonusTransaction)
class BonusTransactionAdmin(ScopedAdminMixin):
    admin_section = AdminSection.BONUSES
    allow_partner_add = False
    allow_partner_delete = False
    list_display = ("guest", "program", "partner", "transaction_type", "amount", "created_at")
    list_filter = ("partner", "transaction_type")
    search_fields = (
        "guest__customer_code",
        "guest__telegram_account__username",
        "partner__name",
        "comment",
    )


@admin.register(WalkInSale)
class WalkInSaleAdmin(ScopedAdminMixin):
    admin_section = AdminSection.BONUSES
    form = WalkInSaleAdminForm
    inlines = (WalkInSaleItemInline,)
    list_display = (
        "loyalty_customer",
        "guest",
        "partner",
        "amount",
        "bonus_awarded_amount",
        "created_by",
        "created_at",
    )
    list_filter = ("partner",)
    search_fields = (
        "customer_code_snapshot",
        "guest__customer_code",
        "guest__telegram_account__username",
        "partner__name",
        "comment",
    )
    readonly_fields = (
        "guest",
        "customer_code_snapshot",
        "bonus_awarded_amount",
        "created_by",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "partner",
                    "customer_code_input",
                    "amount",
                    "comment",
                )
            },
        ),
        (
            "Resolved loyalty data",
            {
                "fields": (
                    "guest",
                    "customer_code_snapshot",
                    "bonus_awarded_amount",
                    "created_by",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    def get_form(self, request, obj=None, change=False, **kwargs):
        form_class = super().get_form(request, obj, change, **kwargs)

        class RequestBoundForm(form_class):
            def __init__(self, *args, **inner_kwargs):
                inner_kwargs["request"] = request
                super().__init__(*args, **inner_kwargs)

        return RequestBoundForm

    @admin.display(description="Customer code")
    def loyalty_customer(self, obj: WalkInSale) -> str:
        return obj.customer_code_snapshot or "anonymous"

    def has_change_permission(self, request, obj=None):
        if obj is not None:
            return False
        return super().has_change_permission(request, obj)

    def save_model(self, request, obj, form, change):
        if change:
            super().save_model(request, obj, form, change)
            return

        partner = form.cleaned_data["partner"]
        obj.partner = partner
        sale = register_walk_in_sale_by_customer_code(
            partner_id=partner.id,
            customer_code=form.cleaned_data.get("normalized_customer_code", ""),
            amount=obj.amount,
            comment=obj.comment,
            created_by=request.user,
        )
        obj.pk = sale.pk
        obj.partner = sale.partner
        obj.guest = sale.guest
        obj.customer_code_snapshot = sale.customer_code_snapshot
        obj.bonus_awarded_amount = sale.bonus_awarded_amount
        obj.created_by = sale.created_by
