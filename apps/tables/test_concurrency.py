from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from apps.partners.models import Partner
from apps.tables.models import Table, TableSession
from apps.tables.services import activate_table_session
from apps.users.models import GuestProfile, TelegramAccount


@skipUnless(connection.vendor == "postgresql", "PostgreSQL concurrency test")
class TableSessionConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.partner = Partner.objects.create(
            name="Session concurrency venue",
            slug="session-concurrency-venue",
            status=Partner.Status.ACTIVE,
        )
        self.tables = [
            Table.objects.create(
                partner=self.partner,
                number=number,
                name=f"Table {number}",
                qr_token=f"session-concurrency-{number}",
            )
            for number in (1, 2)
        ]
        account = TelegramAccount.objects.create(
            telegram_id=980002,
            username="session_concurrency_guest",
        )
        self.guest = GuestProfile.objects.create(
            partner=self.partner,
            telegram_account=account,
        )
        self.telegram_id = account.telegram_id

    def _activate(self, table, barrier):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return activate_table_session(
                partner_id=self.partner.id,
                telegram_id=self.telegram_id,
                payload=table.deep_link_payload,
                username="session_concurrency_guest",
            )
        finally:
            close_old_connections()

    def test_concurrent_two_table_scans_leave_one_active_session(self):
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            sessions = list(
                executor.map(
                    lambda table: self._activate(table, barrier),
                    self.tables,
                )
            )

        active_sessions = TableSession.objects.filter(
            partner=self.partner,
            guest=self.guest,
            status=TableSession.Status.ACTIVE,
        )
        self.assertEqual(active_sessions.count(), 1)
        self.assertEqual(
            TableSession.objects.filter(
                id__in=[session.id for session in sessions],
                status=TableSession.Status.CLOSED,
            ).count(),
            1,
        )
