from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError

from apps.bonuses.services import apply_manual_purchase_bonus
from apps.partners.models import Partner


class Command(BaseCommand):
    help = "Apply partner bonus programs for a walk-in or cashier purchase by customer code."

    def add_arguments(self, parser):
        parser.add_argument("--partner-slug", required=True)
        parser.add_argument("--customer-code", required=True)
        parser.add_argument("--amount", required=True)
        parser.add_argument("--comment", default="")

    def handle(self, *args, **options):
        try:
            amount = Decimal(options["amount"])
        except InvalidOperation as exc:
            raise CommandError("`--amount` must be a valid decimal number.") from exc

        partner = Partner.objects.filter(slug=options["partner_slug"].strip()).first()
        if partner is None:
            raise CommandError("Partner with this slug was not found.")

        transactions = apply_manual_purchase_bonus(
            partner_id=partner.id,
            customer_code=options["customer_code"].strip(),
            purchase_total=amount,
            comment=options["comment"].strip(),
        )
        total_awarded = sum((transaction.amount for transaction in transactions), Decimal("0.00"))
        self.stdout.write(
            self.style.SUCCESS(
                f"Applied {len(transactions)} bonus transaction(s) for "
                f"{options['customer_code'].strip().upper()} totaling {total_awarded}."
            )
        )
