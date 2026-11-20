"""Connecting a venue to Poster: start in Admin, Poster's return, choosing the
spot, disconnecting (PQ-058, step 2). Poster itself is replaced by mocks."""

import threading
from datetime import timedelta
from unittest import mock, skipUnless
from urllib.parse import parse_qs, urlparse

from django.contrib import admin
from django.db import connection as db_connection
from django.db import transaction
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.integrations.poster import services
from apps.integrations.poster.admin import PosterConnectionAdmin
from apps.integrations.poster.client import PosterError
from apps.integrations.poster.models import PosterConnection
from apps.integrations.poster.services import PosterConnectError
from apps.partners.models import Partner
from apps.users.admin_navigation import sidebar
from apps.users.constants import AdminAccessPreset, AdminSection
from apps.users.models import AdminAccessProfile, AdminSectionPermission, User

POSTER_SETTINGS = {
    "POSTER_APPLICATION_ID": "5611",
    "POSTER_APPLICATION_SECRET": "secret-placeholder",
    "POSTER_REDIRECT_URI": "https://shop.example.com/integrations/poster/oauth/callback/",
}
TOKEN = "100200:token-placeholder"
ONE_SPOT = [{"spot_id": 1, "name": "Головна", "address": ""}]
TWO_SPOTS = [{"spot_id": 1, "name": "Центр", "address": ""},
             {"spot_id": 2, "name": "Поділ", "address": ""}]
CALLBACK = "/integrations/poster/oauth/callback/"


def _answer(**fields):
    answer = {"access_token": TOKEN, "account_number": "100200", "tariff": {"key": "business"}}
    answer.update(fields)
    return answer


def _owner(partner, username):
    user = User.objects.create_user(
        username=username, password="pass12345", partner=partner, role=User.Role.OWNER,
        is_staff=True,
    )
    AdminAccessProfile.objects.create(
        user=user, partner=partner, preset=AdminAccessPreset.OWNER,
        is_partner_owner=True, can_access_admin=True,
    )
    return user


def _manager(partner, username):
    """May open and change the venue's profile in Admin, but is not its owner."""

    user = User.objects.create_user(
        username=username, password="pass12345", partner=partner, role=User.Role.MANAGER,
        is_staff=True,
    )
    profile = AdminAccessProfile.objects.create(
        user=user, partner=partner, preset=AdminAccessPreset.CUSTOM, can_access_admin=True,
    )
    AdminSectionPermission.objects.update_or_create(
        profile=profile, section=AdminSection.PARTNER_PROFILE,
        defaults={"can_view": True, "can_change": True},
    )
    return user


