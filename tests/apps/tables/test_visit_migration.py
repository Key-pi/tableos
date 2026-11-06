from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

# Later migrations of other apps add NOT NULL columns these historical models
# do not know about, so roll them back as well.
BEFORE = [("tables", "0003_tablesession_unique_active_table_session_per_partner_guest"),
          ("billing", "0005_billadjustment_and_more"),
          ("orders", "0004_alter_order_payment_method"),
          ("users", "0001_initial")]
AFTER = [("tables", "0005_backfill_table_visits"), ("billing", "0007_backfill_bill_visits")]


class TableVisitBackfillMigrationTests(TransactionTestCase):
    """Owner decision 2: the active sessions of a table become one visit."""

    def setUp(self):
        executor = MigrationExecutor(connection)
        executor.migrate(BEFORE)
        self.old_apps = executor.loader.project_state(BEFORE).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def _seed(self):
        apps = self.old_apps
        Partner = apps.get_model("partners", "Partner")
        Table = apps.get_model("tables", "Table")
        TelegramAccount = apps.get_model("users", "TelegramAccount")
        GuestProfile = apps.get_model("users", "GuestProfile")
        TableSession = apps.get_model("tables", "TableSession")
        Order = apps.get_model("orders", "Order")
        Bill = apps.get_model("billing", "Bill")
        BillOrder = apps.get_model("billing", "BillOrder")

        partner = Partner.objects.create(name="Migr", slug="migr", status="active")
        table_a = Table.objects.create(partner=partner, number=1, qr_token="migr-a")
        table_b = Table.objects.create(partner=partner, number=2, qr_token="migr-b")

        def guest(tg_id):
            account = TelegramAccount.objects.create(telegram_id=tg_id)
            return GuestProfile.objects.create(
                partner=partner, telegram_account=account, customer_code=f"C{tg_id}"
            )

        s1 = TableSession.objects.create(partner=partner, guest=guest(1), table=table_a)
        s2 = TableSession.objects.create(partner=partner, guest=guest(2), table=table_a)
        s3 = TableSession.objects.create(partner=partner, guest=guest(3), table=table_b)
        closed = TableSession.objects.create(
            partner=partner, guest=guest(4), table=table_a, status="closed"
        )
        order = Order.objects.create(
            partner=partner, guest=s1.guest, table=table_a, table_session=s1, public_id="ORD1"
        )
        bill = Bill.objects.create(partner=partner, table=table_a, public_id="BILL1")
        BillOrder.objects.create(partner=partner, bill=bill, order=order)
        return {"s1": s1.id, "s2": s2.id, "s3": s3.id, "closed": closed.id, "bill": bill.id}

    def test_active_sessions_of_a_table_share_one_visit(self):
        ids = self._seed()
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(AFTER)
        apps = executor.loader.project_state(AFTER).apps
        TableSession = apps.get_model("tables", "TableSession")
        TableVisit = apps.get_model("tables", "TableVisit")
        Bill = apps.get_model("billing", "Bill")

        s1, s2, s3, closed = (
            TableSession.objects.get(id=ids[key]) for key in ("s1", "s2", "s3", "closed")
        )
        self.assertIsNotNone(s1.visit_id)
        self.assertEqual(s1.visit_id, s2.visit_id)
        self.assertNotEqual(s1.visit_id, s3.visit_id)
        self.assertIsNone(closed.visit_id)
        self.assertEqual(TableVisit.objects.filter(status="active").count(), 2)
        self.assertEqual(Bill.objects.get(id=ids["bill"]).visit_id, s1.visit_id)
