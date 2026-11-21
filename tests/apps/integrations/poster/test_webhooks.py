"""Poster webhooks (PQ-058, step 4): the signature, the inbox without
duplicates, the venue from the signed login, one menu sync after the last
change; orders and checks wait for steps 5–6. The signature is computed
here on its own, as Poster does: md5 of
``account;object;object_id;action;[data;]time;secret``."""

import hashlib
import json
import threading
from datetime import timedelta
from unittest import mock, skipUnless
from urllib.parse import urlencode

from django.db import connection as db_connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from apps.integrations.poster import menu_sync, tasks, webhooks
from apps.integrations.poster.models import PosterConnection, PosterRef, PosterWebhookEvent
from apps.integrations.poster.tasks import sync_menu_after_changes_task
from apps.partners.models import Partner
from tests.apps.integrations.poster.test_menu_sync import connect, trial_menu

SECRET = "secret-placeholder"
URL = "/integrations/poster/webhook/"
Status = PosterWebhookEvent.Status


def signed(*, account="kava", object="product", object_id=1, action="changed",
           time="1795150999", data=None, account_number="100200", secret=SECRET):
    body = {"account": account, "object": object, "object_id": object_id, "action": action,
            "time": time, "account_number": account_number}
    parts = [account, object, str(object_id), action]
    if data is not None:
        body["data"] = data
        parts.append(data)
    parts += [time, secret]
    body["verify"] = hashlib.md5(";".join(parts).encode("utf-8")).hexdigest()
    return body


def venue(slug, status=Partner.Status.ACTIVE):
    return Partner.objects.create(name=slug, slug=slug, status=status)


@override_settings(POSTER_APPLICATION_SECRET=SECRET)
class WebhookFixture(TestCase):
    def setUp(self):
        self.partner = venue("poster-hook-kava")
        self.connection = connect(self.partner)
        self.fetch_mock = mock.patch.object(
            menu_sync, "fetch_menu", side_effect=lambda token: trial_menu(),
        ).start()
        self.addCleanup(mock.patch.stopall)

    def post(self, body, *, client=None, form=False):
        client = client or self.client
        with self.captureOnCommitCallbacks(execute=True):
            if form:
                return client.post(URL, urlencode(body),
                                   content_type="application/x-www-form-urlencoded")
            return client.post(URL, json.dumps(body, ensure_ascii=False),
                               content_type="application/json")


class SignatureTests(WebhookFixture):
    def test_a_signed_menu_change_brings_the_menu(self):
        response = self.post(signed())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "accept"})
        event = PosterWebhookEvent.objects.get()
        self.assertEqual((event.status, event.partner, event.account, event.entity,
                          event.entity_id, event.action, event.event_time),
                         (Status.PROCESSED, self.partner, "kava", "product", "1", "changed",
                          1795150999))
        self.assertNotIn("verify", event.payload)
        self.connection.refresh_from_db()
        self.assertIsNotNone(self.connection.menu_changed_at)
        self.fetch_mock.assert_called_once()
        self.assertTrue(PosterRef.objects.filter(partner=self.partner).exists())

    def test_a_wrong_signature_writes_nothing(self):
        wrong = signed()
        wrong["verify"] = "0" * 32
        tampered = signed()
        tampered["object_id"] = 2
        for body in (wrong, tampered, {k: v for k, v in signed().items() if k != "verify"}):
            with self.subTest(body=body):
                self.assertEqual(self.post(body).status_code, 403)
        self.assertFalse(PosterWebhookEvent.objects.exists())
        self.fetch_mock.assert_not_called()

    @override_settings(POSTER_APPLICATION_SECRET="")
    def test_without_the_platforms_secret_nothing_is_accepted(self):
        self.assertEqual(self.post(signed(secret="")).status_code, 403)
        self.assertFalse(PosterWebhookEvent.objects.exists())

    def test_not_json_and_not_signed_is_refused(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(URL, "not json", content_type="application/json")
        self.assertEqual(response.status_code, 403)

    def test_only_post(self):
        self.assertEqual(self.client.get(URL).status_code, 405)

    def test_no_csrf_token_is_needed(self):
        response = self.post(signed(), client=Client(enforce_csrf_checks=True))
        self.assertEqual(response.status_code, 200)

    def test_data_is_signed_as_it_came(self):
        data = json.dumps({"name": "Кава лате", "price": "5500"}, ensure_ascii=False)
        self.assertEqual(self.post(signed(data=data)).status_code, 200)
        self.assertEqual(PosterWebhookEvent.objects.get().status, Status.PROCESSED)

    def test_a_form_encoded_webhook(self):
        response = self.post(signed(data='{"type":1}'), form=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PosterWebhookEvent.objects.count(), 1)


class InboxTests(WebhookFixture):
    def test_poster_repeating_an_event_is_one_event(self):
        body = signed()
        self.post(body)
        self.assertEqual(self.post(body).json(), {"status": "accept"})
        self.assertEqual(PosterWebhookEvent.objects.count(), 1)
        self.fetch_mock.assert_called_once()

    def test_events_of_one_second_differ_by_data(self):
        first = signed(object="transaction", data='{"type_history":"settable"}')
        second = signed(object="transaction", data='{"type_history":"changeorderstatus"}')
        self.post(first)
        self.post(second)
        self.assertEqual(PosterWebhookEvent.objects.count(), 2)

    def test_an_event_is_processed_once(self):
        event = webhooks.receive(signed())
        self.assertEqual(webhooks.process(event.pk), Status.PROCESSED)
        self.connection.refresh_from_db()
        marked = self.connection.menu_changed_at
        self.assertEqual(webhooks.process(event.pk), "")
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.menu_changed_at, marked)


