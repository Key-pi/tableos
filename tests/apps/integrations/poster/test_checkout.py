"""Checkout while the venue works with Poster (PQ-058, step 7): pickup and
delivery only on receipt and without bonuses (v1, owner 2026-11-20), and
the venue's choice «Меню в боте» (owner 2026-11-21): only Poster's menu — the
guest does not see Botera's own items and add-ons where orders go to the
register — or Poster's menu with Botera's own items, whose orders do not go
to Poster and reach staff as «не дошёл до Poster», as before."""

from decimal import Decimal
from unittest import mock

from django.contrib import admin
from django.test import RequestFactory, TestCase

from apps.billing.remote_orders import (
    RemoteOrderError,
    get_remote_quote,
    payment_modes_for,
    place_remote_order,
)
from apps.bonuses.models import BonusProgram
from apps.integrations.poster import orders, selectors
from apps.integrations.poster.admin import PosterConnectionAdmin
from apps.integrations.poster.models import PosterConnection, PosterOrderPush, PosterRef
from apps.integrations.poster.services import PosterConnectError, set_menu_mode
from apps.menu.catalog import not_sold_in, sold_in_channels
from apps.menu.models import MenuItem, MenuItemVariant, Modifier, ModifierGroup
from apps.menu.options import ChoiceError, choices_for, resolve_choice
from apps.menu.repositories import MenuItemRepository
from apps.orders.models import Order
from apps.orders.services import (
    OrderFlowError,
    add_items_to_cart_for_telegram_user,
    add_menu_item_for_telegram_user,
    switch_cart_channel_for_telegram_user,
)
from apps.partners.models import PartnerBotSettings
from apps.users.constants import AdminAccessPreset
from apps.users.models import AdminAccessProfile, User
from tests.apps.billing.test_remote_orders import NOON, TG
from tests.apps.integrations.poster.test_orders import OrderSetup

Mode = PosterConnection.MenuMode
Kind = PosterRef.Kind
PICKUP, DELIVERY, TABLE = Order.Channel.PICKUP, Order.Channel.DELIVERY, Order.Channel.TABLE
PREPAID, ON_RECEIPT = Order.PaymentTiming.PREPAID, Order.PaymentTiming.ON_RECEIPT


class CheckoutFixture(OrderSetup, TestCase):
    """«Бургер» is Poster's product 1; «Морс» is Botera's own item."""

    def setUp(self):
        super().setUp()
        PartnerBotSettings.objects.filter(id=self.settings.id).update(
            pickup_pay_transfer=True, delivery_pay_transfer=True, module_options_enabled=True,
        )
        self.settings.refresh_from_db()
        self.own = MenuItem.objects.create(
            partner=self.partner, category=self.item.category, public_id="OWN00001",
            name="Морс", price="50.00", sort_order=2,
        )
        # The guest chose pickup (the bot's first step).
        switch_cart_channel_for_telegram_user(
            partner_id=self.partner.id, telegram_id=TG, channel=PICKUP,
        )

    def mode(self, mode):
        PosterConnection.objects.filter(pk=self.connection.pk).update(menu_mode=mode)

    def disconnect(self):
        PosterConnection.objects.filter(pk=self.connection.pk).update(
            status=PosterConnection.Status.DISCONNECTED,
        )

    def quote(self, *, use_bonuses=False, channel=PICKUP):
        with mock.patch("apps.billing.remote_orders.timezone.now", return_value=NOON):
            return get_remote_quote(
                partner_id=self.partner.id, telegram_id=TG, use_bonuses=use_bonuses,
                channel=channel,
            )

    def checkout(self, *, payment=ON_RECEIPT, use_bonuses=False, total="400.00", to_pay="400.00"):
        with self.captureOnCommitCallbacks(execute=True):
            return place_remote_order(
                partner_id=self.partner.id, telegram_id=TG, slot="now", use_bonuses=use_bonuses,
                expected_total=total, expected_to_pay=to_pay, payment=payment, now=NOON,
            )

    def bonuses(self):
        self._seed_bonuses("100.00")
        BonusProgram.objects.create(
            partner=self.partner, name="Cashback", program_type=BonusProgram.ProgramType.CASHBACK,
            percent="5.00", max_redeem_share="20.00",
        )


