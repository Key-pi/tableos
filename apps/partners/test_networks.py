"""Networks of venues, stage 1 (PQ-047): one Admin login for several venues,
a venue switch in the header, the network report; each venue's data stays
its own."""

from datetime import timedelta
from decimal import Decimal

from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.analytics.models import PartnerDailyMetric, TrafficSource
from apps.billing.services import user_can_adjust_bills
from apps.employees.invites import roles_invitable_by
from apps.employees.models import EmployeeProfile
from apps.partners.admin_network import header_badge, site_dropdown, site_subheader
from apps.partners.models import Network, Partner
from apps.partners.networks import acts_for_partner, admin_partner_ids
from apps.users.constants import AdminAccessPreset
from apps.users.models import AdminAccessProfile, User
from core.admin_mixins import ADMIN_POINT_SESSION_KEY


def venue(name, network=None):
    partner = Partner.objects.create(
        name=name, slug=name.lower().replace(" ", "-"), status=Partner.Status.ACTIVE,
        network=network,
    )
    TrafficSource.objects.create(partner=partner, name=f"Источник {name}", code=partner.slug[:8])
    return partner


def admin_user(username, partner, *, owner, extra=()):
    user = User.objects.create_user(
        username=username, password="pass12345", partner=partner,
        role=User.Role.OWNER if owner else User.Role.MANAGER, is_staff=True,
    )
    profile = AdminAccessProfile.objects.create(
        user=user, partner=partner,
        preset=AdminAccessPreset.OWNER if owner else AdminAccessPreset.MANAGER,
        is_partner_owner=owner, can_access_admin=True,
    )
    profile.extra_partners.set(extra)
    return user


class NetworkFixture(TestCase):
    def setUp(self):
        self.network = Network.objects.create(name="Кава Мережа")
        self.a = venue("Point A", self.network)
        self.b = venue("Point B", self.network)
        self.c = venue("Point C", self.network)
        other_network = Network.objects.create(name="Чужая сеть")
        self.e = venue("Point E", other_network)
        self.alone = venue("Alone Cafe")
        self.owner = admin_user("net_owner", self.a, owner=True)
        self.manager = admin_user("net_manager", self.a, owner=False, extra=[self.b, self.e])
        self.stranger = admin_user("e_owner", self.e, owner=True)
        self.alone_owner = admin_user("alone_owner", self.alone, owner=True)


class WhoOpensWhatTests(NetworkFixture):
    def test_owner_opens_every_venue_of_the_network_their_own_first(self):
        self.assertEqual(admin_partner_ids(self.owner), [self.a.id, self.b.id, self.c.id])

    def test_manager_opens_only_given_venues_of_the_same_network(self):
        # Point E was given too, but it is another network: it does not count.
        self.assertEqual(admin_partner_ids(self.manager), [self.a.id, self.b.id])
        self.assertTrue(acts_for_partner(self.manager, self.b.id))
        self.assertFalse(acts_for_partner(self.manager, self.c.id))
        self.assertFalse(acts_for_partner(self.manager, self.e.id))

    def test_a_venue_without_a_network_is_alone(self):
        self.assertEqual(admin_partner_ids(self.alone_owner), [self.alone.id])

    def test_a_venue_moved_to_another_network_stops_counting_at_once(self):
        Partner.objects.filter(id=self.b.id).update(network=self.e.network)
        self.assertEqual(admin_partner_ids(self.owner), [self.a.id, self.c.id])
        self.assertEqual(admin_partner_ids(self.manager), [self.a.id])

    def test_switched_off_access_opens_nothing(self):
        AdminAccessProfile.objects.filter(user=self.owner).update(is_active=False)
        self.assertEqual(admin_partner_ids(self.owner), [])
        self.assertFalse(acts_for_partner(self.owner, self.b.id))