class WhichVenueTests(WebhookFixture):
    def test_the_venue_comes_from_the_signed_login(self):
        other = venue("poster-hook-other")
        other_connection = connect(other, spot_id=2)
        PosterConnection.objects.filter(pk=other_connection.pk).update(
            account="other", account_number="777",
        )
        self.post(signed(account_number="777"))  # account_number is not signed
        self.assertEqual(PosterWebhookEvent.objects.get().partner, self.partner)
        other_connection.refresh_from_db()
        self.assertIsNone(other_connection.menu_changed_at)

    def test_an_unknown_account_is_not_ours(self):
        self.assertEqual(self.post(signed(account="nobody")).json(), {"status": "accept"})
        event = PosterWebhookEvent.objects.get()
        self.assertEqual((event.status, event.partner), (Status.IGNORED, None))
        self.fetch_mock.assert_not_called()

    def test_a_disconnected_or_suspended_venue_is_not_ours(self):
        PosterConnection.objects.filter(pk=self.connection.pk).update(
            status=PosterConnection.Status.DISCONNECTED,
        )
        self.post(signed(object_id=1))
        PosterConnection.objects.filter(pk=self.connection.pk).update(
            status=PosterConnection.Status.ACTIVE,
        )
        Partner.objects.filter(pk=self.partner.pk).update(status=Partner.Status.SUSPENDED)
        self.post(signed(object_id=2))
        self.assertEqual(
            list(PosterWebhookEvent.objects.values_list("status", flat=True)),
            [Status.IGNORED, Status.IGNORED],
        )
        self.fetch_mock.assert_not_called()

    def test_one_account_two_venues(self):
        second = venue("poster-hook-second")
        second_connection = connect(second, spot_id=2)
        self.post(signed(object="category", object_id=3))
        event = PosterWebhookEvent.objects.get()
        self.assertEqual((event.status, event.partner), (Status.PROCESSED, None))
        for connection in (self.connection, second_connection):
            connection.refresh_from_db()
            self.assertIsNotNone(connection.menu_changed_at)
        self.assertEqual(self.fetch_mock.call_count, 2)


class WhatIsDoneTests(WebhookFixture):
    def test_every_menu_object(self):
        for number, entity in enumerate(("product", "dish", "category"), start=1):
            with self.subTest(entity=entity):
                self.post(signed(object=entity, object_id=number))
                self.assertEqual(PosterWebhookEvent.objects.get(entity=entity).status,
                                 Status.PROCESSED)
        self.assertEqual(self.fetch_mock.call_count, 3)

    def test_orders_and_checks_wait_for_steps_5_and_6(self):
        for entity in ("incoming_order", "transaction"):
            self.post(signed(object=entity, action="added", data='{"type":1}'))
        for event in PosterWebhookEvent.objects.all():
            self.assertEqual((event.status, event.partner), (Status.IGNORED, self.partner))
        self.fetch_mock.assert_not_called()
        self.connection.refresh_from_db()
        self.assertIsNone(self.connection.menu_changed_at)

    def test_other_objects_are_not_needed(self):
        self.post(signed(object="client", action="added"))
        self.post(signed(object="incoming_order", action="added"))
        client = PosterWebhookEvent.objects.get(entity="client")
        self.assertEqual(client.status, Status.IGNORED)
        # Orders say they wait; the rest says it is not needed.
        self.assertNotEqual(client.error,
                            PosterWebhookEvent.objects.get(entity="incoming_order").error)
        self.fetch_mock.assert_not_called()