class PaymentAndBonusTests(CheckoutFixture):
    def test_checkout_and_the_push_agree_on_what_goes_to_the_register(self):
        self.assertEqual(set(orders.SERVICE_MODES), set(selectors.REGISTER_CHANNELS))

    def test_pickup_and_delivery_through_poster_are_paid_on_receipt_only(self):
        self._cart()

        self.assertEqual(self.quote().payment_modes, (ON_RECEIPT,))
        self.assertEqual(payment_modes_for(self.settings, DELIVERY), (ON_RECEIPT,))

    def test_without_poster_the_transfer_comes_back(self):
        self.disconnect()
        self._cart()

        self.assertEqual(self.quote().payment_modes, (PREPAID, ON_RECEIPT))

    def test_shipping_does_not_go_to_poster_and_keeps_its_options(self):
        PartnerBotSettings.objects.filter(id=self.settings.id).update(
            shipping_pay_transfer=True, shipping_pay_on_receipt=True,
        )
        self.settings.refresh_from_db()

        self.assertEqual(
            payment_modes_for(self.settings, Order.Channel.SHIPPING), (PREPAID, ON_RECEIPT),
        )

    def test_a_transfer_in_advance_is_refused_at_checkout(self):
        self._cart()

        with self.assertRaisesMessage(RemoteOrderError, "Этот способ оплаты сейчас недоступен"):
            self.checkout(payment=PREPAID)
        self.assertFalse(Order.objects.filter(partner=self.partner).exists())

    def test_with_only_the_transfer_on_pickup_is_not_set_up(self):
        PartnerBotSettings.objects.filter(id=self.settings.id).update(pickup_pay_on_receipt=False)
        self._cart()

        with self.assertRaisesMessage(RemoteOrderError, "ещё не настроило оплату"):
            self.quote()

    def test_bonuses_are_not_offered(self):
        self.bonuses()
        self._cart()

        quote = self.quote(use_bonuses=True)

        self.assertEqual(quote.bonus_available, Decimal("0.00"))
        self.assertEqual(quote.to_pay, Decimal("400.00"))

    def test_spending_bonuses_is_refused_at_checkout(self):
        self.bonuses()
        self._cart()

        with self.assertRaisesMessage(RemoteOrderError, "Бонусы в этом заказе не списываются"):
            self.checkout(use_bonuses=True, to_pay="320.00")
        self.assertFalse(Order.objects.filter(partner=self.partner).exists())

    def test_without_poster_bonuses_come_back(self):
        self.disconnect()
        self.bonuses()
        self._cart()

        self.assertEqual(self.quote(use_bonuses=True).bonus_available, Decimal("80.00"))

    def test_an_order_paid_on_receipt_goes_to_poster(self):
        self._cart()

        order = self.checkout()

        self.assertEqual(order.payment_timing, ON_RECEIPT)
        self.assertEqual(PosterOrderPush.objects.get(order=order).status,
                         PosterOrderPush.Status.SENT)


