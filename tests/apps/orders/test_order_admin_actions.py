"""Buttons of an order in Admin (admin step 3, owner 2026-11-10, PQ-050).

The same actions as the bot's order card and the Mini App
(``staff_order_actions``), through the same services: a press is a POST,
checked again on the server; the services decide who may confirm money."""

from django.contrib.messages import get_messages
from django.urls import reverse

from apps.billing.staff_actions import staff_order_actions
from apps.orders.models import Order
from apps.partners.models import PartnerBotSettings
from apps.users.constants import AdminAccessPreset, AdminSection
from apps.users.models import AdminAccessProfile, AdminSectionPermission, User
from tests.apps.analytics.test_dashboard import order as plain_order
from tests.webapp.test_guest_api import make_venue
from tests.webapp.test_staff_api import StaffFixture


def admin_access(user, preset, *, owner=False, view_only=()):
    user.is_staff = True
    user.language = "ru"
    user.save()
    profile = AdminAccessProfile.objects.create(
        user=user, partner=user.partner, preset=preset, is_partner_owner=owner,
        can_access_admin=True,
    )
    for section in view_only:
        AdminSectionPermission.objects.create(profile=profile, section=section, can_view=True)
    return profile


class OrderAdminFixture(StaffFixture):
    def setUp(self):
        super().setUp()
        admin_access(self.owner.user, AdminAccessPreset.OWNER, owner=True)
        self.client.force_login(self.owner.user)

    def page(self, order):
        response = self.client.get(reverse("admin:orders_order_change", args=[order.pk]))
        self.assertEqual(response.status_code, 200)
        return response

    def press(self, order, code, **data):
        return self.client.post(
            reverse("admin:orders_order_act", args=[order.pk]),
            {"object_action": code, **data},
        )

    @staticmethod
    def notices(response) -> list[str]:
        return [str(message) for message in get_messages(response.wsgi_request)]


class OrderButtonsTests(OrderAdminFixture):
    def test_the_same_buttons_as_the_bot_for_every_kind_of_order(self):
        PartnerBotSettings.objects.filter(partner=self.partner).update(remote_unpaid_orders_limit=0)
        cases = [
            self.place(),
            self.place(payment="on_receipt"),
            self.place(channel="delivery", payment="on_receipt", address="Хрещатик 1"),
            self.place(channel="shipping", carrier="nova_poshta", destination="Київ 3"),
        ]
        for order in cases:
            for _step in range(4):
                order = Order.objects.get(id=order.id)
                shown = [action.code for action in self.page(order).context["object_actions"]]
                self.assertEqual(shown, staff_order_actions(order), order.channel)
                forward = [code for code in shown
                           if code not in ("transfer_no", "receipt_no", "status:canceled")]
                if not forward:
                    break
                self.press(order, forward[0], tracking="20450000000000")

    def test_transfer_then_statuses_to_the_end(self):
        order = self.place()
        response = self.page(order)
        self.assertContains(response, "Что сделать")
        self.assertContains(response, "Оплата получена")
        response = self.press(order, "transfer_ok")
        self.assertRedirects(response, reverse("admin:orders_order_change", args=[order.pk]))
        order.refresh_from_db()
        self.assertEqual((order.status, order.paid_at is not None), ("accepted", True))
        for code, status in (("status:ready", "ready"), ("status:completed", "completed")):
            self.press(order, code)
            order.refresh_from_db()
            self.assertEqual(order.status, status)
        self.assertEqual(self.page(order).context["object_actions"], [])

    def test_a_press_from_an_out_of_date_page_does_nothing(self):
        order = self.place(payment="on_receipt")
        self.press(order, "status:accepted")
        response = self.press(order, "status:accepted")
        self.assertIn(
            "Это действие уже недоступно — страница устарела. Посмотрите, что изменилось.",
            self.notices(response),
        )
        response = self.press(order, "fly")
        order.refresh_from_db()
        self.assertEqual(order.status, "accepted")

    def test_only_a_post_and_only_this_venues_order(self):
        order = self.place()
        url = reverse("admin:orders_order_act", args=[order.pk])
        self.assertEqual(self.client.get(url, {"object_action": "transfer_ok"}).status_code, 405)
        foreign = plain_order(make_venue("Other"), channel=Order.Channel.TABLE)
        self.assertEqual(self.press(foreign, "status:accepted").status_code, 404)
        foreign.refresh_from_db()
        self.assertEqual(foreign.status, "new")

    def test_the_service_decides_who_confirms_money(self):
        # A waiter given Admin rights to change orders: money checks are still
        # for the owner, a manager or a cashier (the service decides).
        waiter = self.waiter.user
        admin_access(waiter, AdminAccessPreset.CASHIER)
        self.client.force_login(waiter)
        order = self.place()
        response = self.press(order, "transfer_ok")
        self.assertIn("Подтверждать оплату может владелец, менеджер или кассир.",
                      self.notices(response))
        order.refresh_from_db()
        self.assertIsNone(order.paid_at)

    def test_a_parcel_is_sent_with_its_tracking_number(self):
        order = self.place(channel="shipping", carrier="nova_poshta", destination="Київ 3")
        self.press(order, "transfer_ok")
        self.press(order, "status:ready")
        order.refresh_from_db()
        ship = next(action for action in self.page(order).context["object_actions"]
                    if action.code == "ship")
        self.assertEqual((ship.field, str(ship.field_label)), ("tracking", "Номер ТТН"))
        response = self.press(order, "ship", tracking="")
        self.assertTrue(self.notices(response))
        order.refresh_from_db()
        self.assertEqual(order.status, "ready")
        self.press(order, "ship", tracking="20450000000000")
        order.refresh_from_db()
        self.assertEqual((order.status, order.tracking_number), ("delivering", "20450000000000"))

    def test_money_on_receipt_and_the_refusal(self):
        order = self.place(payment="on_receipt")
        self.press(order, "receipt_card")
        order.refresh_from_db()
        self.assertIsNotNone(order.paid_at)
        second = self.place(payment="on_receipt")
        refusal = next(action for action in self.page(second).context["object_actions"]
                       if action.code == "receipt_no")
        self.assertEqual(refusal.style, "danger")
        self.assertTrue(refusal.confirm)
        self.press(second, "receipt_no")
        second.refresh_from_db()
        self.assertEqual(second.status, "canceled")


