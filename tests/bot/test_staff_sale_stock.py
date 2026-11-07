"""The quick sale shows the staff how many are left (catalog step 3, owner
2026-11-07); the till may sell below zero, so nothing is refused."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from asgiref.sync import async_to_sync

from apps.menu.models import MenuItem, MenuItemVariant
from apps.partners.models import PartnerBotSettings
from bot.handlers import staff
from bot.states.staff_sale import StaffQuickSaleStates
from tests.apps.menu.test_options import options_module
from tests.apps.menu.test_stock import stock_module
from tests.bot.test_staff_flow import OWNER_TG, StaffFlowFixture, _buttons


class StaffSaleStockTests(StaffFlowFixture):
    def setUp(self):
        super().setUp()
        PartnerBotSettings.objects.filter(partner=self.partner).update(
            module_quick_sale_enabled=True
        )
        stock_module(self.partner)
        options_module(self.partner)
        self.item = MenuItem.objects.get(public_id="FLOW0001")  # Сырники 220.00
        self.item.track_stock = True
        self.item.stock_quantity = 3
        self.item.save()
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
            "redeem_bonus": False,
        })

    def item_buttons(self):
        _text, keyboard, _category = staff._compose_staff_sale_view(
            partner_id=self.partner.id, customer_code="", items_map={},
            selected_category_id=str(self.item.category_id),
        )
        return [button.text for button in _buttons(keyboard) if button.text.startswith("➕")]

    def test_the_item_shows_what_is_left(self):
        self.assertIn("➕ Сырники · ост. 3 — 220.00 грн", self.item_buttons())

    def test_switched_off_nothing_is_shown(self):
        stock_module(self.partner, on=False)
        self.assertIn("➕ Сырники — 220.00 грн", self.item_buttons())

    def test_variants_show_theirs_and_none_is_refused(self):
        MenuItemVariant.objects.create(
            item=self.item, name="3 шт", price="220.00", sort_order=1, stock_quantity=0
        )
        MenuItemVariant.objects.create(
            item=self.item, name="5 шт", price="330.00", sort_order=2, stock_quantity=4
        )
        callback = self._staff_callback("so:FLOW0001")
        async_to_sync(staff.staff_sale_choice_handler)(callback, self.bot, self.state)
        _text, picker = self._screen(callback)
        texts = [button.text for button in _buttons(picker)]
        self.assertIn("✅ 3 шт · 220.00 · ост. 0", texts)
        self.assertIn("○ 5 шт · 330.00 · ост. 4", texts)
