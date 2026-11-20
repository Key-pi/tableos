"""Poster connection, references, order pushes and webhook inbox (PQ-058, step 1)."""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.integrations.poster.models import (
    PosterConnection,
    PosterOrderPush,
    PosterRef,
    PosterWebhookEvent,
)
from apps.orders.models import Order
from apps.partners.models import Partner
from apps.users.models import GuestProfile


def _partner(slug: str) -> Partner:
    return Partner.objects.create(name=slug, slug=slug, status=Partner.Status.ACTIVE)


class PosterConnectionTests(TestCase):
    def setUp(self):
        self.partner = _partner("poster-a")
        self.other = _partner("poster-b")

    def _connection(self, partner, **fields):
        values = {"account": "venue", "account_number": "100200", "spot_id": 1,
                  "status": PosterConnection.Status.ACTIVE}
        values.update(fields)
        return PosterConnection.objects.create(partner=partner, **values)

    def test_the_token_is_stored_encrypted_and_read_back(self):
        connection = self._connection(self.partner)
        connection.set_access_token("100200:token-placeholder")
        connection.save()
        connection.refresh_from_db()

        self.assertNotIn("token-placeholder", connection.access_token_encrypted)
        self.assertEqual(connection.get_access_token(), "100200:token-placeholder")

        connection.clear_access_token()
        self.assertEqual(connection.access_token_encrypted, "")
        self.assertEqual(connection.get_access_token(), "")

    def test_a_venue_has_one_connection(self):
        self._connection(self.partner)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._connection(self.partner, account_number="999", spot_id=2)

    def test_one_poster_spot_works_for_one_venue_at_a_time(self):
        self._connection(self.partner)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._connection(self.other)

    def test_a_disconnected_venue_frees_its_spot(self):
        self._connection(self.partner, status=PosterConnection.Status.DISCONNECTED)
        self._connection(self.other)  # no error

        self.assertEqual(PosterConnection.objects.count(), 2)

    def test_a_spot_still_being_chosen_does_not_hold_it(self):
        self._connection(self.partner, status=PosterConnection.Status.PENDING_SPOT)
        self._connection(self.other)  # no error

        self.assertEqual(PosterConnection.objects.count(), 2)

    def test_two_spots_of_one_account_serve_two_venues_of_a_network(self):
        self._connection(self.partner, spot_id=1)
        self._connection(self.other, spot_id=2)

        self.assertEqual(PosterConnection.objects.count(), 2)

    def test_the_account_is_a_plain_poster_login(self):
        connection = PosterConnection(partner=self.partner, account_number="1", spot_id=1)
        for bad in ("evil.com/x?", "venue.joinposter.com", "Venue", "-venue", "a b", ""):
            with self.subTest(account=bad):
                connection.account = bad
                with self.assertRaises(ValidationError):
                    connection.full_clean()
        connection.account = "my-venue2"
        connection.full_clean()


class PosterRefTests(TestCase):
    def setUp(self):
        self.partner = _partner("ref-a")
        self.other = _partner("ref-b")

    def test_one_botera_object_per_poster_id_and_kind(self):
        PosterRef.objects.create(
            partner=self.partner, kind=PosterRef.Kind.PRODUCT, poster_id="6",
            object_id="00000000-0000-0000-0000-000000000001",
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            PosterRef.objects.create(
                partner=self.partner, kind=PosterRef.Kind.PRODUCT, poster_id="6",
                object_id="00000000-0000-0000-0000-000000000002",
            )

    def test_one_poster_id_per_botera_object_and_kind(self):
        PosterRef.objects.create(
            partner=self.partner, kind=PosterRef.Kind.CATEGORY, poster_id="3",
            object_id="00000000-0000-0000-0000-000000000003",
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            PosterRef.objects.create(
                partner=self.partner, kind=PosterRef.Kind.CATEGORY, poster_id="4",
                object_id="00000000-0000-0000-0000-000000000003",
            )

    def test_the_same_poster_id_lives_in_other_venues_and_kinds(self):
        object_id = "00000000-0000-0000-0000-000000000004"
        PosterRef.objects.create(
            partner=self.partner, kind=PosterRef.Kind.MODIFIER, poster_id="1", object_id=object_id,
        )
        PosterRef.objects.create(
            partner=self.other, kind=PosterRef.Kind.MODIFIER, poster_id="1", object_id=object_id,
        )
        PosterRef.objects.create(
            partner=self.partner, kind=PosterRef.Kind.MODIFIER_GROUP, poster_id="1",
            object_id=object_id,
        )

        self.assertEqual(PosterRef.objects.count(), 3)

    def test_a_variant_is_keyed_by_product_and_modificator(self):
        self.assertEqual(PosterRef.variant_key(6, "2"), "6:2")


class PosterOrderPushTests(TestCase):
    def test_an_order_is_pushed_once(self):
        partner = _partner("push-a")
        guest = GuestProfile.objects.create(partner=partner)
        order = Order.objects.create(partner=partner, guest=guest)
        PosterOrderPush.objects.create(partner=partner, order=order)

        with self.assertRaises(IntegrityError), transaction.atomic():
            PosterOrderPush.objects.create(partner=partner, order=order)

    def test_a_new_push_waits_to_be_sent(self):
        partner = _partner("push-b")
        guest = GuestProfile.objects.create(partner=partner)
        order = Order.objects.create(partner=partner, guest=guest)
        push = PosterOrderPush.objects.create(partner=partner, order=order)

        self.assertEqual(push.status, PosterOrderPush.Status.PENDING)
        self.assertEqual(push.attempts, 0)
        self.assertIsNone(push.incoming_order_id)


class PosterWebhookEventTests(TestCase):
    BODY = {
        "account": "venue",
        "object": "transaction",
        "object_id": 1,
        "action": "changed",
        "time": "1795151919",
        "verify": "0" * 32,
        "data": '{"transactions_history":{"type_history":"settable"}}',
    }

    def test_a_repeated_webhook_has_the_same_key(self):
        again = dict(self.BODY)
        self.assertEqual(
            PosterWebhookEvent.dedupe_key_for(self.BODY),
            PosterWebhookEvent.dedupe_key_for(again),
        )

    def test_two_events_in_the_same_second_differ_by_their_data(self):
        # Step 0: accepting an order sent two «transaction changed» webhooks
        # with the same time and different data.
        other = dict(
            self.BODY, data='{"transactions_history":{"type_history":"changeorderstatus"}}',
        )
        self.assertNotEqual(
            PosterWebhookEvent.dedupe_key_for(self.BODY),
            PosterWebhookEvent.dedupe_key_for(other),
        )

    def test_the_signature_does_not_change_the_key(self):
        resigned = dict(self.BODY, verify="f" * 32)
        self.assertEqual(
            PosterWebhookEvent.dedupe_key_for(self.BODY),
            PosterWebhookEvent.dedupe_key_for(resigned),
        )

    def test_the_inbox_keeps_one_row_per_event(self):
        key = PosterWebhookEvent.dedupe_key_for(self.BODY)
        PosterWebhookEvent.objects.create(
            dedupe_key=key, account="venue", entity="transaction", entity_id="1",
            action="changed", event_time=1795151919, payload={},
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            PosterWebhookEvent.objects.create(
                dedupe_key=key, account="venue", entity="transaction", entity_id="1",
                action="changed", event_time=1795151919, payload={},
            )