class OrderListTests(OrderAdminFixture):
    def test_the_next_step_from_the_list(self):
        order = self.place(payment="on_receipt")
        listed = self.client.get(reverse("admin:orders_order_changelist"))
        act = reverse("admin:orders_order_act", args=[order.pk]) + "?from=list"
        self.assertContains(listed, f'formaction="{act}"')
        # Not with the guest yet: the order's own step comes before the money.
        self.assertContains(listed, 'value="status:accepted"')
        # Refusals stay on the order's page.
        self.assertNotContains(listed, 'value="receipt_no"')
        back = reverse("admin:orders_order_changelist") + "?status__exact=new"
        response = self.client.post(act, {"object_action": "status:accepted"},
                                    HTTP_REFERER="http://testserver" + back)
        self.assertRedirects(response, "http://testserver" + back, fetch_redirect_response=False)
        order.refresh_from_db()
        self.assertEqual(order.status, "accepted")
        # Ready at the counter: now the money first.
        self.press(order, "status:ready")
        listed = self.client.get(reverse("admin:orders_order_changelist"))
        self.assertContains(listed, 'value="receipt_cash"')

    def test_a_parcel_opens_its_page_for_the_tracking_number(self):
        order = self.place(channel="shipping", carrier="nova_poshta", destination="Київ 3")
        self.press(order, "transfer_ok")
        self.press(order, "status:ready")
        listed = self.client.get(reverse("admin:orders_order_changelist"))
        self.assertContains(
            listed, f'href="{reverse("admin:orders_order_change", args=[order.pk])}"'
        )
        self.assertNotContains(listed, 'value="ship"')


class ViewOnlyTests(OrderAdminFixture):
    def test_no_buttons_without_the_right_to_change(self):
        viewer = User.objects.create_user(username="order_viewer", password="x",
                                          partner=self.partner, role=User.Role.MANAGER)
        admin_access(viewer, AdminAccessPreset.CUSTOM, view_only=[AdminSection.ORDERS])
        self.client.force_login(viewer)
        order = self.place()
        response = self.page(order)
        self.assertNotIn("object_actions", response.context)
        listed = self.client.get(reverse("admin:orders_order_changelist"))
        self.assertNotIn("next_step", listed.context["cl"].list_display)
        self.assertEqual(self.press(order, "transfer_ok").status_code, 403)