class AdminSwitchTests(NetworkFixture):
    sources = reverse("admin:analytics_trafficsource_changelist")

    def names(self):
        response = self.client.get(self.sources)
        self.assertEqual(response.status_code, 200)
        return {source.name for source in response.context["cl"].result_list}

    def switch(self, partner):
        return self.client.get(reverse("admin-point", args=[partner.id.hex]))

    def test_owner_switches_and_sees_one_venue_at_a_time(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.names(), {"Источник Point A"})
        response = self.switch(self.b)
        self.assertRedirects(response, reverse("admin:index"), fetch_redirect_response=False)
        self.assertEqual(self.names(), {"Источник Point B"})

    def test_nobody_opens_a_venue_they_were_not_given(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.switch(self.c).status_code, 404)
        self.assertEqual(self.switch(self.e).status_code, 404)
        self.assertEqual(self.names(), {"Источник Point A"})
        self.assertEqual(self.switch(self.b).status_code, 302)
        self.assertEqual(self.names(), {"Источник Point B"})

    def test_a_forged_or_outdated_choice_falls_back_to_the_own_venue(self):
        self.client.force_login(self.manager)
        session = self.client.session
        session[ADMIN_POINT_SESSION_KEY] = str(self.e.id)
        session.save()
        self.assertEqual(self.names(), {"Источник Point A"})
        self.switch(self.b)
        AdminAccessProfile.objects.get(user=self.manager).extra_partners.clear()
        self.assertEqual(self.names(), {"Источник Point A"})

    def test_an_object_of_another_venue_is_not_found_after_switching(self):
        self.client.force_login(self.owner)
        source_a = TrafficSource.objects.get(partner=self.a)
        self.switch(self.b)
        response = self.client.get(
            reverse("admin:analytics_trafficsource_change", args=[source_a.id])
        )
        self.assertIn(response.status_code, (302, 404))
        self.assertNotIn("Источник Point A", response.content.decode())

    def test_a_new_record_belongs_to_the_venue_now_open(self):
        self.client.force_login(self.owner)
        self.switch(self.c)
        self.client.post(
            reverse("admin:analytics_trafficsource_add"),
            {"name": "Листовка", "code": "", "is_active": "on", "note": ""},
        )
        self.assertEqual(TrafficSource.objects.get(name="Листовка").partner, self.c)

    def test_the_header_lists_the_venues_and_marks_the_open_one(self):
        request = RequestFactory().get("/admin/")
        request.user = self.owner
        request.session = {ADMIN_POINT_SESSION_KEY: str(self.b.id)}
        items = site_dropdown(request)
        self.assertEqual(
            [item["title"] for item in items], ["Point A", "✓ Point B", "Point C"]
        )
        self.assertEqual(site_subheader(request), "Point B")
        self.assertEqual(header_badge(request), ["📍 Point B", "info"])
        alone = RequestFactory().get("/admin/")
        alone.user, alone.session = self.alone_owner, {}
        self.assertEqual(site_dropdown(alone), [])
        self.assertEqual(site_subheader(alone), "")
        self.assertIsNone(header_badge(alone))


class ActingForAVenueTests(NetworkFixture):
    def test_bill_changes(self):
        # The owner of the network: every venue of it.
        self.assertTrue(user_can_adjust_bills(self.owner, self.b.id))
        self.assertFalse(user_can_adjust_bills(self.owner, self.e.id))
        # A manager: the same right as at home, only in the given venues.
        EmployeeProfile.objects.create(
            partner=self.a, user=self.manager, can_adjust_bills=True
        )
        self.assertTrue(user_can_adjust_bills(self.manager, self.b.id))
        self.assertFalse(user_can_adjust_bills(self.manager, self.c.id))
        self.assertFalse(user_can_adjust_bills(self.stranger, self.a.id))

    def test_staff_invites(self):
        self.assertTrue(roles_invitable_by(self.owner, self.c.id))
        self.assertTrue(roles_invitable_by(self.manager, self.b.id))
        self.assertEqual(roles_invitable_by(self.manager, self.c.id), [])


class NetworkReportTests(NetworkFixture):
    def setUp(self):
        super().setUp()
        today = timezone.localdate()
        for partner, revenue, sales, days_ago in (
            (self.a, "1000.00", 10, 0),
            (self.a, "500.00", 4, 10),
            (self.b, "300.00", 3, 1),
            (self.c, "700.00", 7, 0),
            (self.e, "9999.00", 99, 0),
        ):
            PartnerDailyMetric.objects.create(
                partner=partner, bucket_date=today - timedelta(days=days_ago),
                revenue=Decimal(revenue), sales_count=sales, orders_count=1,
            )

    def report(self, user, **params):
        self.client.force_login(user)
        response = self.client.get(reverse("admin:partners_network_report"), params)
        return response

    def rows(self, response):
        return {
            (row.partner.name if row.partner else "network"): (row.revenue, row.sales)
            for row in response.context["rows"]
        }

    def test_owner_sees_every_venue_and_the_total(self):
        response = self.report(self.owner, days=7)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.rows(response),
            {
                "Point A": (Decimal("1000.00"), 10),
                "Point B": (Decimal("300.00"), 3),
                "Point C": (Decimal("700.00"), 7),
                "network": (Decimal("2000.00"), 20),
            },
        )
        self.assertEqual(self.rows(self.report(self.owner, days=30))["Point A"][1], 14)

    def test_the_average_check_is_per_sale_and_old_days_still_count(self):
        # A day counted before sales were stored: 400 at 100 per sale = 4.
        PartnerDailyMetric.objects.create(
            partner=self.b, bucket_date=timezone.localdate(),
            revenue=Decimal("400.00"), average_check=Decimal("100.00"),
        )
        rows = {row.partner.name if row.partner else "network": row
                for row in self.report(self.owner).context["rows"]}
        self.assertEqual((rows["Point B"].sales, rows["Point B"].average_check),
                         (7, Decimal("100.00")))
        self.assertEqual(rows["network"].average_check, Decimal("100.00"))  # 2400 / 24

    def test_manager_sees_only_given_venues(self):
        self.assertEqual(
            set(self.rows(self.report(self.manager))), {"Point A", "Point B", "network"}
        )

    def test_a_single_venue_has_no_network_report(self):
        self.assertEqual(self.report(self.alone_owner).status_code, 403)



