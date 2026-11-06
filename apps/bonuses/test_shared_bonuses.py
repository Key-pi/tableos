"""Shared bonuses in a network (PQ-047, stage 3, owner's decisions 2026-11-05):
each venue accrues by its own program, the guest spends one balance at any
venue, balances already earned add up, one guest code for the network, the
network report shows what each venue accrued and redeemed."""

import threading
import time
import unittest
from decimal import Decimal
from unittest.mock import patch

from django.contrib import admin as django_admin
from django.db import close_old_connections, connection
from django.test import RequestFactory, TestCase, TransactionTestCase

from apps.analytics.selectors import network_summary
from apps.bonuses import services
from apps.bonuses.models import BonusProgram, BonusTransaction
from apps.bonuses.selectors import recent_bonus_transactions_for_guest
from apps.bonuses.services import (
    BonusServiceError,
    apply_bonus_programs,
    assert_bonus_balance_reconciled,
    bonus_balance,
    register_walk_in_sale,
)
from apps.partners.admin import NetworkAdmin
from apps.partners.models import Network, NetworkWallet, Partner
from apps.users.models import GuestProfile, TelegramAccount, User
from apps.users.services import find_guest_by_customer_code, get_or_create_guest_profile
from bot.handlers.profile import _render_profile_bonuses

PERSON = 9900001
D = Decimal


class SharedBonusFixture:
    def make_network(self):
        self.network = Network.objects.create(name="Ночь")
        self.kyiv = self._venue("Ночь Київ", percent="10")
        self.kharkiv = self._venue("Ночь Харків", percent="5")
        self.at_kyiv = get_or_create_guest_profile(partner_id=self.kyiv.id, telegram_id=PERSON)
        self.at_kharkiv = get_or_create_guest_profile(
            partner_id=self.kharkiv.id, telegram_id=PERSON
        )

    def _venue(self, name, *, percent):
        partner = Partner.objects.create(
            name=name, slug=name.lower().replace(" ", "-"), status=Partner.Status.ACTIVE,
            network=self.network,
        )
        BonusProgram.objects.create(
            partner=partner, name="Кешбэк", program_type=BonusProgram.ProgramType.CASHBACK,
            trigger_event=BonusProgram.TriggerEvent.MANUAL_PURCHASE, percent=D(percent),
            max_redeem_share=D("100"),
        )
        return partner

    def share(self):
        Network.objects.filter(id=self.network.id).update(bonus_mode=Network.BonusMode.SHARED)

    def earn(self, guest, total):
        return apply_bonus_programs(
            partner_id=guest.partner_id, event=BonusProgram.TriggerEvent.MANUAL_PURCHASE,
            guest=guest, purchase_total=D(total),
        )

    def spend(self, guest, total):
        return register_walk_in_sale(
            partner_id=guest.partner_id, guest=guest, amount=D(total), redeem_bonus=True
        )

    def balance(self, guest):
        guest.refresh_from_db()
        return bonus_balance(guest)


class SharedBalanceTests(SharedBonusFixture, TestCase):
    def setUp(self):
        self.make_network()

    def test_separate_balances_stay_apart(self):
        self.earn(self.at_kyiv, "1000")
        self.assertEqual(self.balance(self.at_kyiv), D("100.00"))
        self.assertEqual(self.balance(self.at_kharkiv), D("0.00"))

    def test_shared_balance_adds_up_what_venues_earned_by_their_own_programs(self):
        self.earn(self.at_kyiv, "1000")  # 10% → 100, while separate
        self.share()
        self.earn(self.at_kharkiv, "1000")  # 5% → 50
        self.assertEqual(self.balance(self.at_kyiv), D("150.00"))
        self.assertEqual(self.balance(self.at_kharkiv), D("150.00"))

    def test_bonuses_earned_in_one_venue_are_spent_in_another(self):
        self.share()
        self.earn(self.at_kyiv, "1000")
        sale = self.spend(self.at_kharkiv, "120")
        self.assertEqual(sale.bonus_spent_amount, D("100.00"))
        # Kharkiv accrues 5% of the whole purchase by its program (full total).
        self.assertEqual(self.balance(self.at_kyiv), D("6.00"))
        redemption = BonusTransaction.objects.get(
            transaction_type=BonusTransaction.TransactionType.REDEMPTION
        )
        # The ledger records where it happened.
        self.assertEqual(redemption.partner, self.kharkiv)

    def test_a_tampered_shared_balance_stops_changes(self):
        self.share()
        self.earn(self.at_kyiv, "1000")
        NetworkWallet.objects.update(balance=D("999.00"))
        with self.assertRaises(BonusServiceError):
            assert_bonus_balance_reconciled(self.at_kharkiv)

    def test_a_venue_outside_the_network_does_not_share(self):
        alone = Partner.objects.create(name="Alone", slug="alone", status=Partner.Status.ACTIVE)
        at_alone = get_or_create_guest_profile(partner_id=alone.id, telegram_id=PERSON)
        self.share()
        self.earn(self.at_kyiv, "1000")
        self.assertEqual(self.balance(at_alone), D("0.00"))