@override_settings(**POSTER_SETTINGS)
class ConnectionFlowTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(
            name="Kava", slug="poster-kava", status=Partner.Status.ACTIVE,
        )
        self.owner = _owner(self.partner, "poster_kava_owner")
        self.exchange = mock.patch.object(services, "exchange_code", return_value=_answer())
        self.spots = mock.patch.object(services, "fetch_spots", return_value=ONE_SPOT)
        self.exchange_mock = self.exchange.start()
        self.spots_mock = self.spots.start()
        self.addCleanup(mock.patch.stopall)
        self.client.force_login(self.owner)

    def _start(self):
        response = self.client.post(reverse("admin:poster_posterconnection_connect"))
        self.assertEqual(response.status_code, 302)
        return response["Location"]

    def _return(self, **query):
        params = {"code": "code-placeholder", "account": "venue"}
        params.update(query)
        return self.client.get(CALLBACK, params)

    def test_connect_sends_the_owner_to_poster(self):
        location = urlparse(self._start())
        query = {key: values[0] for key, values in parse_qs(location.query).items()}

        self.assertEqual((location.scheme, location.netloc, location.path),
                         ("https", "joinposter.com", "/api/auth"))
        self.assertEqual(query, {
            "application_id": "5611",
            "redirect_uri": POSTER_SETTINGS["POSTER_REDIRECT_URI"],
            "response_type": "code",
        })
        self.assertEqual(self.client.session[services.SESSION_KEY]["partner_id"],
                         str(self.partner.pk))

    def test_starting_is_a_post(self):
        response = self.client.get(reverse("admin:poster_posterconnection_connect"))

        self.assertEqual(response.status_code, 405)
        self.assertNotIn(services.SESSION_KEY, self.client.session)

    def test_one_spot_connects_at_once(self):
        self._start()
        response = self._return()

        connection = PosterConnection.objects.get(partner=self.partner)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"],
                         reverse("admin:poster_posterconnection_change", args=[connection.pk]))
        self.assertEqual(connection.status, PosterConnection.Status.ACTIVE)
        self.assertEqual(connection.spot_id, 1)
        self.assertEqual((connection.account, connection.account_number), ("venue", "100200"))
        self.assertEqual(connection.get_access_token(), TOKEN)
        self.assertNotIn("token-placeholder", connection.access_token_encrypted)
        self.assertEqual(connection.connected_by, self.owner)
        self.assertEqual(connection.tariff, {"key": "business"})
        self.assertEqual(connection.available_spots, ONE_SPOT)
        self.assertGreater(connection.token_expires_at, timezone.now() + timedelta(days=700))
        self.exchange_mock.assert_called_once()
        kwargs = self.exchange_mock.call_args.kwargs
        self.assertEqual((kwargs["account"], kwargs["code"]), ("venue", "code-placeholder"))
        self.assertEqual(kwargs["application_secret"], "secret-placeholder")
        self.spots_mock.assert_called_once_with(TOKEN)

    def test_a_start_is_spent_once(self):
        self._start()
        self._return()
        response = self._return(code="another-code")

        self.assertEqual(self.exchange_mock.call_count, 1)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(PosterConnection.objects.count(), 1)

    def test_a_return_without_a_start_in_admin_asks_nothing_of_poster(self):
        response = self._return()

        self.assertEqual(response.status_code, 302)
        self.assertFalse(PosterConnection.objects.exists())
        self.exchange_mock.assert_not_called()

    def test_an_old_start_is_refused(self):
        self._start()
        session = self.client.session
        session[services.SESSION_KEY]["started_at"] = (
            timezone.now() - timedelta(minutes=20)
        ).isoformat()
        session.save()

        self._return()

        self.assertFalse(PosterConnection.objects.exists())
        self.exchange_mock.assert_not_called()

    def test_a_start_of_another_person_is_refused(self):
        self._start()
        session = self.client.session
        session[services.SESSION_KEY]["user_id"] = self.owner.pk + 1000
        session.save()

        self._return()

        self.assertFalse(PosterConnection.objects.exists())
        self.exchange_mock.assert_not_called()

    def test_an_owner_who_lost_the_venue_meanwhile_is_refused(self):
        self._start()
        AdminAccessProfile.objects.filter(user=self.owner).update(is_partner_owner=False)

        self._return()

        self.assertFalse(PosterConnection.objects.exists())
        self.exchange_mock.assert_not_called()

    def test_a_strange_account_is_refused_before_asking_poster(self):
        for bad in ("evil.com/x?", "venue.joinposter.com", ""):
            with self.subTest(account=bad):
                self._start()
                self._return(account=bad)
        self.assertFalse(PosterConnection.objects.exists())
        self.exchange_mock.assert_not_called()

    def test_a_cancelled_login_saves_nothing(self):
        self._start()
        response = self._return(code="", error="access_denied")

        self.assertEqual(response.status_code, 302)
        self.assertFalse(PosterConnection.objects.exists())
        self.exchange_mock.assert_not_called()
        self.assertNotIn(services.SESSION_KEY, self.client.session)

    def test_poster_refusing_the_code_saves_nothing(self):
        self.exchange_mock.side_effect = PosterError("wrong code", code=33, http_status=400)
        self._start()
        response = self.client.get(CALLBACK, {"code": "x", "account": "venue"}, follow=True)

        self.assertFalse(PosterConnection.objects.exists())
        self.assertContains(response, "wrong code")

    def test_several_spots_wait_for_the_owners_choice(self):
        self.spots_mock.return_value = TWO_SPOTS
        self._start()
        self._return()

        connection = PosterConnection.objects.get(partner=self.partner)
        self.assertEqual(connection.status, PosterConnection.Status.PENDING_SPOT)
        self.assertIsNone(connection.spot_id)

        act = reverse("admin:poster_posterconnection_act", args=[connection.pk])
        self.client.post(act, {"object_action": "spot:2"})

        connection.refresh_from_db()
        self.assertEqual(connection.status, PosterConnection.Status.ACTIVE)
        self.assertEqual(connection.spot_id, 2)

    def test_a_spot_outside_the_account_cannot_be_chosen(self):
        self.spots_mock.return_value = TWO_SPOTS
        self._start()
        self._return()
        connection = PosterConnection.objects.get(partner=self.partner)

        with self.assertRaises(PosterConnectError):
            services.choose_spot(connection_id=connection.pk, user=self.owner, spot_id=99)
        connection.refresh_from_db()
        self.assertEqual(connection.status, PosterConnection.Status.PENDING_SPOT)

    def test_a_spot_of_another_venue_is_not_taken_over(self):
        other = Partner.objects.create(
            name="Other", slug="poster-other", status=Partner.Status.ACTIVE,
        )
        PosterConnection.objects.create(
            partner=other, account="venue", account_number="100200", spot_id=1,
            status=PosterConnection.Status.ACTIVE,
        )
        self._start()
        response = self.client.get(CALLBACK, {"code": "x", "account": "venue"}, follow=True)

        self.assertFalse(PosterConnection.objects.filter(partner=self.partner).exists())
        self.assertContains(response, "Точка «Головна» уже подключена к другому заведению")

    def test_disconnecting_forgets_the_token_and_keeps_the_record(self):
        self._start()
        self._return()
        connection = PosterConnection.objects.get(partner=self.partner)

        act = reverse("admin:poster_posterconnection_act", args=[connection.pk])
        self.client.post(act, {"object_action": "disconnect"})

        connection.refresh_from_db()
        self.assertEqual(connection.status, PosterConnection.Status.DISCONNECTED)
        self.assertEqual(connection.access_token_encrypted, "")
        self.assertIsNone(connection.token_expires_at)
        self.assertEqual((connection.account, connection.spot_id), ("venue", 1))

    def test_connecting_again_updates_the_same_record(self):
        self._start()
        self._return()
        first = PosterConnection.objects.get(partner=self.partner)
        services.disconnect(connection_id=first.pk, user=self.owner)

        self.exchange_mock.return_value = _answer(access_token="100200:new-token-placeholder")
        self._start()
        self._return()

        connection = PosterConnection.objects.get(partner=self.partner)
        self.assertEqual(connection.pk, first.pk)
        self.assertEqual(connection.status, PosterConnection.Status.ACTIVE)
        self.assertEqual(connection.get_access_token(), "100200:new-token-placeholder")


