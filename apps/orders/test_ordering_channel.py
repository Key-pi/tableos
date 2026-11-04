"""The guest chooses the way of ordering first (ADR-0009): the menu only shows
until they do, and nothing picks pickup for them silently."""

from django.test import TestCase

from apps.menu.models import MenuCategory, MenuItem
from apps.orders.models import Cart, Order
from apps.orders.services import (
    OrderFlowError,
    add_menu_item_for_telegram_user,
    get_active_cart_for_telegram_user,
    ordering_channel_for_telegram_user,
    switch_cart_channel_for_telegram_user,
)
from apps.partners.models import Partner, PartnerBotSettings
from apps.tables.models import Table
from apps.tables.services import activate_table_session, close_table_session
from apps.users.services import get_or_create_guest_profile

TG = 7710001


class OrderingChannelTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(name="Канал", slug="kanal")
        self.settings = PartnerBotSettings.objects.create(
            partner=self.partner,
            module_menu_enabled=True,
            module_orders_enabled=True,
            module_cart_enabled=True,
            module_tables_enabled=True,
            module_pickup_enabled=True,
            module_delivery_enabled=True,
        )
        category = MenuCategory.objects.create(partner=self.partner, name="Еда")
        MenuItem.objects.create(
            partner=self.partner, category=category, public_id="CHAN0001", name="Суп", price="100"
        )
        get_or_create_guest_profile(partner_id=self.partner.id, telegram_id=TG)

    def _channel(self):
        return ordering_channel_for_telegram_user(partner_id=self.partner.id, telegram_id=TG)

    def _add(self):
        return add_menu_item_for_telegram_user(
            partner_id=self.partner.id, telegram_id=TG, item_public_id="CHAN0001"
        )

    def _sit_down(self, number=1):
        table = Table.objects.create(partner=self.partner, number=number, qr_token=f"chan-{number}")
        return activate_table_session(
            partner_id=self.partner.id, telegram_id=TG, payload=table.deep_link_payload
        )

    def test_nothing_is_chosen_until_the_guest_chooses(self):
        self.assertIsNone(self._channel())
        with self.assertRaisesMessage(OrderFlowError, "Сначала выберите"):
            self._add()
        self.assertFalse(Cart.objects.exists())

    def test_the_chosen_way_is_kept(self):
        switch_cart_channel_for_telegram_user(
            partner_id=self.partner.id, telegram_id=TG, channel=Order.Channel.DELIVERY
        )
        self.assertEqual(self._channel(), Order.Channel.DELIVERY)
        cart = self._add()
        self.assertEqual(cart.channel, Order.Channel.DELIVERY)

    def test_the_only_way_needs_no_choice(self):
        PartnerBotSettings.objects.filter(id=self.settings.id).update(
            module_tables_enabled=False, module_delivery_enabled=False
        )
        self.assertEqual(self._channel(), Order.Channel.PICKUP)
        self.assertEqual(self._add().channel, Order.Channel.PICKUP)

    def test_with_tables_on_a_single_remote_way_is_still_a_choice(self):
        # A guest without a table may be at the venue without scanning yet.
        PartnerBotSettings.objects.filter(id=self.settings.id).update(
            module_delivery_enabled=False
        )
        self.assertIsNone(self._channel())

    def test_at_a_table_the_table_is_the_way(self):
        self._sit_down()
        self.assertEqual(self._channel(), Order.Channel.TABLE)
        self.assertEqual(self._add().channel, Order.Channel.TABLE)

    def test_leaving_the_table_does_not_turn_the_cart_into_pickup(self):
        session = self._sit_down()
        self._add()
        close_table_session(session)

        self.assertIsNone(self._channel())
        cart = get_active_cart_for_telegram_user(partner_id=self.partner.id, telegram_id=TG)
        self.assertEqual(cart.channel, Order.Channel.TABLE)
        self.assertIsNone(cart.table_session_id)
        with self.assertRaisesMessage(OrderFlowError, "Сначала выберите"):
            self._add()

        cart, removed = switch_cart_channel_for_telegram_user(
            partner_id=self.partner.id, telegram_id=TG, channel=Order.Channel.PICKUP
        )
        self.assertEqual(removed, [])
        self.assertEqual(cart.items.get().quantity, 1)

    def test_leaving_the_table_with_one_way_left_keeps_ordering_that_way(self):
        PartnerBotSettings.objects.filter(id=self.settings.id).update(
            module_delivery_enabled=False
        )
        session = self._sit_down()
        self._add()
        PartnerBotSettings.objects.filter(id=self.settings.id).update(
            module_tables_enabled=False
        )
        close_table_session(session)
        self.assertEqual(self._channel(), Order.Channel.PICKUP)
