"""Traffic sources: deep links `start=src_<code>` (who brought a guest)."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from asgiref.sync import async_to_sync
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.analytics.models import SourceVisit, TrafficSource
from apps.analytics.sources import parse_source_payload, record_source_visit
from apps.partners.models import BotInstance, Partner
from apps.users.constants import AdminAccessPreset
from apps.users.models import AdminAccessProfile, GuestProfile, User
from apps.users.services import get_or_create_guest_profile_with_created
from bot.handlers.start import start_handler
from bot.services.runtime import BotRuntimeConfig, register_runtime_configs


def _partner(slug: str) -> Partner:
    return Partner.objects.create(name=slug.title(), slug=slug, status=Partner.Status.ACTIVE)


def _guest(partner: Partner, telegram_id: int) -> GuestProfile:
    guest, _ = get_or_create_guest_profile_with_created(
        partner_id=partner.id, telegram_id=telegram_id
    )
    return guest


class SourcePayloadTests(TestCase):
    def test_source_payloads_are_recognised(self):
        self.assertEqual(parse_source_payload("src_insta"), "insta")
        self.assertEqual(parse_source_payload("src_Flyer-01"), "flyer-01")
        # Source-shaped but unusable: still not a table payload.
        self.assertEqual(parse_source_payload("src_"), "")
        self.assertEqual(parse_source_payload("src_bad!code"), "")

    def test_other_payloads_are_not_sources(self):
        self.assertIsNone(parse_source_payload("table_1_abc"))
        self.assertIsNone(parse_source_payload(""))


class TrafficSourceModelTests(TestCase):
    def setUp(self):
        self.partner = _partner("src-venue")

    def test_code_is_generated_and_link_is_built(self):
        source = TrafficSource.objects.create(partner=self.partner, name="Instagram")
        self.assertRegex(source.code, r"^[a-z0-9]{8}$")
        self.assertEqual(source.deep_link_payload, f"src_{source.code}")
        self.assertEqual(
            source.telegram_start_url("@venue_bot"),
            f"https://t.me/venue_bot?start=src_{source.code}",
        )

    def test_code_is_validated_and_unique_per_partner(self):
        bad = TrafficSource(partner=self.partner, name="Bad", code="Bad Code")
        with self.assertRaises(ValidationError):
            bad.full_clean()
        TrafficSource.objects.create(partner=self.partner, name="A", code="insta")
        other = _partner("other-venue")
        TrafficSource.objects.create(partner=other, name="A", code="insta")
        with self.assertRaises(IntegrityError), transaction.atomic():
            TrafficSource.objects.create(partner=self.partner, name="B", code="insta")


class RecordSourceVisitTests(TestCase):
    def setUp(self):
        self.partner = _partner("visit-venue")
        self.source = TrafficSource.objects.create(
            partner=self.partner, name="Instagram", code="insta"
        )

    def test_new_guest_is_attributed_once_and_every_click_is_logged(self):
        guest = _guest(self.partner, 610001)
        first = record_source_visit(
            partner_id=self.partner.id, guest_id=guest.id, payload="src_insta", is_new_guest=True
        )
        second = record_source_visit(
            partner_id=self.partner.id, guest_id=guest.id, payload="src_insta", is_new_guest=False
        )
        self.assertTrue(first.is_new_guest)
        self.assertFalse(second.is_new_guest)
        self.assertEqual(SourceVisit.objects.filter(guest=guest).count(), 2)

    def test_a_guest_has_at_most_one_acquiring_source(self):
        flyer = TrafficSource.objects.create(partner=self.partner, name="Flyer", code="flyer")
        guest = _guest(self.partner, 610002)
        record_source_visit(
            partner_id=self.partner.id, guest_id=guest.id, payload="src_insta", is_new_guest=True
        )
        # A second "new guest" claim (stale flag) is logged as an ordinary click.
        visit = record_source_visit(
            partner_id=self.partner.id, guest_id=guest.id, payload="src_flyer", is_new_guest=True
        )
        self.assertFalse(visit.is_new_guest)
        self.assertEqual(visit.source, flyer)
        with self.assertRaises(IntegrityError), transaction.atomic():
            SourceVisit.objects.create(
                partner=self.partner, source=flyer, guest=guest, is_new_guest=True
            )

    def test_inactive_unknown_and_foreign_sources_are_ignored(self):
        guest = _guest(self.partner, 610003)
        TrafficSource.objects.create(partner=self.partner, name="Old", code="old", is_active=False)
        foreign = _partner("foreign-venue")
        TrafficSource.objects.create(partner=foreign, name="Theirs", code="theirs")
        for payload in ("src_old", "src_nothing", "src_theirs", "src_", "table_1_x"):
            self.assertIsNone(
                record_source_visit(
                    partner_id=self.partner.id,
                    guest_id=guest.id,
                    payload=payload,
                    is_new_guest=True,
                )
            )
        self.assertFalse(SourceVisit.objects.exists())

    def test_guest_of_another_partner_is_refused(self):
        foreign_guest = _guest(_partner("guest-elsewhere"), 610004)
        with self.assertRaises(GuestProfile.DoesNotExist):
            record_source_visit(
                partner_id=self.partner.id,
                guest_id=foreign_guest.id,
                payload="src_insta",
                is_new_guest=True,
            )


class StartWithSourceTests(TestCase):
    token = "source-start-token"

    def setUp(self):
        self.partner = _partner("start-src-venue")
        bot_instance = BotInstance.objects.create(
            partner=self.partner,
            display_name="Src Bot",
            username="start_src_bot",
            token_encrypted="",
        )
        register_runtime_configs(
            [
                BotRuntimeConfig(
                    partner_id=str(self.partner.id),
                    bot_instance_id=str(bot_instance.id),
                    username=bot_instance.username,
                    token=self.token,
                )
            ]
        )
        self.source = TrafficSource.objects.create(
            partner=self.partner, name="Instagram", code="insta"
        )
        self.bot = SimpleNamespace(token=self.token)

    def tearDown(self):
        register_runtime_configs([])

    def _start(self, telegram_id: int, args: str):
        message = SimpleNamespace(
            from_user=SimpleNamespace(
                id=telegram_id, username="", first_name="Guest", last_name="", language_code=""
            ),
            answer=AsyncMock(),
        )
        async_to_sync(start_handler)(message, SimpleNamespace(args=args), self.bot)
        return message.answer.await_args.args[0]

    def test_source_link_welcomes_the_guest_and_records_the_visit(self):
        text = self._start(620001, "src_insta")
        self.assertIn("Ваш код клиента", text)
        visit = SourceVisit.objects.get()
        self.assertTrue(visit.is_new_guest)
        self.assertEqual(visit.source, self.source)

        self._start(620001, "src_insta")
        self.assertEqual(SourceVisit.objects.filter(is_new_guest=False).count(), 1)

    def test_existing_guest_is_not_counted_as_brought_by_the_source(self):
        self._start(620002, "")
        self._start(620002, "src_insta")
        self.assertFalse(SourceVisit.objects.get().is_new_guest)

    def test_unknown_source_link_still_gets_a_normal_welcome(self):
        text = self._start(620003, "src_unknown")
        self.assertIn("Ваш код клиента", text)
        self.assertFalse(SourceVisit.objects.exists())


class TrafficSourceAdminTests(TestCase):
    def setUp(self):
        self.partner = _partner("admin-src-venue")
        BotInstance.objects.create(
            partner=self.partner,
            display_name="Admin Src Bot",
            username="admin_src_bot",
            token_encrypted="",
            is_active=True,
        )
        self.owner = User.objects.create_user(
            username="src_owner",
            password="pass12345",
            partner=self.partner,
            role=User.Role.OWNER,
            is_staff=True,
        )
        AdminAccessProfile.objects.create(
            user=self.owner,
            partner=self.partner,
            preset=AdminAccessPreset.OWNER,
            is_partner_owner=True,
            can_access_admin=True,
        )
        self.source = TrafficSource.objects.create(
            partner=self.partner, name="Instagram", code="insta"
        )
        other = _partner("admin-src-other")
        TrafficSource.objects.create(partner=other, name="Чужой источник", code="theirs")

        old_guest = _guest(self.partner, 630001)
        new_guest = _guest(self.partner, 630002)
        old_visit = record_source_visit(
            partner_id=self.partner.id,
            guest_id=old_guest.id,
            payload="src_insta",
            is_new_guest=True,
        )
        SourceVisit.objects.filter(id=old_visit.id).update(
            created_at=timezone.now() - timedelta(days=40)
        )
        record_source_visit(
            partner_id=self.partner.id,
            guest_id=new_guest.id,
            payload="src_insta",
            is_new_guest=True,
        )
        record_source_visit(
            partner_id=self.partner.id,
            guest_id=new_guest.id,
            payload="src_insta",
            is_new_guest=False,
        )
        self.client.force_login(self.owner)
        self.url = reverse("admin:analytics_trafficsource_changelist")

    def test_changelist_shows_link_counts_and_only_own_sources(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        page = response.content.decode()
        self.assertIn("https://t.me/admin_src_bot?start=src_insta", page)
        self.assertNotIn("Чужой источник", page)
        row = response.context["cl"].result_list.get(id=self.source.id)
        self.assertEqual((row.clicks, row.new_guests), (3, 2))

    def test_period_filter_limits_the_counts(self):
        response = self.client.get(self.url, {"period": "30"})
        row = response.context["cl"].result_list.get(id=self.source.id)
        self.assertEqual((row.clicks, row.new_guests), (2, 1))

    def test_owner_creates_a_source_with_a_generated_code(self):
        response = self.client.post(
            reverse("admin:analytics_trafficsource_add"),
            {"name": "Листовка у входа", "code": "", "is_active": "on", "note": ""},
        )
        self.assertEqual(response.status_code, 302)
        created = TrafficSource.objects.get(name="Листовка у входа")
        self.assertEqual(created.partner, self.partner)
        self.assertRegex(created.code, r"^[a-z0-9]{8}$")

    def test_duplicate_code_is_a_form_error(self):
        response = self.client.post(
            reverse("admin:analytics_trafficsource_add"),
            {"name": "Ещё Instagram", "code": "INSTA", "is_active": "on", "note": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Источник с таким кодом уже есть", response.content.decode())
        self.assertEqual(TrafficSource.objects.filter(partner=self.partner).count(), 1)

    def test_code_cannot_change_after_creation(self):
        self.client.post(
            reverse("admin:analytics_trafficsource_change", args=[self.source.id]),
            {"name": "Instagram stories", "code": "changed", "is_active": "on", "note": ""},
        )
        self.source.refresh_from_db()
        self.assertEqual((self.source.name, self.source.code), ("Instagram stories", "insta"))

    def test_visit_log_is_read_only_and_scoped(self):
        url = reverse("admin:analytics_sourcevisit_changelist")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["cl"].result_count, 3)
        self.assertNotIn("Чужой источник", response.content.decode())
        visit = SourceVisit.objects.first()
        change = self.client.post(
            reverse("admin:analytics_sourcevisit_change", args=[visit.id]),
            {"is_new_guest": ""},
        )
        self.assertIn(change.status_code, (302, 403))
        visit.refresh_from_db()
        self.assertEqual(SourceVisit.objects.filter(is_new_guest=True).count(), 2)

    def test_manager_preset_can_view_but_not_create(self):
        manager = User.objects.create_user(
            username="src_manager",
            password="pass12345",
            partner=self.partner,
            role=User.Role.MANAGER,
            is_staff=True,
        )
        AdminAccessProfile.objects.create(
            user=manager,
            partner=self.partner,
            preset=AdminAccessPreset.MANAGER,
            can_access_admin=True,
        )
        self.client.force_login(manager)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("admin:analytics_trafficsource_add")).status_code, 403
        )
