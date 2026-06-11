from aiogram.fsm.state import State, StatesGroup


class StaffQuickSaleStates(StatesGroup):
    waiting_for_customer_code = State()
    browsing_menu = State()
    waiting_for_comment = State()
