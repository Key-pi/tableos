from collections.abc import Sequence

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.handlers.billing import router as billing_router
from bot.handlers.guest_call import router as guest_call_router
from bot.handlers.menu import router as menu_router
from bot.handlers.order import router as order_router
from bot.handlers.profile import router as profile_router
from bot.handlers.session import router as session_router
from bot.handlers.staff import router as staff_router
from bot.handlers.start import router as start_router
from bot.services.runtime import BotRuntimeConfig, register_runtime_configs


def create_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.include_router(start_router)
    dispatcher.include_router(session_router)
    dispatcher.include_router(menu_router)
    dispatcher.include_router(order_router)
    dispatcher.include_router(profile_router)
    dispatcher.include_router(billing_router)
    dispatcher.include_router(guest_call_router)
    dispatcher.include_router(staff_router)
    return dispatcher


async def run_polling(configs: Sequence[BotRuntimeConfig]) -> None:
    register_runtime_configs(list(configs))
    dispatcher = create_dispatcher()
    bots = [
        Bot(
            token=config.token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        for config in configs
    ]
    try:
        await dispatcher.start_polling(
            *bots,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )
    finally:
        for bot in bots:
            await bot.session.close()