class OneCodeTests(SharedBonusFixture, TestCase):
    def setUp(self):
        self.make_network()

    def test_the_code_from_one_venue_finds_the_guest_in_another(self):
        code = self.at_kyiv.customer_code
        with self.assertRaises(GuestProfile.DoesNotExist):
            find_guest_by_customer_code(self.kharkiv.id, code)  # separate: no
        self.share()
        found = find_guest_by_customer_code(self.kharkiv.id, code.lower())
        self.assertEqual(found, self.at_kharkiv)

    def test_a_guest_new_to_a_venue_is_found_by_their_network_code(self):
        self.share()
        third = self._venue("Ночь Одеса", percent="5")
        found = find_guest_by_customer_code(third.id, self.at_kyiv.customer_code)
        self.assertEqual((found.partner, found.telegram_account.telegram_id), (third, PERSON))
        self.assertEqual(found.customer_code, self.at_kyiv.customer_code)

    def test_a_new_profile_in_a_shared_network_gets_the_guests_code(self):
        self.share()
        third = self._venue("Ночь Львів", percent="5")
        profile = get_or_create_guest_profile(partner_id=third.id, telegram_id=PERSON)
        self.assertEqual(profile.customer_code, self.at_kyiv.customer_code)

    def test_a_local_code_wins_over_the_network(self):
        self.share()
        other = GuestProfile.objects.create(
            partner=self.kharkiv,
            telegram_account=TelegramAccount.objects.create(telegram_id=9900002),
            customer_code="ABC123",
        )
        GuestProfile.objects.filter(id=self.at_kyiv.id).update(customer_code="ABC123")
        self.assertEqual(find_guest_by_customer_code(self.kharkiv.id, "ABC123"), other)


class NetworkReportBonusTests(SharedBonusFixture, TestCase):
    def setUp(self):
        self.make_network()

    def test_the_report_shows_what_each_venue_accrued_and_redeemed(self):
        self.share()
        self.earn(self.at_kyiv, "1000")
        self.spend(self.at_kharkiv, "120")
        rows = {
            (row.partner.name if row.partner else "network"): (
                row.bonus_accrued, row.bonus_redeemed
            )
            for row in network_summary([self.kyiv.id, self.kharkiv.id], days=1)
        }
        self.assertEqual(rows["Ночь Київ"], (D("100.00"), D("0.00")))
        self.assertEqual(rows["Ночь Харків"], (D("6.00"), D("100.00")))
        self.assertEqual(rows["network"], (D("106.00"), D("100.00")))


@unittest.skipUnless(connection.vendor == "postgresql", "row locks need PostgreSQL")
class SharedSpendRaceTests(SharedBonusFixture, TransactionTestCase):
    def setUp(self):
        self.make_network()
        self.share()
        self.earn(self.at_kyiv, "1000")  # 100 shared
        # No cashback on the purchases below: exactly 100 can be spent.
        BonusProgram.objects.update(percent=0)

    def test_two_venues_cannot_spend_the_same_balance_at_once(self):
        barrier = threading.Barrier(2)
        spent = []
        real_redeemable = services.get_redeemable_bonus_amount

        def slow_redeemable(**kwargs):
            # Hold the read balance a moment: without the lock both spend it.
            amount = real_redeemable(**kwargs)
            time.sleep(0.3)
            return amount

        errors = []

        def buy(guest):
            try:
                barrier.wait()
                spent.append(self.spend(guest, "100").bonus_spent_amount)
            except Exception as exc:  # noqa: BLE001 - reported below
                errors.append(exc)
            finally:
                close_old_connections()

        threads = [
            threading.Thread(target=buy, args=(guest,))
            for guest in (self.at_kyiv, self.at_kharkiv)
        ]
        # Patched once around both threads (patching inside threads races).
        with patch.object(services, "get_redeemable_bonus_amount", new=slow_redeemable):
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        # Both purchases go through: one spends the 100, the other waits and
        # finds nothing left — not an error for the guest.
        self.assertEqual(errors, [])
        self.assertEqual(sorted(spent), [D("0.00"), D("100.00")])
        self.assertGreaterEqual(self.balance(self.at_kyiv), D("0.00"))