class OwnItemsTests(CheckoutFixture):
    def test_poster_only_hides_own_items_where_orders_go_to_the_register(self):
        def names(channel):
            return set(MenuItemRepository.active_for_partner(self.partner.id, channel=channel)
                       .values_list("name", flat=True))

        self.assertEqual(names(PICKUP), {"Бургер"})
        self.assertEqual(names(DELIVERY), {"Бургер"})
        self.assertEqual(names(TABLE), {"Бургер", "Морс"})
        self.assertEqual(names(None), {"Бургер", "Морс"})
        self.assertEqual(not_sold_in([self.item, self.own], PICKUP), {self.own.id})
        self.assertEqual(not_sold_in([self.item, self.own], TABLE), set())

    def test_the_mini_app_sees_where_each_item_is_sold(self):
        channels = sold_in_channels([self.item, self.own])

        self.assertIn(PICKUP, channels[self.item.id])
        self.assertIn(DELIVERY, channels[self.item.id])
        self.assertEqual(channels[self.own.id], [TABLE, Order.Channel.SHIPPING])
        self.mode(Mode.WITH_OWN)
        self.assertIn(PICKUP, sold_in_channels([self.own])[self.own.id])

    def test_an_own_item_is_not_added_for_pickup(self):
        with self.assertRaisesMessage(OrderFlowError, "Недоступно для заказа"):
            add_items_to_cart_for_telegram_user(
                partner_id=self.partner.id, telegram_id=TG, raw_items="OWN00001x1",
            )
        with self.assertRaisesMessage(OrderFlowError, "Недоступно для заказа"):
            add_menu_item_for_telegram_user(
                partner_id=self.partner.id, telegram_id=TG, item_public_id="OWN00001",
            )

    def test_a_cart_filled_before_the_choice_is_checked_again(self):
        self.mode(Mode.WITH_OWN)
        self._cart(1)
        add_items_to_cart_for_telegram_user(
            partner_id=self.partner.id, telegram_id=TG, raw_items="OWN00001x1",
        )
        self.mode(Mode.POSTER)

        with self.assertRaisesMessage(RemoteOrderError, "Недоступно (самовывоз): Морс"):
            self.quote()
        _cart, removed = switch_cart_channel_for_telegram_user(
            partner_id=self.partner.id, telegram_id=TG, channel=DELIVERY,
        )
        self.assertEqual(removed, ["Морс"])

    def test_without_poster_own_items_are_sold_again(self):
        self.disconnect()

        self.assertEqual(not_sold_in([self.own], PICKUP), set())
        add_items_to_cart_for_telegram_user(
            partner_id=self.partner.id, telegram_id=TG, raw_items="OWN00001x1",
        )

    def test_with_own_items_the_order_stays_in_botera_as_before(self):
        self.mode(Mode.WITH_OWN)
        self.assertEqual(not_sold_in([self.own], PICKUP), set())
        self._cart(1)
        add_items_to_cart_for_telegram_user(
            partner_id=self.partner.id, telegram_id=TG, raw_items="OWN00001x1",
        )

        order = self.checkout(total="250.00", to_pay="250.00")

        push = PosterOrderPush.objects.get(order=order)
        self.assertEqual(push.status, PosterOrderPush.Status.FAILED)
        self.assertIn("«Морс» нет в меню Poster", push.last_error)
        self.assertEqual(len(self.staff_messages()), 1)
        self.assertEqual(self.poster.created(), [])


class OwnAddOnsTests(CheckoutFixture):
    def setUp(self):
        super().setUp()
        self.poster_group = ModifierGroup.objects.create(
            partner=self.partner, name="Соус", min_choices=0, max_choices=1,
        )
        self.poster_group.items.add(self.item)
        self.sauce = Modifier.objects.create(
            partner=self.partner, group=self.poster_group, name="Сырный", price="10.00",
        )
        PosterRef.objects.create(partner=self.partner, kind=Kind.MODIFIER_GROUP, poster_id="3",
                                 object_id=self.poster_group.pk)
        PosterRef.objects.create(partner=self.partner, kind=Kind.MODIFIER, poster_id="3:7",
                                 object_id=self.sauce.pk)
        self.own_group = ModifierGroup.objects.create(
            partner=self.partner, name="Подача", min_choices=1, max_choices=1,
        )
        self.own_group.items.add(self.item)
        self.plate = Modifier.objects.create(
            partner=self.partner, group=self.own_group, name="На тарелке", price="0.00",
        )

    def groups(self, channel):
        return [entry.group.name for entry in choices_for(self.item, channel=channel).groups]

    def test_poster_only_offers_poster_add_ons_where_orders_go_to_the_register(self):
        self.assertEqual(self.groups(PICKUP), ["Соус"])
        self.assertEqual(sorted(self.groups(TABLE)), ["Подача", "Соус"])
        self.assertEqual(sorted(self.groups(None)), ["Подача", "Соус"])

    def test_a_required_own_group_holds_nothing_back_there(self):
        choice = resolve_choice(self.item, channel=PICKUP)

        self.assertEqual(choice.modifiers, ())
        with self.assertRaises(ChoiceError):
            resolve_choice(self.item, modifier_ids=[self.plate.id], channel=PICKUP)

    def test_an_own_add_on_is_not_added_for_pickup(self):
        with self.assertRaisesMessage(OrderFlowError, "Этой добавки"):
            add_menu_item_for_telegram_user(
                partner_id=self.partner.id, telegram_id=TG, item_public_id="PICK0001",
                modifier_ids=[self.plate.id],
            )
        cart = add_menu_item_for_telegram_user(
            partner_id=self.partner.id, telegram_id=TG, item_public_id="PICK0001",
            modifier_ids=[self.sauce.id],
        )
        self.assertEqual(cart.items.get().unit_price, Decimal("210.00"))

    def test_an_own_add_on_chosen_before_is_checked_at_checkout(self):
        self.mode(Mode.WITH_OWN)
        add_menu_item_for_telegram_user(
            partner_id=self.partner.id, telegram_id=TG, item_public_id="PICK0001",
            modifier_ids=[self.plate.id],
        )
        self.mode(Mode.POSTER)

        with self.assertRaisesMessage(RemoteOrderError, "больше нельзя заказать так"):
            self.quote()

    def test_only_poster_s_sizes_are_offered_there(self):
        big = MenuItemVariant.objects.create(item=self.item, name="XL", price="250.00")
        PosterRef.objects.create(partner=self.partner, kind=Kind.VARIANT, poster_id="1:5",
                                 object_id=big.pk)
        MenuItemVariant.objects.create(item=self.item, name="Детский", price="150.00")

        def sizes(channel):
            return [variant.name for variant in choices_for(self.item, channel=channel).variants]

        self.assertEqual(sizes(PICKUP), ["XL"])
        self.assertEqual(sorted(sizes(TABLE)), ["XL", "Детский"])

    def test_with_own_items_own_add_ons_are_offered(self):
        self.mode(Mode.WITH_OWN)

        self.assertEqual(sorted(self.groups(PICKUP)), ["Подача", "Соус"])