@override_settings(**POSTER_SETTINGS)
class WhoMayConnectTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(
            name="Kava", slug="poster-who", status=Partner.Status.ACTIVE,
        )
        self.owner = _owner(self.partner, "poster_who_owner")
        self.manager = _manager(self.partner, "poster_who_manager")
        self.factory = RequestFactory()

    def test_only_the_owner_starts_a_connection(self):
        with self.assertRaises(PosterConnectError):
            services.start_connection(session={}, user=self.manager, partner_id=self.partner.pk)

        session = {}
        services.start_connection(session=session, user=self.owner, partner_id=self.partner.pk)
        self.assertIn(services.SESSION_KEY, session)

    @override_settings(POSTER_APPLICATION_ID="")
    def test_without_the_platforms_poster_application_nothing_starts(self):
        with self.assertRaises(PosterConnectError):
            services.start_connection(session={}, user=self.owner, partner_id=self.partner.pk)

    def test_a_manager_gets_no_buttons_and_cannot_press_them(self):
        connection = PosterConnection.objects.create(
            partner=self.partner, account="venue", account_number="1", spot_id=1,
            status=PosterConnection.Status.ACTIVE,
        )
        connection.set_access_token(TOKEN)
        connection.save()
        request = self.factory.get("/admin/")
        request.user = self.manager
        request.session = {}
        page = PosterConnectionAdmin(PosterConnection, admin.site)

        self.assertEqual(page.get_object_actions(request, connection), [])
        with self.assertRaises(PosterConnectError):
            services.disconnect(connection_id=connection.pk, user=self.manager)

        self.client.force_login(self.manager)
        act = reverse("admin:poster_posterconnection_act", args=[connection.pk])
        self.client.post(act, {"object_action": "disconnect"})
        connection.refresh_from_db()
        self.assertEqual(connection.status, PosterConnection.Status.ACTIVE)

    def test_the_owner_finds_poster_in_settings_before_connecting(self):
        request = self.factory.get("/admin/")
        request.user = self.owner
        request.session = {}
        request.path = "/admin/"
        links = [item["link"] for group in sidebar(request) for item in group["items"]
                 if "/poster/posterconnection/" in item["link"]]
        self.assertEqual(links, ["/admin/poster/posterconnection/"])

        self.client.force_login(self.owner)
        page = self.client.get("/admin/poster/posterconnection/")
        self.assertContains(page, reverse("admin:poster_posterconnection_connect"))

    def test_the_connection_page_has_no_save_buttons(self):
        connection = PosterConnection.objects.create(
            partner=self.partner, account="venue", account_number="1", spot_id=1,
            status=PosterConnection.Status.ACTIVE,
        )
        self.client.force_login(self.owner)
        page = self.client.get(
            reverse("admin:poster_posterconnection_change", args=[connection.pk]),
        )

        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'name="_save"')
        self.assertNotContains(page, 'name="_continue"')
        self.assertContains(page, "disconnect")

    def test_the_platform_has_no_venue_to_connect(self):
        platform = User.objects.create_superuser(
            username="poster_who_platform", password="pass12345", email="p@example.com",
        )
        self.client.force_login(platform)
        response = self.client.post(reverse("admin:poster_posterconnection_connect"))

        self.assertEqual(response.status_code, 302)
        self.assertNotIn("joinposter.com", response["Location"])
        self.assertNotIn(services.SESSION_KEY, self.client.session)


