from datetime import datetime
from datetime import timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase

from apps.bonuses.models import BonusProgram, BonusTransaction
from apps.bonuses.strategies import BonusContext, _visit_bonus
from apps.partners.models import Partner
from apps.users.models import (
    GuestProfile,
    TelegramAccount,
)


class VisitBonusDayBoundaryTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(
            name="Night Venue",
            slug="night-venue",
            status=Partner.Status.ACTIVE,
            timezone="Europe/Kyiv",
        )
        account = TelegramAccount.objects.create(telegram_id=12345901, username="night_guest")
        self.guest = GuestProfile.objects.create(partner=self.partner, telegram_account=account)
        self.program = BonusProgram.objects.create(
            partner=self.partner,
            name="Visit bonus",
            trigger_event=BonusProgram.TriggerEvent.VISIT,
            program_type=BonusProgram.ProgramType.VISIT,
            fixed_amount="10.00",
            is_active=True,
        )

    def test_once_per_day_uses_the_venue_local_day(self):
        # AA-029: 00:30 and 10:00 in Kyiv are the same venue day, although the
        # first one is still the previous day in UTC.
        kyiv = ZoneInfo("Europe/Kyiv")
        accrual = BonusTransaction.objects.create(
            partner=self.partner,
            guest=self.guest,
            program=self.program,
            transaction_type=BonusTransaction.TransactionType.ACCRUAL,
            amount="10.00",
        )
        BonusTransaction.objects.filter(id=accrual.id).update(
            created_at=datetime(2026, 10, 23, 0, 30, tzinfo=kyiv)
        )
        context = BonusContext(event=BonusProgram.TriggerEvent.VISIT, guest=self.guest)

        with patch(
            "apps.bonuses.strategies.timezone.now",
            # timezone.now() is always UTC: 07:00 UTC == 10:00 in Kyiv.
            return_value=datetime(2026, 10, 23, 7, 0, tzinfo=dt_timezone.utc),
        ):
            amount = _visit_bonus(self.program, context)

        self.assertEqual(amount, Decimal("0.00"))