class SettingAndHistoryTests(SharedBonusFixture, TestCase):
    def setUp(self):
        self.make_network()

    def test_the_guest_sees_operations_of_every_venue_with_its_name(self):
        self.share()
        self.earn(self.at_kyiv, "1000")
        history = list(recent_bonus_transactions_for_guest(self.at_kharkiv.id, limit=25))
        self.assertEqual([t.partner for t in history], [self.kyiv])
        text, _pages = _render_profile_bonuses(
            guest_profile=self.at_kharkiv, active_programs=[], recent_transactions=history,
            page=0, balance=self.balance(self.at_kharkiv),
        )
        self.assertIn("📍 Ночь Київ", text)
        self.assertIn("100.00", text)

    def test_the_owner_turns_shared_bonuses_on_but_not_back_off(self):
        owner = User.objects.create_user(username="noch_owner", password="x", partner=self.kyiv)
        request = RequestFactory().get("/")
        request.user = owner
        model_admin = NetworkAdmin(Network, django_admin.site)
        self.assertEqual(model_admin.get_readonly_fields(request, self.network), ())
        self.share()
        self.network.refresh_from_db()
        self.assertEqual(model_admin.get_readonly_fields(request, self.network), ("bonus_mode",))


@unittest.skipUnless(connection.vendor == "postgresql", "unique keys under load need PostgreSQL")
class RepeatedSaleRaceTests(TransactionTestCase):
    """The same quick sale sent twice at the same moment (a retry while the
    first is still saving, Mini App PQ-048): one sale, one spend, one accrual."""

    def test_two_copies_of_one_sale_at_once(self):
        from apps.bonuses.models import WalkInSale
        from apps.bonuses.services import (
            register_walk_in_sale_from_menu_items_by_customer_code,
            sync_bonus_balance_from_ledger,
        )
        from apps.menu.models import MenuCategory, MenuItem

        partner = Partner.objects.create(
            name="Кава", slug="kava-race", status=Partner.Status.ACTIVE
        )
        BonusProgram.objects.create(
            partner=partner, name="Кешбэк", program_type=BonusProgram.ProgramType.CASHBACK,
            trigger_event=BonusProgram.TriggerEvent.MANUAL_PURCHASE, percent=D("10"),
            max_redeem_share=D("50"),
        )
        category = MenuCategory.objects.create(partner=partner, name="Кава")
        latte = MenuItem.objects.create(
            partner=partner, category=category, name="Лате", price="80.00", public_id="RACELAT"
        )
        guest = get_or_create_guest_profile(partner_id=partner.id, telegram_id=PERSON)
        BonusTransaction.objects.create(
            partner=partner, guest=guest, transaction_type="manual", amount=D("30.00"),
            source_type="test", source_id="seed", idempotency_key="race-seed",
        )
        sync_bonus_balance_from_ledger(guest)
        real_preview = services.preview_walk_in_sale_by_customer_code
        barrier = threading.Barrier(2)

        def slow_preview(**kwargs):
            barrier.wait()  # both copies are past the "already saved?" check
            return real_preview(**kwargs)

        errors = []

        def sell():
            try:
                register_walk_in_sale_from_menu_items_by_customer_code(
                    partner_id=partner.id, customer_code=guest.customer_code,
                    items=[{"menu_item_id": latte.id, "quantity": 2}],
                    redeem_bonus=True, request_key="race-key-0001",
                )
            except Exception as exc:  # noqa: BLE001 — reported below
                errors.append(exc)
            finally:
                close_old_connections()

        with patch.object(services, "preview_walk_in_sale_by_customer_code", slow_preview):
            threads = [threading.Thread(target=sell) for _copy in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(WalkInSale.objects.count(), 1)
        sale = WalkInSale.objects.get()
        self.assertEqual(BonusTransaction.objects.filter(walk_in_sale=sale).count(), 2)
        guest.refresh_from_db()
        assert_bonus_balance_reconciled(guest)
