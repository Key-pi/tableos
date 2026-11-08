"""«Бонусы» in the guest's profile opens their history (regression,
2026-11-08: the history was read in async code and the bot failed with
SynchronousOnlyOperation)."""

from decimal import Decimal

from asgiref.sync import async_to_sync

from apps.bonuses.models import BonusTransaction
from apps.bonuses.services import sync_bonus_balance_from_ledger
from apps.partners.models import Network, Partner
from apps.users.services import get_or_create_guest_profile
from bot.handlers import profile as profile_handlers
from tests.bot.test_guest_flow import GUEST, GuestFlowFixture


class ProfileBonusesTests(GuestFlowFixture):
    def setUp(self):
        super().setUp()
        self.guest = get_or_create_guest_profile(partner_id=self.partner.id, telegram_id=GUEST)
        BonusTransaction.objects.create(
            partner=self.partner, guest=self.guest, transaction_type="manual",
            amount=Decimal("30.00"), source_type="test", source_id="seed",
            idempotency_key="profile-bonuses-seed",
        )
        sync_bonus_balance_from_ledger(self.guest)

    def press(self):
        callback = self._callback("guestprofile:bonuses:0")
        async_to_sync(profile_handlers.profile_bonuses_callback)(callback, self.bot)
        return callback.message.edit_text.call_args.args[0]

    def test_the_history_opens(self):
        self.assertIn("30.00", self.press())

    def test_the_history_opens_with_shared_network_bonuses(self):
        network = Network.objects.create(name="Сеть", bonus_mode=Network.BonusMode.SHARED)
        Partner.objects.filter(id=self.partner.id).update(network=network)
        self.assertIn("30.00", self.press())
