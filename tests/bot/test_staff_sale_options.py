"""A variant and add-ons in the bot's sale at the counter (catalog step 2,
owner 2026-11-07): chosen as the guest chooses them, sold at the price of the
choice. The sale is an order «На кассе» paid in cash or by card (PQ-054)."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from asgiref.sync import async_to_sync

from apps.billing.models import Payment
from apps.menu.models import MenuItem, MenuItemVariant, Modifier, ModifierGroup
from apps.orders.models import Order
from apps.partners.models import PartnerBotSettings
from bot.handlers import staff
from bot.states.staff_sale import StaffQuickSaleStates
from tests.apps.menu.test_options import options_module
from tests.bot.test_staff_flow import OWNER_TG, StaffFlowFixture, _buttons, _data


class StaffSaleChoiceTests(StaffFlowFixture):
    def setUp(self):
        super().setUp()
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            module_quick_sale_enabled=True
        )
        options_module(self.partner)
        self.item = MenuItem.objects.get(public_id="FLOW0001")  # Сырники 220.00
        MenuItemVariant.objects.create(item=self.item, name="3 шт", price="220.00", sort_order=1)
        MenuItemVariant.objects.create(item=self.item, name="5 шт", price="330.00", sort_order=2)
        sauce = ModifierGroup.objects.create(
            partner=self.partner, name="Соус", min_choices=1, max_choices=1
        )
        Modifier.objects.create(group=sauce, name="Сметана", sort_order=1)
        Modifier.objects.create(group=sauce, name="Варенье", price="20.00", sort_order=2)
        sauce.items.add(self.item)
        self.bot = SimpleNamespace(
            token=self.token, edit_message_text=AsyncMock(), send_message=AsyncMock()
        )
        self.state = FSMContext(
            storage=MemoryStorage(),
            key=StorageKey(bot_id=1, chat_id=OWNER_TG, user_id=OWNER_TG),
        )
        async_to_sync(self.state.set_state)(StaffQuickSaleStates.browsing_menu)
        async_to_sync(self.state.set_data)({
            "preview_chat_id": OWNER_TG, "preview_message_id": 10, "customer_code": "",
            "items": {}, "selected_category_id": str(self.item.category_id), "comment": "",
            "redeem_bonus": False, "sale_key": "draft-key-0001",
        })

    def press(self, handler, data):
        callback = self._staff_callback(data)
        async_to_sync(handler)(callback, self.bot, self.state)
        return callback

    def sale_screen(self):
        call = self.bot.edit_message_text.call_args
        return call.kwargs["text"], call.kwargs["reply_markup"]

    def items(self):
        return async_to_sync(self.state.get_data)()["items"]

    def test_choose_add_and_sell(self):
        _text, keyboard, _category = staff._compose_staff_sale_view(
            partner_id=self.partner.id, customer_code="", items_map={},
            selected_category_id=str(self.item.category_id),
        )
        opener = next(b for b in _buttons(keyboard) if b.callback_data == "so:FLOW0001")
        self.assertEqual(opener.text, "➕ Сырники — от 220.00 грн")

        callback = self.press(staff.staff_sale_choice_handler, "so:FLOW0001")
        _text, picker = self._screen(callback)
        self.assertIn("staffsale:pback", _data(picker))
        five = next(b for b in _buttons(picker) if b.text.startswith("○ 5 шт")).callback_data
        _text, picker = self._screen(self.press(staff.staff_sale_choice_handler, five))
        jam = next(b for b in _buttons(picker) if b.text.startswith("○ Варенье")).callback_data
        _text, picker = self._screen(self.press(staff.staff_sale_choice_handler, jam))
        add = next(b for b in _buttons(picker) if b.text.startswith("➕ Добавить"))
        self.assertEqual(add.text, "➕ Добавить · 350.00 грн")
        self.press(staff.staff_sale_choice_add_handler, add.callback_data)

        text, keyboard = self.sale_screen()
        self.assertIn("Сырники, 5 шт (Варенье)", text)
        self.assertIn("staffsale:ladd:0", _data(keyboard))
        self.press(staff.staff_sale_line_quantity_handler, "staffsale:ladd:0")
        self.assertEqual(list(self.items().values()), [2])

        text, keyboard = self.sale_screen()
        # Paid at once (PQ-054): the cashier presses cash or card; the button
        # carries the amount shown.
        self.assertIn("staffsale:pay:cash:700.00", _data(keyboard))
        self.assertIn("staffsale:pay:terminal:700.00", _data(keyboard))
        callback = self.press(staff.staff_sale_pay_handler, "staffsale:pay:terminal:700.00")
        sale = Order.objects.get(channel=Order.Channel.COUNTER)
        self.assertEqual(sale.total_amount, Decimal("700.00"))
        self.assertEqual(sale.items.get().item_name, "Сырники, 5 шт (Варенье)")
        self.assertEqual(Payment.objects.get().method, Payment.Method.TERMINAL)
        self.assertIn("700.00", callback.message.edit_text.call_args.args[0])

    def test_a_changed_amount_is_not_charged(self):
        self.press(staff.staff_sale_item_quantity_handler, "staffsale:add:" + str(
            MenuItem.objects.get(public_id="FLOW0002").id))
        callback = self.press(staff.staff_sale_pay_handler, "staffsale:pay:cash:1.00")
        self.assertTrue(callback.answer.call_args.kwargs.get("show_alert"))
        self.assertFalse(Order.objects.filter(channel=Order.Channel.COUNTER).exists())

    def test_the_old_confirm_button_asks_how_the_guest_pays(self):
        self.press(staff.staff_sale_item_quantity_handler, "staffsale:add:" + str(
            MenuItem.objects.get(public_id="FLOW0002").id))
        callback = self.press(staff.staff_sale_confirm_handler, "staffsale:confirm")
        self.assertIn("наличными или картой", callback.answer.call_args.args[0])
        self.assertFalse(Order.objects.filter(channel=Order.Channel.COUNTER).exists())

    def test_a_double_press_makes_one_sale(self):
        self.press(staff.staff_sale_item_quantity_handler, "staffsale:add:" + str(
            MenuItem.objects.get(public_id="FLOW0002").id))
        data = async_to_sync(self.state.get_data)()
        total = staff._compose_staff_sale_view(
            partner_id=self.partner.id, customer_code="", items_map=data["items"],
            selected_category_id=None,
        )
        pay = next(d for d in _data(total[1]) if d.startswith("staffsale:pay:cash:"))
        self.press(staff.staff_sale_pay_handler, pay)
        # The second tap came with the old draft before the state was cleared.
        async_to_sync(self.state.set_state)(StaffQuickSaleStates.browsing_menu)
        async_to_sync(self.state.set_data)(data)
        self.press(staff.staff_sale_pay_handler, pay)
        self.assertEqual(Order.objects.filter(channel=Order.Channel.COUNTER).count(), 1)

    def test_a_choice_without_the_required_sauce_is_not_added(self):
        callback = self.press(staff.staff_sale_choice_add_handler, "sa:FLOW0001:0:0")
        self.assertIn("Соус", callback.answer.call_args.args[0])
        self.assertEqual(self.items(), {})
        self.assertFalse(Order.objects.filter(channel=Order.Channel.COUNTER).exists())

    def test_a_draft_line_number_out_of_range_changes_nothing(self):
        callback = self.press(staff.staff_sale_line_quantity_handler, "staffsale:lsub:3")
        self.assertTrue(callback.answer.call_args.kwargs.get("show_alert"))
        self.assertEqual(self.items(), {})