@override_settings(**POSTER_SETTINGS)
class NetworkOutsideTransactionTests(TransactionTestCase):
    def test_poster_is_asked_before_any_transaction_opens(self):
        partner = Partner.objects.create(
            name="Kava", slug="poster-tx", status=Partner.Status.ACTIVE,
        )
        owner = _owner(partner, "poster_tx_owner")
        seen = []

        def exchange(**kwargs):
            seen.append(transaction.get_connection().in_atomic_block)
            return _answer()

        def spots(token):
            seen.append(transaction.get_connection().in_atomic_block)
            return ONE_SPOT

        with mock.patch.object(services, "exchange_code", side_effect=exchange), \
                mock.patch.object(services, "fetch_spots", side_effect=spots):
            services.finish_connection(
                user=owner, partner_id=partner.pk, account="venue", code="code-placeholder",
            )

        self.assertEqual(seen, [False, False])
        self.assertEqual(PosterConnection.objects.get(partner=partner).status,
                         PosterConnection.Status.ACTIVE)


@skipUnless(db_connection.vendor == "postgresql", "нужна настоящая параллельная запись")
@override_settings(**POSTER_SETTINGS)
class SameSpotRaceTests(TransactionTestCase):
    def test_two_venues_choosing_one_spot_at_once_get_it_once(self):
        connections, owners = [], []
        for number in (1, 2):
            partner = Partner.objects.create(
                name=f"Race {number}", slug=f"poster-race-{number}", status=Partner.Status.ACTIVE,
            )
            owners.append(_owner(partner, f"poster_race_owner_{number}"))
            connection = PosterConnection.objects.create(
                partner=partner, account="venue", account_number="100200",
                status=PosterConnection.Status.PENDING_SPOT, available_spots=TWO_SPOTS,
            )
            connection.set_access_token(TOKEN)
            connection.save()
            connections.append(connection)

        barrier = threading.Barrier(2)
        results = []
        real_taken = services._spot_taken

        def slow_taken(*args, **kwargs):
            answer = real_taken(*args, **kwargs)
            barrier.wait(timeout=10)  # both checked: neither saw the other yet
            return answer

        def choose(index):
            try:
                services.choose_spot(
                    connection_id=connections[index].pk, user=owners[index], spot_id=1,
                )
                results.append("ok")
            except PosterConnectError:
                results.append("refused")
            finally:
                db_connection.close()

        with mock.patch.object(services, "_spot_taken", side_effect=slow_taken):
            threads = [threading.Thread(target=choose, args=(index,)) for index in (0, 1)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)

        self.assertEqual(sorted(results), ["ok", "refused"])
        self.assertEqual(
            PosterConnection.objects.filter(
                status=PosterConnection.Status.ACTIVE, spot_id=1,
            ).count(),
            1,
        )