class MenuModeTests(CheckoutFixture):
    def setUp(self):
        super().setUp()
        self.owner = User.objects.create_user(
            username="poster_menu_owner", password="pass12345", partner=self.partner,
            role=User.Role.OWNER, is_staff=True, language="ru",
        )
        AdminAccessProfile.objects.create(
            user=self.owner, partner=self.partner, preset=AdminAccessPreset.OWNER,
            is_partner_owner=True, can_access_admin=True,
        )

    def actions(self, user):
        request = RequestFactory().get("/admin/")
        request.user, request.session = user, {}
        page = PosterConnectionAdmin(PosterConnection, admin.site)
        return {action.code: action for action in page.get_object_actions(request,
                                                                          self.connection)}

    def test_a_new_connection_shows_only_poster_s_menu(self):
        self.assertEqual(PosterConnection().menu_mode, Mode.POSTER)

    def test_the_owner_switches_the_menu(self):
        connection = set_menu_mode(
            connection_id=self.connection.pk, user=self.owner, mode=Mode.WITH_OWN,
        )

        self.assertEqual(connection.menu_mode, Mode.WITH_OWN)
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.menu_mode, Mode.WITH_OWN)

    def test_staff_do_not_switch_it(self):
        with self.assertRaises(PosterConnectError):
            set_menu_mode(connection_id=self.connection.pk, user=self.cashier, mode=Mode.WITH_OWN)
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.menu_mode, Mode.POSTER)

    def test_an_unknown_mode_is_refused(self):
        with self.assertRaises(PosterConnectError):
            set_menu_mode(connection_id=self.connection.pk, user=self.owner, mode="everything")

    def test_admin_offers_the_other_mode_to_the_owner_only(self):
        self.assertIn("menu:with_own", self.actions(self.owner))
        self.assertNotIn("menu:poster", self.actions(self.owner))
        self.assertEqual(self.actions(self.cashier), {})
        self.mode(Mode.WITH_OWN)
        self.connection.refresh_from_db()
        self.assertIn("menu:poster", self.actions(self.owner))

    def test_the_poster_page_says_what_checkout_offers(self):
        self.client.force_login(self.owner)
        url = f"/admin/poster/posterconnection/{self.connection.pk}/change/"

        response = self.client.get(url, follow=True)

        self.assertContains(response, "оплачиваются при получении и без бонусов")