def form_data(response):
    """The data of the change form as it is shown, ready to post back."""

    form = response.context["adminform"].form
    data = {}
    for name in form.fields:
        value = form[name].value()
        if isinstance(value, bool):
            if value:
                data[name] = "on"
        elif isinstance(value, (list, tuple)):
            data[name] = [str(item) for item in value]
        elif value is not None:
            data[name] = str(value)
    return data


class VenueSettingsInAdminTests(NetworkFixture):
    """The owner of a network sets up each venue from one login (PQ-047)."""

    def setUp(self):
        super().setUp()
        from apps.partners.models import PartnerBotSettings
        from apps.users.models import TelegramAccount

        self.settings_a = PartnerBotSettings.objects.create(
            partner=self.a, transfer_payment_details="IBAN A"
        )
        self.settings_b = PartnerBotSettings.objects.create(
            partner=self.b, transfer_payment_details="IBAN B"
        )
        EmployeeProfile.objects.create(
            partner=self.a, user=self.owner,
            telegram_account=TelegramAccount.objects.create(telegram_id=9920001),
        )
        self.client.force_login(self.owner)
        self.client.get(reverse("admin-point", args=[self.b.id.hex]))

    def test_the_open_venue_card_with_its_address(self):
        url = reverse("admin:partners_partner_change", args=[self.b.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = form_data(response)
        self.assertIn("address", response.context["adminform"].form.fields)
        data.update(name="Point B", address="Харків, Сумська 1", contact_phone="+380501112233")
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.b.refresh_from_db()
        self.a.refresh_from_db()
        self.assertEqual(self.b.address, "Харків, Сумська 1")
        self.assertEqual(self.a.address, "")
        # The venue of another network still does not open.
        other = self.client.get(reverse("admin:partners_partner_change", args=[self.e.id]))
        self.assertNotEqual(other.status_code, 200)

    def test_the_open_venue_bot_settings_and_the_owner_is_told(self):
        from apps.notifications.models import StaffNotification

        url = reverse("admin:partners_partnerbotsettings_change", args=[self.settings_b.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = form_data(response)
        self.assertIn("transfer_payment_details", data)
        data["transfer_payment_details"] = "IBAN B NEW"
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.settings_a.refresh_from_db()
        self.settings_b.refresh_from_db()
        self.assertEqual(self.settings_b.transfer_payment_details, "IBAN B NEW")
        self.assertEqual(self.settings_a.transfer_payment_details, "IBAN A")
        # The network's owner works in Point B through their copy and is told.
        alert = StaffNotification.objects.get(partner=self.b)
        self.assertEqual(alert.employee.telegram_account.telegram_id, 9920001)
        # Point A's settings do not open while Point B is open.
        response = self.client.get(
            reverse("admin:partners_partnerbotsettings_change", args=[self.settings_a.id])
        )
        self.assertNotEqual(response.status_code, 200)

    def test_opening_hours_go_to_the_open_venue(self):
        from apps.partners.models import OpeningHours

        response = self.client.post(
            reverse("admin:partners_openinghours_add"),
            {"scope": OpeningHours.Scope.COMMON, "weekday": "0",
             "opens_at": "10:00", "closes_at": "22:00"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(OpeningHours.objects.get().partner, self.b)

    def test_copies_of_network_staff_stay_out_of_the_venue_users(self):
        copy = EmployeeProfile.objects.get(partner=self.b, mirrored_from__isnull=False)
        users = self.client.get(reverse("admin:users_user_changelist"))
        self.assertNotIn(copy.user, users.context["cl"].result_list)
        opened = self.client.get(reverse("admin:users_user_change", args=[copy.user.id]))
        self.assertNotEqual(opened.status_code, 200)
        add_access = self.client.get(reverse("admin:users_adminaccessprofile_add"))
        choices = add_access.context["adminform"].form.fields["user"].queryset
        self.assertNotIn(copy.user, choices)
        add_staff = self.client.get(reverse("admin:employees_employeeprofile_add"))
        self.assertNotIn(copy.user, add_staff.context["adminform"].form.fields["user"].queryset)