@override_settings(POSTER_APPLICATION_SECRET=SECRET)
class MenuQuietTests(TestCase):
    """Poster sends a webhook per changed product; the menu is brought once,
    a minute after the last change."""

    def setUp(self):
        self.connection = connect(venue("poster-quiet"))
        self.fetch_mock = mock.patch.object(
            menu_sync, "fetch_menu", side_effect=lambda token: trial_menu(),
        ).start()
        self.addCleanup(mock.patch.stopall)

    def test_each_change_waits_a_minute(self):
        with mock.patch.object(tasks.sync_menu_after_changes_task, "apply_async") as later:
            with self.captureOnCommitCallbacks(execute=True):
                event = webhooks.receive(signed())
            # The receiving task ran eagerly; the sync is put off.
        self.assertEqual(PosterWebhookEvent.objects.get(pk=event.pk).status, Status.PROCESSED)
        later.assert_called_once()
        self.assertEqual(later.call_args.kwargs["countdown"], webhooks.MENU_QUIET_SECONDS)
        self.fetch_mock.assert_not_called()

    def test_only_the_last_change_brings_the_menu(self):
        earlier = timezone.now() - timedelta(seconds=30)
        last = timezone.now()
        PosterConnection.objects.filter(pk=self.connection.pk).update(menu_changed_at=last)
        self.assertEqual(sync_menu_after_changes_task(str(self.connection.pk),
                                                      earlier.isoformat()), "")
        self.fetch_mock.assert_not_called()
        self.assertTrue(sync_menu_after_changes_task(str(self.connection.pk), last.isoformat()))
        self.fetch_mock.assert_called_once()

    def test_a_refusal_after_the_quiet_is_not_raised(self):
        last = timezone.now()
        PosterConnection.objects.filter(pk=self.connection.pk).update(
            menu_changed_at=last, status=PosterConnection.Status.DISCONNECTED,
        )
        self.assertEqual(sync_menu_after_changes_task(str(self.connection.pk),
                                                      last.isoformat()), "")
        self.fetch_mock.assert_not_called()


@skipUnless(db_connection.vendor == "postgresql", "нужна настоящая параллельная запись")
@override_settings(POSTER_APPLICATION_SECRET=SECRET)
class SameWebhookTwiceAtOnceTests(TransactionTestCase):
    """Poster repeats an event while the first is still being written: one
    row, one task, no error to Poster."""

    def test_one_event_and_one_task(self):
        connect(venue("poster-hook-race"))
        barrier = threading.Barrier(2)
        seen = webhooks._seen

        def seen_and_wait(key):
            answer = seen(key)
            barrier.wait(timeout=5)
            return answer

        results, errors = [], []

        def run():
            try:
                results.append(webhooks.receive(signed()))
            except Exception as exc:  # noqa: BLE001 — the test reports it
                errors.append(exc)
            finally:
                db_connection.close()

        with mock.patch.object(webhooks, "_seen", side_effect=seen_and_wait), \
                mock.patch.object(tasks.process_webhook_task, "delay") as delay:
            threads = [threading.Thread(target=run) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(PosterWebhookEvent.objects.count(), 1)
        self.assertEqual(len([event for event in results if event is not None]), 1)
        delay.assert_called_once()


@skipUnless(db_connection.vendor == "postgresql", "нужны построчные блокировки")
@override_settings(POSTER_APPLICATION_SECRET=SECRET)
class OneEventTwoWorkersTests(TransactionTestCase):
    """Celery may deliver a task twice: the event's row lock and its status
    let one worker do it."""

    def test_processed_once(self):
        connect(venue("poster-hook-workers"))
        with mock.patch.object(tasks.process_webhook_task, "delay"):
            event = webhooks.receive(signed())
        barrier = threading.Barrier(2)
        connections_of = webhooks._connections_of

        def connections_and_wait(event):
            found = connections_of(event)
            try:
                barrier.wait(timeout=1.5)
            except threading.BrokenBarrierError:
                pass
            return found

        results, errors = [], []

        def run():
            try:
                results.append(webhooks.process(event.pk))
            except Exception as exc:  # noqa: BLE001 — the test reports it
                errors.append(exc)
            finally:
                db_connection.close()

        with mock.patch.object(webhooks, "_connections_of", side_effect=connections_and_wait), \
                mock.patch.object(tasks.sync_menu_after_changes_task, "apply_async") as later:
            threads = [threading.Thread(target=run) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(sorted(results), ["", Status.PROCESSED])
        later.assert_called_once()
