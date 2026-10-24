from aiogram.filters import BaseFilter
from aiogram.types import Message

from apps.partners.bot_texts import BUTTON_TEXTS
from core.i18n import variants


class PartnerButtonFilter(BaseFilter):
    """Match a reply-keyboard button by its text in any language (PQ-040).

    The owner's own button texts of this update's venue are included
    (``core.i18n.texts``), so a keyboard sent before the guest switched the
    language still works.
    """

    def __init__(self, label_attr: str) -> None:
        self.label_attr = label_attr
        self.msgid = BUTTON_TEXTS[label_attr]

    async def __call__(self, message: Message) -> bool:
        if not message.text:
            return False
        return message.text in variants(self.msgid)
