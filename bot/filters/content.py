from aiogram import Bot
from aiogram.filters import BaseFilter
from aiogram.types import Message
from asgiref.sync import sync_to_async

from bot.services.content import BotContent
from bot.services.context import resolve_partner_for_bot_token


class PartnerButtonFilter(BaseFilter):
    """Match reply-keyboard buttons using partner-specific labels."""

    def __init__(self, label_attr: str) -> None:
        self.label_attr = label_attr

    async def __call__(self, message: Message, bot: Bot) -> bool:
        if not message.text:
            return False
        partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
        content = await sync_to_async(BotContent.for_partner)(partner)
        return message.text == getattr(content, self.label_attr)
