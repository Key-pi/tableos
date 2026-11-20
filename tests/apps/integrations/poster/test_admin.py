"""Poster pages in Admin are read-only, scoped to the venue and never show the token
(PQ-058, step 1)."""

from django.contrib import admin
from django.test import RequestFactory, TestCase

from apps.integrations.poster.admin import (
    PosterConnectionAdmin,
    PosterOrderPushAdmin,
    PosterRefAdmin,
    PosterWebhookEventAdmin,
)
from apps.integrations.poster.models import (
    PosterConnection,
    PosterOrderPush,
    PosterRef,
    PosterWebhookEvent,
)
from apps.orders.models import Order
from apps.partners.models import Partner
from apps.users.admin_navigation import sidebar
from apps.users.constants import AdminAccessPreset
from apps.users.models import AdminAccessProfile, GuestProfile, User


class PosterAdminTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.partners = [
            Partner.objects.create(name=slug, slug=slug, status=Partner.Status.ACTIVE)
            for slug in ("poster-admin-a", "poster-admin-b")
        ]
        self.connections = []
        self.pushes = []
        for number, partner in enumerate(self.partners, start=1):
            connection = PosterConnection.objects.create(
                partner=partner, account=f"venue{number}", account_number=str(number), spot_id=1,
            )
            connection.set_access_token(f"{number}:token-placeholder")
            connection.save()
            self.connections.append(connection)
            guest = GuestProfile.objects.create(partner=partner)
            order = Order.objects.create(partner=partner, guest=guest)
            self.pushes.append(PosterOrderPush.objects.create(partner=partner, order=order))
        self.owner = User.objects.create_user(
            username="poster_owner", password="pass12345", partner=self.partners[0],
            role=User.Role.OWNER, is_staff=True,
        )
        AdminAccessProfile.objects.create(
            user=self.owner, partner=self.partners[0], preset=AdminAccessPreset.OWNER,
            is_partner_owner=True, can_access_admin=True,
        )
        self.platform = User.objects.create_superuser(
            username="poster_platform", password="pass12345", email="platform@example.com",
        )

    def _request(self, user):
        request = self.factory.get("/admin/")
        request.user = user
        request.session = {}
        return request

    def test_the_owner_sees_only_their_connection_and_cannot_change_it(self):
        request = self._request(self.owner)
        page = PosterConnectionAdmin(PosterConnection, admin.site)

        self.assertEqual(list(page.get_queryset(request)), [self.connections[0]])
        self.assertTrue(page.has_view_permission(request, self.connections[0]))
        self.assertFalse(page.has_view_permission(request, self.connections[1]))
        self.assertFalse(page.has_add_permission(request))
        self.assertFalse(page.has_change_permission(request, self.connections[0]))
        self.assertFalse(page.has_delete_permission(request, self.connections[0]))

    def test_nobody_sees_the_token(self):
        page = PosterConnectionAdmin(PosterConnection, admin.site)
        for user in (self.owner, self.platform):
            with self.subTest(user=user.username):
                request = self._request(user)
                shown = set(page.get_readonly_fields(request, self.connections[0]))
                shown |= {
                    name for _title, options in page.get_fieldsets(request, self.connections[0])
                    for name in options["fields"]
                }
                shown |= set(page.get_list_display(request))
                leaked = {name for name in shown if "token" in name and "expires" not in name}
                self.assertFalse(leaked)

                response_fields = page.get_form(request, self.connections[0])().fields
                self.assertNotIn("access_token_encrypted", response_fields)

    def test_order_pushes_are_a_read_only_log_of_the_venue(self):
        request = self._request(self.owner)
        page = PosterOrderPushAdmin(PosterOrderPush, admin.site)

        self.assertEqual(list(page.get_queryset(request)), [self.pushes[0]])
        self.assertFalse(page.has_add_permission(request))
        self.assertFalse(page.has_change_permission(request, self.pushes[0]))
        self.assertFalse(page.has_delete_permission(request, self.pushes[0]))

    def test_references_and_webhooks_are_for_the_platform_only(self):
        PosterRef.objects.create(
            partner=self.partners[0], kind=PosterRef.Kind.PRODUCT, poster_id="1",
            object_id="00000000-0000-0000-0000-000000000001",
        )
        PosterWebhookEvent.objects.create(
            partner=self.partners[0], dedupe_key="k1", account="venue1", entity="product",
            entity_id="1", action="changed", event_time=1, payload={},
        )
        owner_request = self._request(self.owner)
        platform_request = self._request(self.platform)
        pages = ((PosterRefAdmin, PosterRef), (PosterWebhookEventAdmin, PosterWebhookEvent))
        for page_class, model in pages:
            with self.subTest(page=page_class.__name__):
                page = page_class(model, admin.site)
                self.assertFalse(page.has_module_permission(owner_request))
                self.assertFalse(page.has_view_permission(owner_request))
                self.assertEqual(page.get_queryset(owner_request).count(), 0)

                self.assertTrue(page.has_view_permission(platform_request))
                self.assertEqual(page.get_queryset(platform_request).count(), 1)
                self.assertFalse(page.has_add_permission(platform_request))
                self.assertFalse(page.has_change_permission(platform_request, model.objects.get()))
                self.assertFalse(page.has_delete_permission(platform_request, model.objects.get()))

    def test_the_settings_menu_shows_poster_only_to_a_connected_venue(self):
        def poster_links(request):
            return [
                item["link"] for group in sidebar(request) for item in group["items"]
                if "/poster/posterconnection/" in item["link"]
            ]

        request = self._request(self.owner)
        request.path = "/admin/"
        self.assertEqual(
            poster_links(request),
            [f"/admin/poster/posterconnection/{self.connections[0].pk}/change/"],
        )

        self.connections[0].delete()
        request = self._request(self.owner)
        request.path = "/admin/"
        self.assertEqual(poster_links(request), [])
